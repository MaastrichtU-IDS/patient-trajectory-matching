"""Hosted: the synthetic store's matches in a child process with a hard time limit (D50, security re-check of M1).

The store's cost per request is not bounded by its search budget alone. Measured on the synthetic cohort at one
worker, a 6-step chain of `seizure` steps with first, last and any occurrences, tight `after` windows and 4
relaxations costs about 6.5 s even at a search budget of 500, because each of its 16 relaxation sets does work
(the occurrence and order checks) that the budget does not count. The page's own graph template has the same
structure (5 steps, 4 relaxations, 16 sets) and costs 0.07 s only because its concepts are sparse, so no cap on
structure separates the two. The bound is therefore on time: each match runs in a child process, and one that has
not answered within the time limit is killed (503 MATCH_TIME_LIMIT). The child is then started again, which builds
its own copy of the store (about 1.3 s).

The child is this file run as its own program (`python -I match_worker.py COHORT ROOT`), so nothing of the
parent's main module is imported again. It runs `store_trajectory.TrajectoryStore` itself, unchanged, so its
answers, content check and refusals are the in-process store's. It reads one JSON request per line on stdin and
writes one JSON answer per line on stdout; its stderr is discarded, so no traceback leaves it. Its build goes into a
scratch directory of its own under the server's store root, removed when the child is killed or the server
closes. Locally (no time limit) the server matches in-process as before.
"""
from __future__ import annotations

import json
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
START_WAIT = 120  # seconds a match waits for a child to build its store


def child_main(cohort, root):
    """The child's loop: build the store, report, then answer one request per line until stdin closes."""
    sys.path.insert(0, str(HERE.parents[1]))
    sys.path.insert(0, str(HERE))
    import store_trajectory
    out = sys.stdout

    def send(value):
        out.write(json.dumps(value) + '\n')
        out.flush()
    store = store_trajectory.TrajectoryStore(cohort, root).start()
    status = store.status()
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


class IsolatedMatcher:
    """`match(spec)` with the in-process store's answers, refusals and errors, each bounded by `time_limit` seconds."""

    def __init__(self, cohort, root=None, time_limit=2.0):
        self.cohort = str(cohort)
        self.root = Path(root) if root else Path(tempfile.gettempdir())
        self.time_limit = time_limit
        self._lock = threading.Lock()
        self._process = self._scratch = None
        self.kills = 0

    def _read(self, timeout):
        """One line from the child within `timeout` seconds, or None."""
        ready, _, _ = select.select([self._process.stdout], [], [], timeout)
        if not ready:
            return None
        line = self._process.stdout.readline()
        return json.loads(line) if line else None

    def _start(self):
        import store_trajectory
        self.root.mkdir(parents=True, exist_ok=True)
        self._scratch = Path(tempfile.mkdtemp(prefix='ptm-match-worker-', dir=self.root))
        self._process = subprocess.Popen(
            [sys.executable, '-I', str(Path(__file__).resolve()), self.cohort, str(self._scratch)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        hello = self._read(START_WAIT)
        if not hello or not hello.get('ready'):
            self._stop()
            raise store_trajectory.Unavailable((hello or {}).get('error') or 'STORE_BUILDING')

    def _stop(self):
        if self._process is not None:
            self._process.kill()
            try:
                self._process.wait(5)
            except subprocess.TimeoutExpired:
                pass
            for stream in (self._process.stdin, self._process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
        if self._scratch is not None:
            shutil.rmtree(self._scratch, ignore_errors=True)
        self._process = self._scratch = None

    def start(self):
        """Start the child now (on a background thread), so the first match does not wait for its build."""
        def warm():
            import store_trajectory
            with self._lock:
                if self._process is None:
                    try:
                        self._start()
                    except store_trajectory.Unavailable:
                        pass
        threading.Thread(target=warm, name='ptm-match-worker-start', daemon=True).start()
        return self

    def match(self, spec):
        import store_trajectory
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._stop()
                self._start()
            try:
                self._process.stdin.write(json.dumps(spec) + '\n')
                self._process.stdin.flush()
                answer = self._read(self.time_limit)
                failure = 'MATCH_TIME_LIMIT'
            except (OSError, ValueError):  # the child died, or wrote something that is not an answer
                answer, failure = None, 'MATCH_WORKER_FAILED'
            if answer is None:
                self._stop()
                self.kills += 1
                raise store_trajectory.Unavailable(failure)
        if 'ok' in answer:
            return answer['ok']
        if 'refused' in answer:
            error = (store_trajectory.StoreRefused if answer.get('by') == 'store' else store_trajectory.RequestError)
            raise error(answer['refused'])
        if 'unavailable' in answer:
            raise store_trajectory.Unavailable(answer['unavailable'])
        raise RuntimeError('INTERNAL_ERROR')

    def close(self):
        with self._lock:
            self._stop()


if __name__ == '__main__':
    child_main(sys.argv[1], sys.argv[2])
