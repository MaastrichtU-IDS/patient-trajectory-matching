"""Measure the authored mapped HTTP route and compare warm results with fresh mapped execution."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import serve
from mapped_pressure import MappedPressureService, stamp
from patterns import mapped_pressure_session as mapped

DEFAULT = {'threshold':'65', 'baseline_minutes':30, 'followup_minutes':120}
CHANGED = {'threshold':'59', 'baseline_minutes':25, 'followup_minutes':60}
FILES = tuple(sorted(set(mapped.FILES + tuple(stamp()) + ('demo/benchmark_mapped_pressure.py',
    'demo/serve.py', 'demo/pressure.js', 'demo/pressure-synthetic-review.json'))))


def artifacts(): return {f:mapped.ei.digest((ROOT/f).read_text()) for f in FILES}


def comparable(value):
    result = deepcopy(value); result.pop('elapsed_seconds'); return result


def benchmark():
    service = MappedPressureService(); previous = serve.PRESSURE; serve.PRESSURE = service
    server = serve.ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def get(path):
        with urlopen(base+path,timeout=30) as response: return json.load(response)
    def run(controls):
        body = json.dumps({'stratum':'synthetic','controls':controls}).encode()
        with urlopen(Request(base+'/api/pressure/jobs',data=body,headers={'Content-Type':'application/json'}),timeout=30) as response:
            job = json.load(response)
        deadline = time.monotonic()+120
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.05); job = get('/api/pressure/jobs/'+job['id'])
        mapped.ei.require(job['status'] == 'COMPLETED', 'MAPPED_HTTP_JOB_FAILED:'+job.get('error','timeout'))
        return job, service.jobs[job['id']]['_result']
    try:
        before = artifacts(); cold, original = run(DEFAULT)
        with patch.object(mapped.source,'audit_store',wraps=mapped.source.audit_store) as audit, \
             patch.object(mapped.source.mappings,'plan',wraps=mapped.source.mappings.plan) as plan, \
             patch.object(mapped.source.mappings.mixed,'_compile',wraps=mapped.source.mappings.mixed._compile) as compile_network, \
             patch.object(mapped.pressure.p.reference,'execute',wraps=mapped.pressure.p.reference.execute) as sql:
            changed, actual = run(CHANGED)
            work = {'source_audits':audit.call_count, 'mapping_plans':plan.call_count,
                    'network_compilations':compile_network.call_count, 'anchor_sql_checks':sql.call_count}
        repeated, cached = run(CHANGED)
        mapped.ei.require(comparable(cached)==comparable(actual),'MAPPED_COMPLETE_CACHE_DIFFERENCE')
        start = time.monotonic(); reference = service.session.execute(CHANGED); fresh_seconds = time.monotonic()-start
        mapped.ei.require(comparable(actual)==comparable(reference),'MAPPED_PREPARED_RESULT_DIFFERENCE')
        inspected = 0
        for token in actual['details']:
            evidence = get('/api/pressure/jobs/'+changed['id']+'/anchors/'+token)
            mapped.ei.require(evidence==service.session.inspect(reference,token),'MAPPED_HTTP_INSPECTION_DIFFERENCE')
            mapped.ei.require(evidence['sql_agreement'] and evidence['measurement_mapping']['selection_plan']['semantic_run']['status']=='READY',
                              'MAPPED_HTTP_EVIDENCE_MISSING')
            inspected += 1
        mapped.ei.require(work=={'source_audits':0,'mapping_plans':0,'network_compilations':0,'anchor_sql_checks':3},'MAPPED_WARM_WORK_DIFFERENCE')
        mapped.ei.require(changed['execution']['batch_preparation']['reused_batches']==3 and
                          changed['execution']['batch_preparation']['fresh_batches']==0,'MAPPED_BATCH_REUSE_FAILED')
        mapped.ei.require(repeated['execution']['mode']=='cached_complete_result','MAPPED_COMPLETE_CACHE_MISS')
        mapped.ei.require(before==artifacts(),'MAPPED_BENCHMARK_IMPLEMENTATION_CHANGED')
        return {'profile':'mapped-pressure-http-verification-1.0','status':'VERIFIED',
                'synthetic_only':True,'clinical_mapping_verified':False,'public_demo_mapping_executed':False,
                'artifacts':before,'python':platform.python_version(),'platform':platform.platform(),
                'session_context_id':service.session.id,'mapping_files':service.session.context['mapping_files'],
                'original_controls':DEFAULT,'changed_controls':CHANGED,'original_metrics':original['metrics'],
                'changed_metrics':actual['metrics'],'anchors_verified':actual['anchors_verified'],
                'stays_retained':len(actual['roster']),'http_anchor_inspections_equal':inspected,
                'all_result_fields_except_duration_equal':True,'warm_operation_counts':work,
                'cold_timing':cold['timing'],'changed_timing':changed['timing'],
                'repeated_timing':repeated['timing'],'fresh_mapped_session_execution_seconds':round(fresh_seconds,6),
                'cold_preparation':cold['execution']['batch_preparation'],
                'changed_preparation':changed['execution']['batch_preparation'],
                'repeated_execution_mode':repeated['execution']['mode'],
                'patient_rows_or_identifiers_included':False,'visual_browser_inspection_verified':False,
                'measurement_note':'Single local synthetic sequence; fresh baseline is a direct mapped session execution; no controlled latency guarantee'}
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close(); service.close(); serve.PRESSURE = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'verification/mapped-pressure-synthetic-report.json')
    args = parser.parse_args(argv)
    protected = {(ROOT/f).resolve() for f in FILES}
    protected.update(p.resolve() for folder in ('examples/measurement-source-catalogue','examples/source-mixed-query') for p in (ROOT/folder).glob('*'))
    mapped.ei.require(args.output.resolve() not in protected,'OUTPUT_OVERWRITES_INPUT')
    report = benchmark(); args.output.parent.mkdir(parents=True,exist_ok=True); temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=args.output.parent,delete=False) as handle:
            temporary = Path(handle.name); json.dump(report,handle,indent=2); handle.write('\n')
        temporary.replace(args.output)
    finally:
        if temporary: temporary.unlink(missing_ok=True)
    print(json.dumps({'status':report['status'],'changed_timing':report['changed_timing'],
                      'fresh_mapped_session_execution_seconds':report['fresh_mapped_session_execution_seconds']}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
