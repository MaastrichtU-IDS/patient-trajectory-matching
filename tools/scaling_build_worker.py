#!/usr/bin/env python3
"""The scaling benchmark's build phase (D34): cohort construction, the build worker, and the code that runs and
measures a build.

`tools/benchmark_execution_store.py` imports these and runs this script, in Python's isolated mode, for each size's
build. The process loads only what a build needs: this script, `patterns.execution_store`, the converter, the decimal
rule, the patient's times (`patterns.trajectory_times`, never the engine's matching code) and the resource locator.
At its end it records every repository file it loaded or read, by whole-file digest, with the library versions
(`patterns/build_record.py`); the benchmark puts that record in its report (`build_provenance`). The code that runs
and measures a build from the parent (`run_phase`, `current_rss`, `limit_bytes`, `build_size`) lives here too, so
the record's digest of this file covers it.

Cohorts. Distinct patients come from the synthetic generator with one seed per 48 patients, relabelled per seed so ids
are unique, up to DISTINCT_CAP. Beyond that the distinct pool is replicated: copy k of a pool patient gets the id
`<id>~r<k>` and every time shifted by a whole number of days that depends only on k. The build streams: the distinct
pool is generated one seed at a time and written to an ndjson file, which the build reads back a line at a time,
replicating as it goes, into `execution_store.StoreBuilder`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The data files a build may read, each bound by whole digest in the record: the synthetic generator (loaded as a
# module) and the manifest schema (read when the snapshot is validated). A build that opens any other repository file
# stops (`build_record`, UNRECORDED_FILE_READ).
BUILD_FILES = ('demos/mockups/synthetic/generate.py', 'schemas/execution-store-manifest.schema.json')


GENERATOR = ROOT / 'demos/mockups/synthetic/generate.py'
PER_COHORT = 48
DISTINCT_CAP = 10_000
SEED_BASE = 20261103  # the generator's default seed, so the first cohort is the committed one
DAY = 86_400_000_000
RSS_LIMIT_BYTES = 40 * 2**30
DEFAULT_MEMORY_LIMIT = '4GB'
# Resident memory is sampled every this many patients while they are added.
ADD_SAMPLE_EVERY = 1000
def pct(values, q):
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def shift_for(k):
    """Whole-day shift of copy k, positive and depending on k alone."""
    return (1 + (k * 37) % 700) * DAY


# D34: the digests of the pinned files' bytes as executed in this process (the generator).
EXECUTED = {}


def load_generator():
    """The synthetic generator, executed from the bytes read here, whose digest `EXECUTED` keeps."""
    import hashlib
    spec = importlib.util.spec_from_file_location('benchmark_generate', GENERATOR)
    module = importlib.util.module_from_spec(spec)
    source = Path(GENERATOR).read_bytes()
    EXECUTED[Path(GENERATOR).resolve().relative_to(ROOT.resolve()).as_posix()] = hashlib.sha256(source).hexdigest()
    exec(compile(source, str(GENERATOR), 'exec'), module.__dict__)
    return module


def generate_pool(size, sink, generator=None):
    """Generate min(size, DISTINCT_CAP) distinct patients one generator seed at a time, passing each
    converted, relabelled patient to `sink`. Returns (header, provenance, generation seconds, conversion
    seconds, cohorts, seeds the generator failed on and that were skipped, events per patient)."""
    from patterns import execution_store as es
    from patterns import synthetic_ingest as si
    generator = generator or load_generator()
    want = min(size, DISTINCT_CAP)
    cohorts = math.ceil(want / PER_COHORT)
    generating = converting = 0.0
    header = provenance = None
    failed, skipped, events, offset, index, written = [], {}, [], 0, 0, 0
    while index < cohorts:
        seed = SEED_BASE + offset
        offset += 1
        started = time.perf_counter()
        try:
            cohort = json.loads(json.dumps(generator.serialise(generator.build(seed), seed), ensure_ascii=False))
        except Exception as exc:  # the generator cannot build every seed; such a seed is skipped and counted
            failed.append({'seed': seed, 'error': type(exc).__name__})
            generating += time.perf_counter() - started
            continue
        middle = time.perf_counter()
        generating += middle - started
        own, why = si.synthetic_header(cohort), {}
        patients = list(si.iter_patients(cohort, why))  # one cohort of 48 at a time
        try:  # a cohort the store would refuse (a generator defect) is skipped too
            for p in patients:
                es.validate_patient(p, own['closure']['ancestors'])
        except es.IngestError as exc:
            failed.append({'seed': seed, 'error': exc.code})
            converting += time.perf_counter() - middle
            continue
        if header is None:
            header, provenance = own, si.synthetic_provenance(cohort, {})
        elif own['closure'] != header['closure']:
            raise ValueError('the cohorts disagree on the concept closure')
        for k, v in why.items():
            skipped[k] = skipped.get(k, 0) + v
        converting += time.perf_counter() - middle
        for p in patients:
            if written == want:
                break
            p['patient_id'] = f"{index:04d}-{p['patient_id']}"
            p['clock_id'] = f"patient_{p['patient_id']}"
            events.append(len(p['events']))
            sink(p)
            written += 1
        index += 1
    header = {**header, 'dataset_id': f'scaling-{size}'}
    provenance = {**provenance, 'skipped': dict(sorted(skipped.items()))}
    return header, provenance, generating, converting, cohorts, failed, events


def pool_ingest(size, generator=None):
    """(ingest document of min(size, DISTINCT_CAP) distinct patients, generation seconds, conversion seconds,
    cohorts, failed seeds), in memory: for the query anchors and the exhaustive check at small sizes."""
    patients = []
    header, provenance, generating, converting, cohorts, failed, _ = generate_pool(size, patients.append, generator)
    return {**header, 'provenance': provenance, 'patients': patients}, generating, converting, cohorts, failed


def write_pool(size, path):
    """The distinct pool as ndjson at `path`, one patient per line; the rest of generate_pool's result."""
    with open(path, 'w', encoding='utf-8') as out:
        def sink(p):
            out.write(json.dumps(p, ensure_ascii=False, separators=(',', ':')) + '\n')
        return generate_pool(size, sink)


def stream_patients(path, pool_size, size):
    """`size` patients read back from the pool file a line at a time: the pool, then copy 1, 2, ... of it."""
    produced = copy = 0
    while produced < size:
        with open(path, encoding='utf-8') as lines:
            for line in lines:
                if produced == size:
                    return
                patient = json.loads(line)
                yield patient if copy == 0 else shifted(patient, copy)
                produced += 1
        copy += 1
        if copy * pool_size != produced:
            raise ValueError('the pool file changed while it was read')


def shifted(patient, k):
    """Copy k of a pool patient: new id, whole-day shift of every time (record times included), evidence shared
    with the source object."""
    delta = shift_for(k)
    pid = f"{patient['patient_id']}~r{k}"
    def move(pair):
        return None if pair is None else [None if t is None else t + delta for t in pair]
    events = []
    for e in patient['events']:
        copy = {**e, 'start': move(e['start'])}
        if e.get('end') is not None:
            copy['end'] = move(e['end'])
        for field in ('recorded_at', 'available_at'):  # record times are on the patient's clock too (D26)
            if field in e:
                copy[field] = e[field] + delta
        events.append(copy)
    coverage = [{**c, 'lo': c['lo'] + delta, 'hi': c['hi'] + delta} for c in patient['coverage']]
    out = {**patient, 'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': events, 'coverage': coverage}
    if 'excluded' in patient:
        out['excluded'] = [{**x, 'start': move(x['start'])} for x in patient['excluded']]
    return out


def replicate(ingest, size):
    """The pool repeated up to `size` patients (the pool itself when it already has that many)."""
    pool = ingest['patients']
    if size <= len(pool):
        return ingest
    patients = list(pool)
    for i in range(len(pool), size):
        patients.append(shifted(pool[i % len(pool)], i // len(pool)))
    return {**ingest, 'patients': patients}


def directory_bytes(path):
    return sum(f.stat().st_size for f in Path(path).rglob('*') if f.is_file())


def progress(path, phase, **extra):
    """Say which phase the worker is in and its peak memory so far, so a killed worker can be placed."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    Path(path).write_text(json.dumps({'phase': phase, 'peak_rss_bytes_so_far': peak, **extra}))


def build_worker(size, work, threads, memory_limit, marker, staging=None):
    """Generate the distinct pool to disk, then stream every patient into `StoreBuilder`."""
    from patterns import execution_store as es
    work = Path(work)
    progress(marker, 'generate_and_convert_distinct_pool')
    pool_path = work / 'pool.ndjson'
    header, provenance, generating, converting, cohorts, failed, per_patient = write_pool(size, pool_path)
    per_patient.sort()
    stages = []
    started = time.perf_counter()
    def mark(name, info=None):
        row = {'stage': name, 'seconds': time.perf_counter() - started, 'rss_bytes': rss_now(),
               'peak_rss_bytes': peak_now(), **(info or {})}
        stages.append(row)
        progress(marker, 'stage:' + name, peak_rss_bytes_so_far=row['peak_rss_bytes'], stages=stages)
    mark('pool_on_disk', {'pool_bytes': pool_path.stat().st_size, 'distinct_patients': len(per_patient)})
    started = time.perf_counter()
    adding, add_rss = 0.0, []
    root = work / 'store'
    # `staging` (D26) only where asked, so that an older builder without the option can run this worker too.
    options = {} if staging is None else {'staging': staging}
    with es.StoreBuilder(root, header, threads=threads, memory_limit=memory_limit, on_stage=mark, **options) as builder:
        for i, patient in enumerate(stream_patients(pool_path, len(per_patient), size)):
            t = time.perf_counter()
            builder.add(patient)
            adding += time.perf_counter() - t
            if i % ADD_SAMPLE_EVERY == 0:
                add_rss.append(rss_now())
                progress(marker, 'add', patients=i, peak_rss_bytes_so_far=peak_now(), stages=stages)
        del patient
        events = builder.events
        mark('added', {'patients': builder.patients, 'events': events, 'add_seconds': adding,
                       'add_rss_max_bytes': max(add_rss), 'add_rss_samples': len(add_rss)})
        snapshot = builder.finish(provenance)
    built = time.perf_counter() - started
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    return {'distinct_patients': len(per_patient), 'cohorts_generated': cohorts, 'generator_seeds_failed': failed,
            'patients': size, 'ingest_events': events,
            'events_per_distinct_patient': {'mean': statistics.fmean(per_patient), 'p95': pct(per_patient, .95),
                                            'max': per_patient[-1], 'patients': len(per_patient)},
            'generate_seconds': generating, 'convert_seconds': converting, 'pool_bytes': pool_path.stat().st_size,
            'build_seconds': built, 'add_seconds': adding, 'memory_limit': memory_limit,
            'build_stages': stages,
            'snapshot_path': str(snapshot), 'snapshot_id': manifest['snapshot_id'],
            'snapshot_bytes': directory_bytes(snapshot),
            'tables': {n: t['rows'] for n, t in manifest['tables'].items()}}


def limit_bytes(limit):
    """A DuckDB memory limit in bytes: KB, MB, GB and TB are powers of 1000, KiB to TiB powers of 1024."""
    number, unit = re.fullmatch(r'([0-9.]+)\s*([KMGT]i?B|B)', limit).groups()
    power = 'BKMGT'.index(unit[0])
    return float(number) * (1024 if 'i' in unit else 1000) ** power


def rss_now():
    return current_rss(os.getpid())


def peak_now():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def current_rss(pid):
    try:
        out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip() or 0) * 1024
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


# D34: the fixed, minimal environment a build process runs in: a plain `PATH` (for `ps`), UTC, the C locale, one
# thread for any library that reads a thread count from the environment; `TMPDIR` and `HOME` are a fresh directory.
CHILD_ENVIRONMENT = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TZ': 'UTC', 'LC_ALL': 'C', 'LANG': 'C',
                     'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}


def build_command(fresh):
    """How a build process starts (D34): this script in Python's isolated mode without `site` (`-I -S`: no environment
    variables, user site, script directory, `.pth` file or `sitecustomize`), the libraries' directories passed
    explicitly, and bytecode compiled into an empty cache in `fresh`, outside the repository, so that no `.pyc` in the
    repository's `__pycache__` runs in place of its source."""
    import site
    command = [sys.executable, '-I', '-S', '-X', f'pycache_prefix={Path(fresh) / "pycache"}', str(Path(__file__).resolve())]
    for directory in site.getsitepackages():
        command += ['--site', directory]
    return command


def run_phase(name, arguments, output, budget, rss_limit):
    """Run one worker; (result or None, record). The record has wall seconds, peak RSS and any failure. The build worker
    is this script, started by `build_command` in `CHILD_ENVIRONMENT` with a fresh temporary directory; every other
    worker is the benchmark's own."""
    import tempfile
    fresh = Path(tempfile.mkdtemp(prefix='scaling-build-'))
    if name == 'build':
        command, env = build_command(fresh), {**CHILD_ENVIRONMENT, 'TMPDIR': str(fresh), 'HOME': str(fresh)}
    else:
        command, env = [sys.executable, str(ROOT / 'tools/benchmark_execution_store.py')], None
    command += ['--worker', name, '--worker-output', str(output), *arguments]
    started = time.monotonic()
    try:
        return _run_child(command, env, output, budget, rss_limit, started)
    finally:
        shutil.rmtree(fresh, ignore_errors=True)


def _run_child(command, env, output, budget, rss_limit, started):
    child = subprocess.Popen(command, cwd=ROOT, env=env, stderr=subprocess.PIPE, text=True, start_new_session=True)
    failure, peak = None, 0
    while True:
        done, status, usage = os.wait4(child.pid, os.WNOHANG)
        if done:
            break
        elapsed = time.monotonic() - started
        peak = max(peak, current_rss(child.pid))
        if elapsed > budget:
            failure = {'reason': 'TIME_BUDGET', 'detail': f'exceeded {budget:.0f} s'}
        elif peak > rss_limit:
            failure = {'reason': 'MEMORY_GUARD', 'detail': f'resident memory reached {peak / 2**30:.1f} GiB, '
                                                           f'limit {rss_limit / 2**30:.0f} GiB'}
        if failure:
            os.killpg(child.pid, signal.SIGKILL)
            _, status, usage = os.wait4(child.pid, 0)
            break
        time.sleep(.5)
    child.returncode = os.waitstatus_to_exitcode(status)  # reaped by wait4, not by Popen
    stderr = child.stderr.read() if child.stderr else ''
    child.stderr.close()
    seconds = time.monotonic() - started
    maxrss = usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    record = {'wall_seconds': seconds, 'peak_rss_bytes': max(maxrss, peak)}
    if failure is None and (not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0):
        failure = {'reason': 'WORKER_FAILED', 'detail': (stderr.strip().splitlines() or ['no message'])[-1][:300]}
    if failure:
        marker = Path(output).with_suffix('.progress')
        if marker.exists():
            failure['last_progress'] = json.loads(marker.read_text())
        return None, {**record, 'failure': failure}
    return json.loads(Path(output).read_text()), record


# The build figures of a size entry: what a reused size keeps from the earlier run.
BUILD_FIELDS = ('build_phase', 'distinct_patients', 'cohorts_generated', 'generator_seeds_failed', 'patients',
                'ingest_events', 'events_per_distinct_patient', 'generate_seconds', 'convert_seconds', 'pool_bytes',
                'build_seconds', 'add_seconds', 'memory_limit', 'snapshot_id', 'snapshot_bytes', 'tables', 'build_stages')


def build_size(size, scratch, staging, threads, memory_limit, budget_seconds):
    """Build one size's snapshot in this script's own isolated process, measured from outside: (the size's build
    fields, the worker's result with its snapshot path and runtime record), or (fields holding the failure, None).
    The benchmark records the fields as they are and checks, before it writes the size's entry, that nothing changed
    them (D34)."""
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    built, record = run_phase('build', ['--size', str(size), '--work-dir', str(scratch), '--staging', staging,
                                        '--threads', str(threads), '--memory-limit', memory_limit],
                              scratch / 'build.json', budget_seconds, RSS_LIMIT_BYTES)
    fields = {'build_phase': {k: v for k, v in record.items() if k != 'failure'}}
    if built is None:
        fields.update(status='failed', failed_phase='build', failure=record['failure'])
        return fields, None
    fields.update({k: v for k, v in built.items() if k not in ('snapshot_path', 'build_stages', 'record')})
    fields['build_stages'] = {'stages': built['build_stages'], 'process_peak_rss_bytes': record['peak_rss_bytes'],
                              'process_peak_rss_over_memory_limit': record['peak_rss_bytes'] / limit_bytes(memory_limit)}
    (scratch / 'pool.ndjson').unlink(missing_ok=True)
    return fields, built


def main(argv=None):
    """The build worker: one size's snapshot, and the runtime record of everything the process loaded and read, with the
    settings it built with as it saw them."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--worker', choices=('build',), required=True)
    parser.add_argument('--worker-output', type=Path, required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--work-dir', required=True)
    parser.add_argument('--staging', choices=('parquet', 'ndjson'), default='parquet')
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--memory-limit', default=DEFAULT_MEMORY_LIMIT)
    parser.add_argument('--site', action='append', default=[], help='a library directory (Python runs without `site`)')
    args = parser.parse_args(argv)
    # Before anything of the repository is imported: the bytecode cache must hold nothing for it yet, or a planted
    # `.pyc` there could run in place of its source (D34). The starting process makes the cache a fresh directory.
    if sys.pycache_prefix and Path(sys.pycache_prefix, *ROOT.resolve().parts[1:]).exists():
        raise RuntimeError(f'BYTECODE_CACHE_NOT_FRESH: {sys.pycache_prefix} already holds bytecode of the repository')
    fresh_cache = bool(sys.pycache_prefix)
    sys.path.extend(d for d in args.site if d not in sys.path)
    from patterns.build_record import Recorder, python_directories
    recorder = Recorder(ROOT, python_directories(args.site))  # before anything else the build needs is imported
    recorder.check_process()
    marker = args.worker_output.with_suffix('.progress')
    recorder.ignore(args.work_dir, args.worker_output, marker)
    from patterns import execution_store, synthetic_ingest  # noqa: F401  (what the build runs, noted at the start)
    recorder.start(BUILD_FILES)
    result = build_worker(args.size, args.work_dir, args.threads, args.memory_limit, marker, args.staging)
    # What shaped the build, as this process saw it: reuse compares these, never the parent's account of them.
    settings = {'staging': args.staging, 'threads': args.threads, 'memory_limit': args.memory_limit,
                'patients_per_cohort': PER_COHORT, 'distinct_pool_cap': DISTINCT_CAP, 'generator_seed_base': SEED_BASE,
                'add_sample_every': ADD_SAMPLE_EVERY,
                'process': {'isolated': bool(sys.flags.isolated), 'no_site': bool(sys.flags.no_site),
                            'fresh_bytecode_cache': fresh_cache},
                # every variable, so that an extra one (DUCKDB_*, ARROW_*) leaves a trace; the fresh directories masked
                'environment': {k: '<fresh directory>' if k in ('TMPDIR', 'HOME') else v
                                for k, v in sorted(os.environ.items())}}
    result['record'] = recorder.record(BUILD_FILES, settings)
    # The generator's bytes as executed must be the bytes recorded: an edit made and reverted during the build is caught.
    for path, digest in EXECUTED.items():
        if result['record']['files'].get(path) != digest:
            raise RuntimeError(f'PINNED_FILE_CHANGED_DURING_BUILD: {path}')
    result['patients'] = args.size
    args.worker_output.write_text(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
