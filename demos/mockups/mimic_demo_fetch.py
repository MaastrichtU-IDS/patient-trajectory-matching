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
FETCH_DEADLINE = 300          # seconds for the whole download (security review of phase 2, L5)
CHUNK = 1 << 16               # bytes per read, the deadline checked after each
# Seconds for the store build. 495 s on an M4 Pro, but over 1,800 s on ids3 (2026-11-27): one CPU at most, a 0.1 CPU
# request on a shared node, and the build then wrote onto the Longhorn volume (133 MB/s against 791 MB/s locally,
# measured in the pod). The build now runs on node-local scratch (PTM_MIMIC_SCRATCH); the limit is an hour.
BUILD_TIMEOUT = 3600
# Where the build runs when set (hosted: an emptyDir on the node's disk); only the finished store is copied onto the
# volume. Unset, the build runs under the destination itself, as before.
SCRATCH_ENV = 'PTM_MIMIC_SCRATCH'
# The build child's address-space limit (RLIMIT_AS, Linux), so a runaway build fails by itself instead of getting the
# whole pod OOM-killed (security review L2): 1,228 MiB of address space measured in a Linux container, times 1.3
# (the hosting plan, "Phase 2").
BUILD_MAX_MIB = 1600
# The named failures; anything else is reported as MIMIC_DEMO_FETCH_FAILED (security review L6).
FETCH_ERRORS = frozenset({'NOT_HTTPS', 'PHYSIONET_UNREACHABLE', 'PHYSIONET_REDIRECT', 'FILE_TOO_LARGE',
                          'PHYSIONET_SUMS_DISAGREE', 'CHECKSUM_MISMATCH', 'FETCH_DEADLINE_EXCEEDED',
                          'STORE_BUILD_FAILED', 'STORE_BUILD_TIMED_OUT', 'SNAPSHOT_ID_NOT_PINNED'})
# DuckDB's memory limit for the build. Measured (one worker, one thread, M4 Pro): the peak resident set is 1.30 GB at
# 256MB, 1.23 GB at 512MB and 1.27 GB at the store's default 4GB, all in about 495 s and with the same snapshot id.
# The peak is in the ingest's Python stages, not in DuckDB, so no limit brings it under 1 GB; 512MB keeps DuckDB's
# share bounded at no cost in time.
BUILD_MEMORY_LIMIT = '512MB'
# The snapshot the build produces from the pinned files (measured 2026-11-23, the same at every DuckDB memory limit).
# A store found on a persistent volume is reused only if it opens, content-verified, as exactly this snapshot; a fresh
# build is published only if it is this snapshot; and the store service opens nothing else (expected_snapshot_id).
# A change to the ingest that alters the id therefore fails closed (SNAPSHOT_ID_NOT_PINNED) until this pin is updated.
SNAPSHOT_ID = '0282b797d9356681f2484a1c59de30718cb5989f2a8190a32233b9a409aa4c78'
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
    """A named failure: the message is the code; `detail` (for the log only, never shown to a caller) says more, such
    as the HTTP status PhysioNet answered."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise FetchError('PHYSIONET_REDIRECT')  # the files are read from the pinned address only


def default_opener():
    """urllib with the environment's proxies (the egress proxy) and no redirects."""
    return urllib.request.build_opener(_NoRedirect)


def get(url, limit=MAX_FILE, opener=None, allow_http=False, deadline=None):
    """The body at `url` (https only, unless `allow_http`: tests on loopback), at most `limit` bytes, before the
    monotonic `deadline` if one is given."""
    import time
    if not (url.startswith('https://') or allow_http and url.startswith('http://')):
        raise FetchError('NOT_HTTPS')
    timeout = TIMEOUT if deadline is None else min(TIMEOUT, deadline - time.monotonic())
    if timeout <= 0:
        raise FetchError('FETCH_DEADLINE_EXCEEDED')
    chunks, size = [], 0
    try:
        with (opener or default_opener()).open(url, timeout=timeout) as response:
            # Read in chunks, each at most one socket read (read1) of at most `timeout` seconds, checking the
            # deadline after each: a server that trickles bytes cannot hold the download past it by more than one
            # read (the phase 2 re-check, low 4).
            while size <= limit:
                chunk = response.read1(min(CHUNK, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if deadline is not None and time.monotonic() > deadline:
                    raise FetchError('FETCH_DEADLINE_EXCEEDED')
    except FetchError:
        raise
    except urllib.error.HTTPError as error:  # an answer, but not the file: 429 or 403 when rate-limited, for example
        raise FetchError('PHYSIONET_UNREACHABLE', f'HTTP {error.code}') from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, 'reason', error)
        raise FetchError('PHYSIONET_UNREACHABLE', type(reason).__name__) from None
    if deadline is not None and time.monotonic() > deadline:
        raise FetchError('FETCH_DEADLINE_EXCEEDED')
    if size > limit:
        raise FetchError('FILE_TOO_LARGE')
    return b''.join(chunks)


def published_sums(text):
    """{path: sha256} from a SHA256SUMS.txt."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0].lower()
    return out


def fetch(dest, base=BASE, pins=PINS, opener=None, allow_http=False, deadline_s=FETCH_DEADLINE):
    """Download the pinned files into `dest` (`dest/icu/...`), each checked, all within `deadline_s` seconds; raises
    FetchError."""
    import time
    dest = Path(dest)
    deadline = time.monotonic() + deadline_s
    sums = published_sums(get(base + 'SHA256SUMS.txt', 1024 * 1024, opener, allow_http, deadline)
                          .decode('utf-8', 'replace'))
    if any(sums.get(path) != digest for path, digest in pins.items()):
        raise FetchError('PHYSIONET_SUMS_DISAGREE')
    for path, digest in pins.items():
        data = get(base + path, MAX_FILE, opener, allow_http, deadline)
        if hashlib.sha256(data).hexdigest() != digest:
            raise FetchError('CHECKSUM_MISMATCH')
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return dest / 'icu'


def build(icu_dir, output, timeout=BUILD_TIMEOUT, memory_limit=None):
    """The store from the fetched files, built in a child process (this file's `--build` mode, which calls the
    existing ingest's `stream_build` with one worker, one DuckDB thread and a DuckDB memory limit); the store root."""
    # At the lowest CPU priority (`nice`), so the pod's matches keep the CPU while the build runs.
    nice = [shutil.which('nice'), '-n', '19'] if shutil.which('nice') else []
    command = nice + [sys.executable, '-I', str(Path(__file__).resolve()), '--build', str(icu_dir), str(output),
                      memory_limit or BUILD_MEMORY_LIMIT]
    try:
        done = subprocess.run(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
                              env={**os.environ, 'MALLOC_ARENA_MAX': '2', 'PTM_BUILD_MAX_MIB': str(BUILD_MAX_MIB)})
    except subprocess.TimeoutExpired:
        raise FetchError('STORE_BUILD_TIMED_OUT') from None
    if done.returncode != 0:
        raise FetchError('STORE_BUILD_FAILED')
    store = Path(output) / 'store'
    if not any(store.glob('**/manifest.json')):
        raise FetchError('STORE_BUILD_FAILED')
    return store


def build_child(icu_dir, output, memory_limit):
    """The `--build` mode: the ingest's streamed build of the demo with chartevents, as `mimic_store_ingest
    --chartevents` does, with the DuckDB memory limit the CLI does not expose."""
    limit = os.environ.get('PTM_BUILD_MAX_MIB', '')
    if limit.isdigit() and sys.platform.startswith('linux'):  # hosting is Linux; macOS does not enforce RLIMIT_AS
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (int(limit) << 20, int(limit) << 20))
    sys.path.insert(0, str(HERE))
    import match_worker
    match_worker.confine_duckdb()  # one worker thread and one async thread per DuckDB database, as in the matchers
    sys.path.insert(0, str(ROOT))
    from patterns import mimic_store_ingest as msi
    msi.stream_build(Path(icu_dir), DATASET_ID, Path(output) / 'store', workers=1, threads=1, memory_limit=memory_limit)


def reusable(dest, snapshot_id=SNAPSHOT_ID, pins=PINS):
    """`dest`'s store if it may be served without rebuilding, else None. Nothing on the volume is trusted on its
    word: the snapshot must open with every table's content re-derived from its files (`verify='content'`) and
    match the pinned snapshot id, and `d_items` must match its pinned sha256. (The store service's content gate,
    the demo's 100 subjects, applies again when it opens the store.)"""
    dest = Path(dest)
    store, items = dest / 'build' / 'store', dest / 'd_items.csv.gz'
    try:
        snapshots = [p for p in store.iterdir() if (p / 'manifest.json').is_file()] if store.is_dir() else []
        if len(snapshots) != 1 or not items.is_file():
            return None
        if hashlib.sha256(items.read_bytes()).hexdigest() != pins['icu/d_items.csv.gz']:
            return None
        sys.path.insert(0, str(ROOT))
        from patterns import execution_store as es
        es.open_store(snapshots[0], verify='content', expected_snapshot_id=snapshot_id).close()
        return store
    except Exception:  # noqa: BLE001 - anything unexpected about the volume's contents means: build again
        return None


def clear_leftovers(dest):
    """Remove what an interrupted run left under `dest`: scratch directories and stores set aside (security review
    L1). Nothing in them is ever served."""
    dest = Path(dest)
    if dest.is_dir():
        for leftover in list(dest.glob('mimic-demo-incoming-*')) + list(dest.glob('.stale-*')):
            shutil.rmtree(leftover, ignore_errors=True)


def prepare(dest, base=BASE, pins=PINS, opener=None, build_store=build, allow_http=False, snapshot_id=SNAPSHOT_ID):
    """The demo's store at `dest/build/store`, and `(store, how)`: 'reused' when a verified store is already there
    (a persistent volume across restarts), else 'built' by fetching, checking and building."""
    clear_leftovers(dest)
    store = reusable(dest, snapshot_id, pins)
    if store is not None:
        return store, 'reused'
    scratch_root = os.environ.get(SCRATCH_ENV) or None
    if scratch_root:
        clear_leftovers(scratch_root)
    return fetch_and_build(dest, base, pins, opener, build_store, allow_http, snapshot_id, scratch_root), 'built'


def built_snapshot_id(store):
    """The snapshot id a fresh build's manifest names (its content is checked again when the store service opens it,
    with expected_snapshot_id)."""
    manifests = list(Path(store).glob('*/manifest.json'))
    if len(manifests) != 1:
        raise FetchError('STORE_BUILD_FAILED')
    try:
        return json.loads(manifests[0].read_text())['snapshot_id']
    except (ValueError, KeyError, OSError):
        raise FetchError('STORE_BUILD_FAILED') from None


def cached_download(dest, pins=PINS):
    """`dest/download/icu` if every pinned file is there and matches its sha256, else None (and an unverifiable cache
    is removed). A verified download is kept on the volume so that a failed build, a retry or a restart never
    downloads from PhysioNet again (ids3, 2026-11-27: retries right after a full download were refused)."""
    root = Path(dest) / 'download'
    try:
        if all(hashlib.sha256((root / path).read_bytes()).hexdigest() == digest for path, digest in pins.items()):
            return root / 'icu'
    except OSError:
        pass
    shutil.rmtree(root, ignore_errors=True)
    return None


def fetch_and_build(dest, base=BASE, pins=PINS, opener=None, build_store=build, allow_http=False,
                    snapshot_id=SNAPSHOT_ID, scratch_root=None):
    """Fetch, check and build into `dest`: the store at `dest/build/store`, and `dest/d_items.csv.gz` for the item
    labels. The checked download is kept at `dest/download` (re-checked before every use). The build runs in a scratch
    directory under `scratch_root` (node-local when hosted) or else under `dest`; the finished store is copied into a
    staging directory under `dest` and moved into place only once complete, so a reader never sees a half-built store
    and a failure leaves no store behind. (Two pods sharing the volume may both build; the first to finish publishes,
    the other discards its copy.)"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    icu = cached_download(dest, pins)
    if icu is None:
        incoming = Path(tempfile.mkdtemp(prefix='mimic-demo-incoming-', dir=dest))
        try:
            fetch(incoming / 'download', base, pins, opener, allow_http)
            try:
                os.rename(incoming / 'download', dest / 'download')
            except OSError:  # another pod kept its download first; it is checked like any other
                pass
        finally:
            shutil.rmtree(incoming, ignore_errors=True)
        icu = cached_download(dest, pins)
        if icu is None:
            raise FetchError('CHECKSUM_MISMATCH')
    scratch_parent = Path(scratch_root) if scratch_root else dest
    scratch_parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='mimic-demo-incoming-', dir=scratch_parent))
    staging = None
    try:
        store = build_store(icu, scratch / 'build')
        if snapshot_id is not None and built_snapshot_id(store) != snapshot_id:
            raise FetchError('SNAPSHOT_ID_NOT_PINNED')  # never published: the store service would refuse it anyway
        if scratch_parent == dest:
            staging = scratch
        else:  # node-local scratch: copy the finished store onto the volume, then publish it there
            staging = Path(tempfile.mkdtemp(prefix='mimic-demo-incoming-', dir=dest))
            shutil.copytree(scratch / 'build', staging / 'build')
        shutil.copyfile(icu / 'd_items.csv.gz', staging / 'd_items.csv.gz')  # the catalogue's item labels
        stale = dest / f'.stale-{staging.name}'
        if (dest / 'build').exists():  # an older or unverifiable store: set aside, then removed
            os.rename(dest / 'build', stale)
        try:
            os.rename(staging / 'build', dest / 'build')
        except OSError:  # another pod published first
            pass
        os.replace(staging / 'd_items.csv.gz', dest / 'd_items.csv.gz')
        shutil.rmtree(stale, ignore_errors=True)
        return dest / 'build' / 'store'
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == '--build':
        build_child(argv[1], argv[2], argv[3])
        return 0
    try:
        store, how = prepare(Path(argv[0]))
        print(json.dumps({'ok': True, 'store': str(store), 'how': how}))
        return 0
    except FetchError as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
