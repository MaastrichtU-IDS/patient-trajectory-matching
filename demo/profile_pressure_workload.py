"""Measure a fixed configured workload in a separate process using Linux /proc RSS."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT/'demo/benchmark_configured_pressure.py'
PROFILE = 'configured-pressure-workload-memory-1.0'
MAX_REPORT_BYTES = 4*1024*1024


class ProfileError(ValueError):
    pass


def options(interval, timeout):
    if not math.isfinite(interval) or not .005 <= interval <= 1:
        raise ProfileError('INVALID_SAMPLE_INTERVAL')
    if not math.isfinite(timeout) or not 1 <= timeout <= 86400:
        raise ProfileError('INVALID_TIMEOUT')


def proc_record(raw):
    """Linux stat: comm can contain spaces and parentheses; RSS is field 24."""
    fields = raw[raw.rindex(')')+2:].split()
    return {'parent':int(fields[1]),'session':int(fields[3]),
            'start':int(fields[19]),'rss_pages':max(0,int(fields[21]))}


def sample_tree(root_pid, proc=Path('/proc')):
    records = {}; failed = 0
    for path in proc.iterdir():
        if not path.name.isdecimal(): continue
        try: records[int(path.name)] = proc_record((path/'stat').read_text())
        except (OSError, ValueError, IndexError): failed += 1
    selected = {pid for pid,row in records.items() if pid == root_pid or row['session'] == root_pid}
    # Include descendants that create another session while their parent is visible.
    while True:
        descendants = {pid for pid,row in records.items() if row['parent'] in selected}
        if descendants <= selected: break
        selected |= descendants
    page = os.sysconf('SC_PAGE_SIZE')
    return {'rss_bytes':sum(records[pid]['rss_pages']*page for pid in selected),
            'processes':len(selected),'root_observed':root_pid in records,
            'identities':{(pid,records[pid]['start']) for pid in selected},
            'unreadable_or_exited_process_records':failed}


def stop_child(child):
    """The fixed runner owns a new session, including ordinary subprocess descendants."""
    try: os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError: pass
    child.wait()


def monitor(child, interval, timeout, clock=time.monotonic):
    started = clock(); samples = []; identities = set(); timed_out = False
    failure = None
    try:
        while True:
            before = clock()
            if before-started >= timeout:
                timed_out = True; break
            try: row = sample_tree(child.pid)
            except OSError:
                failure = 'PROC_SAMPLING_FAILED'; break
            row['offset_seconds'] = before-started
            row['scan_seconds'] = clock()-before
            identities |= row.pop('identities'); samples.append(row)
            if child.poll() is not None: break
            time.sleep(min(interval,max(0,timeout-(clock()-started))))
    finally:
        # Also remove same-session descendants after normal root exit.
        stop_child(child)
    elapsed = clock()-started
    gaps = [b['offset_seconds']-a['offset_seconds'] for a,b in zip(samples,samples[1:])]
    observed = [row for row in samples if row['processes']]
    return {'elapsed_seconds':round(elapsed,6),'timed_out':timed_out,'sampling_failure':failure,
        'child_exit_code':child.returncode,'memory':{
            'method':'linux_proc_sequential_process_tree_rss_samples',
            'sample_interval_requested_seconds':interval,'samples':len(samples),
            'samples_with_processes':len(observed),'samples_with_resident_memory':sum(row['rss_bytes']>0 for row in samples),
            'samples_with_root':sum(row['root_observed'] for row in samples),
            'maximum_sampled_aggregate_rss_bytes':max((row['rss_bytes'] for row in observed),default=None),
            'maximum_processes_observed_together':max((row['processes'] for row in samples),default=0),
            'distinct_process_instances_observed':len(identities),
            'first_sample_offset_seconds':samples[0]['offset_seconds'] if samples else None,
            'last_sample_offset_seconds':samples[-1]['offset_seconds'] if samples else None,
            'maximum_sample_gap_seconds':max(gaps,default=None),
            'maximum_scan_seconds':max((row['scan_seconds'] for row in samples),default=None),
            'unreadable_or_exited_process_records':sum(row['unreadable_or_exited_process_records'] for row in samples),
            'coverage_complete':False,
            'limitations':[
                'Sequential /proc reads are not an atomic snapshot or an exact simultaneous memory peak.',
                'Short-lived processes and between-sample allocation peaks can be missed.',
                'RSS counts shared pages in each process; this is not unique physical memory or PSS.',
                'Includes the isolated runner, HTTP service, fresh references and visible descendants; excludes the profiling parent.',
                'Reparented descendants remain covered if in the runner session; detached and reparented descendants can be missed.',
                'No per-process high-water marks are summed; serialized cache bytes are not used as RSS.',
                'Unreadable/exited record count includes unrelated processes encountered during the system-wide scan.']}}


def protected_output(output, inputs, workload):
    target = workload.protect_output(output,inputs)
    if target == Path(__file__).resolve(): raise ProfileError('OUTPUT_OVERWRITES_INPUT')
    return target


def child_summary(path, expected_context):
    with path.open('rb') as handle: raw = handle.read(MAX_REPORT_BYTES+1)
    if len(raw)>MAX_REPORT_BYTES: raise ProfileError('CHILD_REPORT_TOO_LARGE')
    report = json.loads(raw)
    if not isinstance(report,dict): raise ProfileError('INVALID_CHILD_REPORT')
    if report.get('profile') != 'configured-pressure-workload-verification-1.0':
        raise ProfileError('INVALID_CHILD_REPORT')
    if report.get('context_id') != expected_context: raise ProfileError('CHILD_CONTEXT_MISMATCH')
    if report.get('status') != 'VERIFIED': return {'status':'FAILED','report_sha256':hashlib.sha256(raw).hexdigest()}
    trials = report.get('trials',[])
    if not trials or not all(row.get('all_result_fields_except_duration_equal') is True for row in trials):
        raise ProfileError('INVALID_CHILD_REPORT')
    # Export only strict aggregate fields, never arbitrary labels, source rows or failure text.
    inspections = sum(row['http_inspections_equal'] for row in trials)
    if type(inspections) is not int or inspections < 0: raise ProfileError('INVALID_CHILD_REPORT')
    source = report.get('source_sha256',{})
    if set(source) != {'inputevents','chartevents','icustays','d_items'} or not all(
            isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
            for value in source.values()):
        raise ProfileError('INVALID_CHILD_REPORT')
    return {'status':'VERIFIED','report_sha256':hashlib.sha256(raw).hexdigest(),
            'verified_trials':len(trials),'http_inspections_equal':inspections,'source_sha256':source}


def run(inputs, interval=.02, timeout=3600):
    import benchmark_configured_pressure as workload
    options(interval,timeout)
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    base = {'profile':PROFILE,'status':'FAILED','workload_context_id':inputs['context_id'],
        'configuration_context_id':inputs['context']['configuration_context_id'],
        'workload_sha256':inputs['context']['workload_sha256'],
        'repetitions':inputs['context']['repetitions'],
        'artifacts':{**inputs['context']['artifacts'],'demo/profile_pressure_workload.py':own_hash},
        'python':platform.python_version(),'platform':platform.system(),
        'source_scope':'supplied_records_not_publisher_authenticated',
        'clinical_mapping_verified':False,'representative_clinical_scale_established':False,
        'patient_rows_or_identifiers_included':False,'child_raw_output_included':False,
        'measurement_scope':'entire isolated verification runner, including startup and fresh references',
        'child_verification':{'status':'NOT_RUN'}}
    if platform.system() != 'Linux' or not Path('/proc/self/stat').is_file():
        return {**base,'failure_code':'LINUX_PROC_REQUIRED'}
    try:
        workload.check_inputs(inputs)
        with tempfile.TemporaryDirectory(prefix='pressure-memory-') as directory:
            output = Path(directory)/'child-report.json'
            command = [sys.executable,str(RUNNER),'--config',str(inputs['snapshot']['path']),
                '--workload',str(inputs['workload_path']),'--repetitions',str(inputs['context']['repetitions']),
                '--output',str(output)]
            child = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,start_new_session=True,cwd=ROOT)
            result = monitor(child,interval,timeout); base.update(result)
            if result['timed_out']: return {**base,'failure_code':'WORKLOAD_TIMEOUT'}
            if result['sampling_failure']: return {**base,'failure_code':result['sampling_failure']}
            if output.is_file(): base['child_verification'] = child_summary(output,inputs['context_id'])
            if result['child_exit_code'] != 0 or base['child_verification']['status'] != 'VERIFIED':
                return {**base,'failure_code':'CHILD_WORKLOAD_FAILED'}
            workload.check_inputs(inputs)
            if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != own_hash:
                raise ProfileError('PROFILER_IMPLEMENTATION_CHANGED')
            if not result['memory']['samples_with_root'] or not result['memory']['samples_with_resident_memory']:
                return {**base,'failure_code':'NO_ROOT_MEMORY_SAMPLES'}
            return {**base,'status':'MEASURED'}
    except Exception:
        return {**base,'failure_code':'PROFILE_EXECUTION_FAILED'}


def main(argv=None):
    import benchmark_configured_pressure as workload
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--workload',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--interval',type=float,default=.02)
    parser.add_argument('--timeout',type=float,default=3600)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        options(args.interval,args.timeout)
        inputs = workload.load_inputs(args.config,args.workload,args.repetitions)
        output = protected_output(args.output,inputs,workload)
    except (ValueError,OSError):
        print(json.dumps({'status':'FAILED','failure_code':'PROFILE_INPUT_REJECTED'})); return 1
    report = run(inputs,args.interval,args.timeout)
    try:
        protected_output(output,inputs,workload); workload.write_report(output,report)
    except (ValueError,OSError):
        print(json.dumps({'status':'FAILED','failure_code':'PROFILE_OUTPUT_REJECTED'})); return 1
    print(json.dumps({'status':report['status'],'workload_context_id':report['workload_context_id'],
        'child_verification_status':report['child_verification']['status'],
        **({'failure_code':report['failure_code']} if 'failure_code' in report else {})}))
    return 0 if report['status']=='MEASURED' else 1


if __name__ == '__main__': raise SystemExit(main())
