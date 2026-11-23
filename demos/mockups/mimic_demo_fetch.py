"""Hosted: the open MIMIC-IV demo fetched from PhysioNet at start, checked, and built into the pod's own store (D50,
phase 2). It is never baked into the public image: the image is public, while the demo is served to approved users
only (aggregate counts). The demo is open data (ODbL 1.0, no credentialing), so fetching it needs no secret.

    python demos/mockups/mimic_demo_fetch.py DEST     # fetch, check and build; prints a JSON status line

1. **Fetch** the four `icu/` files the store's ingest reads, plus PhysioNet's `SHA256SUMS.txt`, from
   `https://physionet.org/files/mimic-iv-demo/2.2/`, through the egress proxy (HTTPS_PROXY), with a timeout and a
   size cap per file.
2. **Check** each file against the sha256 pinned here, which is the authority (three of the four pins are also
   in `data/mimic-inputevents-demo-pin.json`). PhysioNet's published sums must agree with the pins, or nothing is
   used (PHYSIONET_SUMS_DISAGREE).
3. **Build** the store with the existing ingest (`patterns.mimic_store_ingest --chartevents`, one worker, in a child
   process) into `DEST/build/store`.

The store service's own content gate then still applies when it opens the snapshot: exactly the open demo's 100
subject ids, by a pinned digest (store_query.check_open_content). Any failure leaves no store, with a named code; the
server then reports the dataset as unavailable and every other dataset keeps working.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = 'https://physionet.org/files/mimic-iv-demo/2.2/'
DATASET_ID = 'mimic-iv-demo-2.2'
# sha256 of each file, pinned (PhysioNet's SHA256SUMS.txt for version 2.2, checked on 2026-11-23).
PINS = {
    'icu/chartevents.csv.gz': '90f096ad3db847ed1aaec073f8d86053c51fea7b616d3b046410e030f083d560',
    'icu/d_items.csv.gz': '1690467a822345226a041c1149237c418aa60098c6b699fb4cef64d73588a01f',
    'icu/icustays.csv.gz': 'e05e81aa52a3022e522b6832a898101a69b84e64c17bb344d819e458d5bc21b3',
    'icu/inputevents.csv.gz': '69597dca47e554e16645ca505167e9f39b07c1724e915ba54ccfb40f5fa0078a',
}
MAX_FILE = 64 * 1024 * 1024   # bytes; the largest file, chartevents, is 5.5 MB
TIMEOUT = 60                  # seconds per request
BUILD_TIMEOUT = 1800          # seconds for the store build
ATTRIBUTION = {
    'dataset': 'MIMIC-IV Clinical Database Demo (version 2.2)',
    'citation': 'Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023). MIMIC-IV '
                'Clinical Database Demo (version 2.2). PhysioNet. RRID:SCR_007345. https://doi.org/10.13026/dp1f-ex47',
    'physionet': 'Pollard, T., Moody, B. E., Lehman, L., Gow, B., Fernandes, C., Xie, C., Johnson, A., Mark, R. G., & '
                 'Heldt, T. (2026). PhysioNet as a global platform for biomedical research. Nature Health. '
                 'https://doi.org/10.1038/s44360-026-00096-z',
    'licence': 'Open Database License v1.0 (ODbL)',
    'source': 'https://physionet.org/content/mimic-iv-demo/2.2/',
}


class FetchError(RuntimeError):
    """A named failure: the message is the code."""


def get(url, limit=MAX_FILE, opener=None, allow_http=False):
    """The body at `url` (https only, unless `allow_http`: tests on loopback), at most `limit` bytes."""
    if not (url.startswith('https://') or allow_http and url.startswith('http://')):
        raise FetchError('NOT_HTTPS')
    try:
        with (opener or urllib.request.build_opener()).open(url, timeout=TIMEOUT) as response:
            data = response.read(limit + 1)
    except (urllib.error.URLError, OSError, ValueError):
        raise FetchError('PHYSIONET_UNREACHABLE') from None
    if len(data) > limit:
        raise FetchError('FILE_TOO_LARGE')
    return data


def published_sums(text):
    """{path: sha256} from a SHA256SUMS.txt."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0].lower()
    return out


def fetch(dest, base=BASE, pins=PINS, opener=None, allow_http=False):
    """Download the pinned files into `dest` (`dest/icu/...`), each checked; raises FetchError."""
    dest = Path(dest)
    sums = published_sums(get(base + 'SHA256SUMS.txt', 1024 * 1024, opener, allow_http).decode('utf-8', 'replace'))
    if any(sums.get(path) != digest for path, digest in pins.items()):
        raise FetchError('PHYSIONET_SUMS_DISAGREE')
    for path, digest in pins.items():
        data = get(base + path, MAX_FILE, opener, allow_http)
        if hashlib.sha256(data).hexdigest() != digest:
            raise FetchError('CHECKSUM_MISMATCH')
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest / 'icu'


def build(icu_dir, output, timeout=BUILD_TIMEOUT):
    """The store from the fetched files, by the existing ingest in a child process; the snapshot's directory."""
    # At the lowest CPU priority (`nice`), so the pod's matches keep the CPU while the build runs (about 8 minutes on
    # one core, peak about 1.2 GB resident, measured on an M4 Pro).
    nice = [shutil.which('nice'), '-n', '19'] if shutil.which('nice') else []
    command = nice + [sys.executable, '-m', 'patterns.mimic_store_ingest', '--input-dir', str(icu_dir), '--dataset-id',
                      DATASET_ID, '--chartevents', '--workers', '1', '--output', str(output)]
    try:
        done = subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout,
                              env={**os.environ, 'MALLOC_ARENA_MAX': '2'})
    except subprocess.TimeoutExpired:
        raise FetchError('STORE_BUILD_TIMED_OUT') from None
    if done.returncode != 0:
        raise FetchError('STORE_BUILD_FAILED')
    store = Path(output) / 'store'
    if not any(store.glob('**/manifest.json')):
        raise FetchError('STORE_BUILD_FAILED')
    return store


def fetch_and_build(dest, base=BASE, pins=PINS, opener=None, build_store=build, allow_http=False):
    """Fetch, check and build into `dest`: the store at `dest/build/store`, and `dest/d_items.csv.gz` for the item
    labels. Downloads go to a scratch directory removed after the build, and a failure leaves nothing behind."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='mimic-demo-download-', dir=dest))
    try:
        icu = fetch(scratch, base, pins, opener, allow_http)
        store = build_store(icu, dest / 'build')
        shutil.copyfile(icu / 'd_items.csv.gz', dest / 'd_items.csv.gz')  # the catalogue's item labels
        return store
    except FetchError:
        shutil.rmtree(dest / 'build', ignore_errors=True)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None):
    dest = Path((argv or sys.argv[1:])[0])
    try:
        store = fetch_and_build(dest)
        print(json.dumps({'ok': True, 'store': str(store)}))
        return 0
    except FetchError as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
