#!/usr/bin/env python3
"""The scaling benchmark's build phase, in a process of its own (D34).

`tools/benchmark_execution_store.py` runs this script for each size's build (`run_phase('build', ...)`) and measures
it from outside. It holds nothing but the build: its `main` and every top-level statement are roots of the build
fingerprint (`patterns/build_fingerprint.py`, `BUILD_ROOTS`), so no edit to the benchmark's query or dispatch code can
change a build without changing the fingerprint, and any edit here does.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None):
    from tools import benchmark_execution_store as bench
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--worker', choices=('build',), required=True)
    parser.add_argument('--worker-output', type=Path, required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--work-dir', required=True)
    parser.add_argument('--staging', choices=('parquet', 'ndjson'), default='parquet')
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--memory-limit', default=bench.DEFAULT_MEMORY_LIMIT)
    args = parser.parse_args(argv)
    marker = args.worker_output.with_suffix('.progress')
    result = bench.build_worker(args.size, args.work_dir, args.threads, args.memory_limit, marker, args.staging)
    args.worker_output.write_text(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
