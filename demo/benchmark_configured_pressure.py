"""Verify and measure bounded workloads through the configured local pressure service."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.request import Request, urlopen
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import serve
from serve_mapped_pressure import Handler
from mapped_pressure import MappedPressureService, stamp
from patterns import pressure_service_config as configuration, mapped_pressure_session as mapped

PROFILE = 'configured-pressure-workload-verification-1.0'
FILES = tuple(sorted(set(mapped.FILES + tuple(stamp()) +
    ('demo/benchmark_configured_pressure.py','demo/serve.py','schemas/pressure-workload.schema.json'))))
JOB_TIMEOUT = 600


class WorkloadError(ValueError):
    def __init__(self,code,stage='input'):
        self.code,self.stage = code,stage
        super().__init__(code)


def require(condition,code):
    if not condition: raise WorkloadError(code)


def artifacts():
    return {name:mapped.source.inputs.source.digest((ROOT/name).read_bytes()) for name in FILES}


def load_inputs(config_path,workload_path,repetitions=3):
    require(type(repetitions) is int and 1 <= repetitions <= 10,'INVALID_REPETITIONS')
    snapshot = configuration.load(config_path)
    path = Path(workload_path).resolve(); workload, digest = configuration.read_json(path,64*1024)
    schema = json.loads((ROOT/'schemas/pressure-workload.schema.json').read_text())
    require(not list(Draft202012Validator(schema).iter_errors(workload)),'INVALID_PRESSURE_WORKLOAD')
    for query in workload['queries']:
        controls = mapped.pressure.controls(query)
        require(all(controls[k] <= snapshot['defaults'][k] for k in ('baseline_minutes','followup_minutes')),
                'OUTSIDE_CONFIGURED_REVIEWED_WINDOWS')
    context = {'profile':PROFILE,'configuration_context_id':snapshot['context_id'],
        'configuration_sha256':snapshot['context']['configuration_sha256'], 'workload_sha256':digest,
        'queries':deepcopy(workload['queries']), 'repetitions':repetitions,'artifacts':artifacts()}
    return {'snapshot':snapshot,'workload_path':path,'context':context,'context_id':mapped.cr.digest(context)}


def check_inputs(inputs):
    configuration.check_current(inputs['snapshot'])
    require(configuration.read_json(inputs['workload_path'],64*1024)[1] == inputs['context']['workload_sha256'],
            'WORKLOAD_CHANGED')
    require(artifacts() == inputs['context']['artifacts'],'WORKLOAD_IMPLEMENTATION_CHANGED')


def protect_output(output,inputs):
    target = Path(output).resolve(); snapshot = inputs['snapshot']; paths = snapshot['paths']
    protected = {snapshot['path'],inputs['workload_path'],paths['pressure_request'],paths['source_review']}
    protected.update((ROOT/name).resolve() for name in FILES)
    require(target.suffix == '.json','OUTPUT_REQUIRES_JSON')
    require(target not in protected and not any(target.is_relative_to(paths[name]) for name in ('source_dir','mapping_dir')),
            'OUTPUT_OVERWRITES_INPUT')
    return target


def comparable(value):
    result = deepcopy(value); result.pop('elapsed_seconds',None); return result


def distribution(values):
    return {'samples':len(values),'minimum_seconds':min(values) if values else None,
            'median_seconds':statistics.median(values) if values else None,'maximum_seconds':max(values) if values else None}


def counted(action):
    targets = {'source_audits':(mapped.source,'audit_store'),
        'mapping_plans':(mapped.source.mappings,'plan'),
        'network_compilations':(mapped.source.mappings.mixed,'_compile'),
        'anchor_sql_checks':(mapped.pressure.p.reference,'execute')}
    with ExitStack() as stack:
        counters = {key:stack.enter_context(patch.object(obj,name,wraps=getattr(obj,name))) for key,(obj,name) in targets.items()}
        result = action()
        counts = {key:value.call_count for key,value in counters.items()}
    return result, counts


class QuietHandler(Handler):
    def log_message(self,*args): pass


def run(inputs):
    """Own the module-global HTTP service for this sequential, single-process experiment."""
    service = server = thread = None; previous = serve.PRESSURE; stage = 'startup'
    try:
        check_inputs(inputs)
        service = MappedPressureService(config=inputs['snapshot']['path'])
        require(service.configuration['context_id'] == inputs['snapshot']['context_id'],'CONFIGURATION_CHANGED_DURING_STARTUP')
        serve.PRESSURE = service
        server = serve.ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urlopen(base+path,timeout=30) as response: return json.load(response)
        def job(controls):
            start = time.monotonic()
            body = json.dumps({'stratum':'configured','controls':controls}).encode()
            request = Request(base+'/api/pressure/jobs',data=body,headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=30) as response: value = json.load(response)
            deadline = start+JOB_TIMEOUT
            while value['status']=='RUNNING' and time.monotonic()<deadline:
                time.sleep(.01); value = get('/api/pressure/jobs/'+value['id'])
            require(value['status']!='RUNNING','WORKLOAD_JOB_TIMEOUT')
            require(value['status']=='COMPLETED','WORKLOAD_JOB_INCOMPLETE')
            return value,service.jobs[value['id']]['_result'],round(time.monotonic()-start,6)
        stage = 'cold'
        (cold,cold_result,cold_http),cold_counts = counted(lambda:job(inputs['snapshot']['defaults']))
        session = service.session
        # Hashes describe exact supplied table bytes; no paths or source rows are exported.
        source_files = mapped.pressure.fingerprint(service.folder)
        cold_summary = {'service_seconds':cold['timing']['total_seconds'],'http_seconds':cold_http,
            'operation_counts':cold_counts,'metrics':cold_result['metrics']}
        rows = []
        for repetition in range(inputs['context']['repetitions']):
            for index,controls in enumerate(inputs['context']['queries']):
                stage = 'prepared'
                check_inputs(inputs)
                # Force a query evaluation while retaining the prepared batch cache.
                service.result_cache.clear()
                (warm,actual,warm_http),warm_counts = counted(lambda:job(controls))
                require(warm['execution']['mode']=='fresh_query','EXPECTED_QUERY_EVALUATION')
                require(warm_counts['anchor_sql_checks']==actual['anchors_verified'],'WARM_SQL_COVERAGE_DIFFERENCE')
                stage = 'repeat'
                (repeat,repeated,repeat_http),repeat_counts = counted(lambda:job(controls))
                cache_hit = repeat['execution']['mode']=='cached_complete_result'
                require(cache_hit == warm['execution']['retained_for_reuse'],'REPEAT_CACHE_MODE_DIFFERENCE')
                require(comparable(actual)==comparable(repeated),'REPEAT_RESULT_DIFFERENCE')
                require(repeat_counts['anchor_sql_checks']==(0 if cache_hit else repeated['anchors_verified']),
                        'REPEAT_SQL_COVERAGE_DIFFERENCE')
                stage = 'reference'
                service._check_implementation(); session.check_current()
                start = time.monotonic(); reference = session.execute(controls); fresh_seconds = round(time.monotonic()-start,6)
                require(reference['status']=='COMPLETED','FRESH_REFERENCE_INCOMPLETE')
                require(comparable(actual)==comparable(reference),'FRESH_RESULT_DIFFERENCE')
                stage = 'inspection'; inspected = 0
                require(len(actual['details'])==actual['anchors_verified'],'INSPECTION_COVERAGE_DIFFERENCE')
                for token in actual['details']:
                    expected = session.inspect(reference,token)
                    for completed in (warm,repeat):
                        evidence = get('/api/pressure/jobs/'+completed['id']+'/anchors/'+token)
                        require(evidence==expected,'HTTP_INSPECTION_DIFFERENCE')
                        require(evidence['sql_agreement'],'HTTP_SQL_AGREEMENT_MISSING')
                        inspected += 1
                check_inputs(inputs); service._check_implementation(); session.check_current()
                preparation = warm['execution']['batch_preparation']
                rows.append({'repetition':repetition+1,'query_index':index,'controls':deepcopy(controls),
                    'metrics':actual['metrics'],'anchors_verified':actual['anchors_verified'],
                    'http_inspections_equal':inspected,'all_result_fields_except_duration_equal':True,
                    'prepared_service_seconds':warm['timing']['total_seconds'],'prepared_http_seconds':warm_http,
                    'repeat_service_seconds':repeat['timing']['total_seconds'],'repeat_http_seconds':repeat_http,
                    'fresh_mapped_execution_seconds':fresh_seconds,
                    'repeat_execution_mode':repeat['execution']['mode'],
                    'prepared_operation_counts':warm_counts,'repeat_operation_counts':repeat_counts,
                    'reused_batches':preparation['reused_batches'],'fresh_batches':preparation['fresh_batches'],
                    'prepared_cache':{key:preparation['cache'][key] for key in
                        ('entries','serialized_bytes','max_entries','max_serialized_bytes','evicted')},
                    'result_cache_serialized_bytes':service.result_cache.bytes})
        stage = 'final_validation'
        check_inputs(inputs); service._check_implementation(); session.check_current()
        require(source_files==mapped.pressure.fingerprint(service.folder),'SOURCE_CHANGED')
        summary = []
        for index in range(len(inputs['context']['queries'])):
            group = [row for row in rows if row['query_index']==index]
            cached = [row for row in group if row['repeat_execution_mode']=='cached_complete_result']
            summary.append({'query_index':index,
                'prepared_service':distribution([row['prepared_service_seconds'] for row in group]),
                'prepared_http':distribution([row['prepared_http_seconds'] for row in group]),
                'cached_service':distribution([row['repeat_service_seconds'] for row in cached]),
                'cached_http':distribution([row['repeat_http_seconds'] for row in cached]),
                'fresh_mapped_execution':distribution([row['fresh_mapped_execution_seconds'] for row in group]),
                'repeat_recomputations':len(group)-len(cached)})
        return {'profile':PROFILE,'status':'VERIFIED','context':deepcopy(inputs['context']),'context_id':inputs['context_id'],
            'session_context_id':session.id,'source_sha256':source_files,
            'source_scope':'supplied_records_not_publisher_authenticated',
            'clinical_mapping_verified':False,'patient_rows_or_identifiers_included':False,
            'python':platform.python_version(),'platform':platform.platform(),
            'stays_retained':len(cold_result['roster']),'anchors':len(cold_result['anchors']),
            'batches':len(session.built),'nonempty_measurement_batches':sum(b['measurement_store'] is not None for b in session.built),
            'cold':cold_summary,'trials':rows,'summary':summary,
            'method':{'order':'one cold default job; repeated query-order trials: prepared, identical repeat, fresh reference, both HTTP inspections',
                'complete_result_cache_cleared_before_each_prepared_trial':True,
                'fresh_reference':'direct mapped session execution after initial source-fidelity preparation',
                'operation_counters':'instrumented prepared/repeat service jobs; inspection and fresh-reference work excluded',
                'http_poll_interval_seconds':0.01,'memory_measure':'serialized cache payload at trial end, not peak RSS',
                'timing_claim':'local sequential observations; no randomized comparison or production latency guarantee'}}
    except WorkloadError as error:
        error.stage = stage; raise
    except Exception as error:
        # Keep record values, reviewer text and local paths out of aggregate failures.
        raise WorkloadError('WORKLOAD_EXECUTION_FAILED',stage) from error
    finally:
        if server:
            if thread and thread.is_alive(): server.shutdown(); thread.join(timeout=5)
            server.server_close()
        if service: service.close()
        serve.PRESSURE = previous


def write_report(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
            temporary = Path(handle.name); json.dump(value,handle,indent=2); handle.write('\n')
        temporary.replace(path)
    finally:
        if temporary: temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--workload',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        inputs = load_inputs(args.config,args.workload,args.repetitions)
        output = protect_output(args.output,inputs)
    except (ValueError,OSError) as error:
        code = error.code if isinstance(error,WorkloadError) else 'WORKLOAD_INPUT_REJECTED'
        print(json.dumps({'status':'FAILED','stage':'input','code':code})); return 1
    try: report = run(inputs)
    except WorkloadError as error:
        report = {'profile':PROFILE,'status':'FAILED','context_id':inputs['context_id'],
                  'failure':{'stage':error.stage,'code':error.code},'completed_workload_metrics_available':False}
    # Recheck output protection before publication; a changed config never supplies new paths.
    protect_output(output,inputs); write_report(output,report)
    print(json.dumps({'status':report['status'],'context_id':report.get('context_id'),
                     'verified_trials':len(report.get('trials',[])),**({'failure':report['failure']} if 'failure' in report else {})}))
    return 0 if report['status']=='VERIFIED' else 1


if __name__ == '__main__': raise SystemExit(main())
