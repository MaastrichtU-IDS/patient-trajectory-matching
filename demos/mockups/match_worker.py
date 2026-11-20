"""Hosted: the synthetic store's matches in a child process with a hard time limit (D50, security re-check of M1).

The store's cost per request is not bounded by its search budget alone. Measured on the synthetic cohort at one
worker, a 6-step chain of `seizure` steps with first, last and any occurrences, tight `after` windows and 4
relaxations costs about 6.5 s even at a search budget of 500, because each of its 16 relaxation sets does work
(the occurrence and order checks) that the budget does not count. The page's own graph template has the same
structure (5 steps, 4 relaxations, 16 sets) and costs 0.07 s only because its concepts are sparse, so no cap on
structure separates the two. The bound is therefore on time: each match runs in a child process, and one that has
not answered within the time limit is killed (503 MATCH_TIME_LIMIT).

A spare child is kept built and waiting, so after a kill the next match goes to it at once and the replacement is
built off the request path (about 1.1 s). A match waits at most `lock_wait` seconds for the child (503 BUSY), and
every kill is reported to `on_event` (the server logs `match_killed`, with no body or address).

The child is this file run as its own program (`python -I match_worker.py COHORT ROOT`), so nothing of the
parent's main module is imported again. It runs `store_trajectory.TrajectoryStore` itself, unchanged, so its
answers, content check and refusals are the in-process store's. The protocol (one JSON request per line, one JSON
answer per line) uses a duplicate of the child's original stdout; its fd 1 and `sys.stdout` are pointed at stderr,
which is discarded, so nothing the store or a library prints can corrupt an answer and no traceback leaves the
child. The child limits its own address space (RLIMIT_AS, `memory_mib`; glibc malloc arenas capped at 2). Its build
goes into a scratch directory of its own under the server's store root, removed when the child is killed or the
server closes. Locally (no time limit) the server matches in-process as before.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
START_WAIT = 120  # seconds to wait for a child to build its store


def child_main(cohort, root):
    """The child's loop: build the store, report, then answer one request per line until stdin closes."""
    proto = os.fdopen(os.dup(1), 'w', buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def send(value):
        proto.write(json.dumps(value) + '\n')
        proto.flush()
    limit = int(os.environ.get('PTM_MATCH_WORKER_MAX_MIB') or 0)
    if limit and sys.platform.startswith('linux'):  # hosting is Linux; macOS does not enforce RLIMIT_AS
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (limit << 20, limit << 20))
        except (ImportError, ValueError, OSError):
            send({'ready': False, 'error': 'WORKER_MEMORY_LIMIT_FAILED'})
            return
    sys.path.insert(0, str(HERE.parents[1]))
    sys.path.insert(0, str(HERE))
    import store_trajectory
    try:
        store = store_trajectory.TrajectoryStore(cohort, root).start()
        status = store.status()
    except MemoryError:
        send({'ready': False, 'error': 'WORKER_OUT_OF_MEMORY'})
        return
    send({'ready': bool(status.get('available')), 'error': status.get('error')})
    for line in sys.stdin:
        try:
            send({'ok': store.match(json.loads(line))})
        except store_trajectory.RequestError as error:
            send({'refused': str(error), 'by': error.by})
        except store_trajectory.Unavailable as error:
            send({'unavailable': str(error)})
        except Exception:  # noqa: BLE001 - named in the parent, with no detail
            send({'internal': True})
    store.close()


class _Child:
    """A started child whose store is built: its process, its scratch directory, and a timed line reader."""

    def __init__(self, process, scratch):
        self.process, self.scratch = process, scratch

    def read(self, timeout):
        """One answer within `timeout` seconds; None on timeout; EOFError on a closed pipe or a line that is not one."""
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        if not ready:
            return None
        line = self.process.stdout.readline()
        if not line:
            raise EOFError('worker closed its output')
        value = json.loads(line)  # ValueError on garbage
        if not isinstance(value, dict):
            raise ValueError('not an answer')
        return value

    def alive(self):
        return self.process.poll() is None

    def kill(self):
        self.process.kill()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        shutil.rmtree(self.scratch, ignore_errors=True)


class IsolatedMatcher:
    """`match(spec)` with the in-process store's answers, refusals and errors, each bounded by `time_limit` seconds."""

    def __init__(self, cohort, root=None, time_limit=2.0, memory_mib=0, lock_wait=3.0, on_event=None):
        self.cohort = str(cohort)
        self.root = Path(root) if root else Path(tempfile.gettempdir())
        self.time_limit, self.memory_mib, self.lock_wait = time_limit, memory_mib, lock_wait
        self.on_event = on_event or (lambda event, **fields: None)
        self._lock = threading.Lock()        # the active child: one match at a time
        self._spare_lock = threading.Lock()  # the spare slot and its builder
        self._active = self._spare = None
        self._building = False
        self._closed = False
        self._started_once = False
        self._spare_ready_once = False
        self._last_error = None
        self.kills = 0

    # ---------------------------------------------------------------- children

    def _launch(self):
        """A new child with its store built, or store_trajectory.Unavailable."""
        import store_trajectory
        self.root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix='ptm-match-worker-', dir=self.root))
        env = {**os.environ, 'MALLOC_ARENA_MAX': '2', 'PTM_MATCH_WORKER_MAX_MIB': str(self.memory_mib or 0)}
        process = subprocess.Popen(
            [sys.executable, '-I', str(Path(__file__).resolve()), self.cohort, str(scratch)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        child = _Child(process, scratch)
        try:
            hello = child.read(START_WAIT)
        except (EOFError, ValueError):
            hello = None
        if not hello or not hello.get('ready'):
            child.kill()
            self._last_error = (hello or {}).get('error') or 'MATCH_WORKER_FAILED'
            self.on_event('match_worker_start_failed', reason=self._last_error)
            raise store_trajectory.Unavailable('MATCH_WORKER_FAILED')
        self._started_once, self._last_error = True, None
        return child

    def _refill(self):
        """Build a spare child on a background thread, unless one exists or is being built."""
        with self._spare_lock:
            if self._closed or self._spare is not None or self._building:
                return
            self._building = True

        def build():
            import store_trajectory
            child = None
            try:
                child = self._launch()
            except store_trajectory.Unavailable:
                pass
            with self._spare_lock:
                self._building = False
                if child is not None and (self._closed or self._spare is not None):
                    child.kill()
                elif child is not None:
                    self._spare = child
                    self._spare_ready_once = True
        threading.Thread(target=build, name='ptm-match-worker-spare', daemon=True).start()

    def _take_spare(self):
        with self._spare_lock:
            child, self._spare = self._spare, None
        return child if child is not None and child.alive() else None

    def start(self):
        """Build the first child and a spare now, in the background, so no match waits for a build."""
        def first():
            import store_trajectory
            with self._lock:
                if self._active is None and not self._closed:
                    try:
                        self._active = self._launch()
                    except store_trajectory.Unavailable:
                        pass
            self._refill()
        threading.Thread(target=first, name='ptm-match-worker-start', daemon=True).start()
        return self

    def state(self):
        """'ready' once the first child and its spare have both started (so the first kill already has a spare to
        hand over to), and while any child is available after that; 'failed' if the last start failed and none is
        available; else 'pending'."""
        if self._spare_ready_once and (self._active is not None or self._spare is not None):
            return 'ready'
        if self._last_error is not None and self._active is None and self._spare is None:
            return 'failed'
        return 'pending'

    # ---------------------------------------------------------------- matching

    def match(self, spec, wait=None):
        """The child's answer to `spec`. Waits for the child at most `wait` seconds (default `lock_wait`), then raises
        Unavailable('BUSY'); a match still running after `time_limit` seconds is killed (MATCH_TIME_LIMIT)."""
        import store_trajectory
        if not self._lock.acquire(timeout=self.lock_wait if wait is None else wait):
            raise store_trajectory.Unavailable('BUSY')  # 503: the child is held by earlier matches
        try:
            if self._active is None or not self._active.alive():
                if self._active is not None:
                    self._active.kill()
                self._active = self._take_spare() or self._launch()
                self._refill()
            child, answer, failure = self._active, None, 'MATCH_TIME_LIMIT'
            try:
                child.process.stdin.write(json.dumps(spec) + '\n')
                child.process.stdin.flush()
                answer = child.read(self.time_limit)
            except Exception:  # noqa: BLE001 - a dead child, a broken pipe, or something that is not an answer
                failure = 'MATCH_WORKER_FAILED'
            finally:
                if answer is None:  # timed out or failed after the write: the child is never reused
                    child.kill()
                    self._active = self._take_spare()
                    self.kills += 1
                    self.on_event('match_killed', reason=failure)
                    self._refill()
            if answer is None:
                raise store_trajectory.Unavailable(failure)
        finally:
            self._lock.release()
        if 'ok' in answer:
            return answer['ok']
        if 'refused' in answer:
            error = (store_trajectory.StoreRefused if answer.get('by') == 'store' else store_trajectory.RequestError)
            raise error(answer['refused'])
        if 'unavailable' in answer:
            raise store_trajectory.Unavailable(answer['unavailable'])
        raise RuntimeError('INTERNAL_ERROR')

    def close(self):
        with self._spare_lock:
            self._closed = True
            spare, self._spare = self._spare, None
        if spare is not None:
            spare.kill()
        with self._lock:
            if self._active is not None:
                self._active.kill()
                self._active = None


if __name__ == '__main__':
    child_main(sys.argv[1], sys.argv[2])
