"""A runtime record of what a build loaded and read (D34): how a report binds its build phase apart from the rest.

The scaling report (`tools/benchmark_execution_store.py`) builds snapshots of up to 1,000,000 patients and then queries
them. A report bound by whole-file digests of everything goes stale when matching code changes, though the builds could
not have changed. So the build runs in a process of its own (`tools/scaling_build_worker.py`), started by the benchmark
in a fixed way: Python's isolated mode without the `site` module (`-I -S`; the libraries' directories are passed
explicitly, so no `.pth` file or `sitecustomize` runs), bytecode compiled afresh into an empty cache outside the
repository (`-X pycache_prefix`), and a fixed minimal environment. At its end this module records what the process
actually used:

- *modules:* every module loaded from the repository, with the `__init__.py` of every package on its path, each by the
  sha256 of its whole source file (rule `runtime_record_v1`). The repository is the directory tree of the checkout less
  the Python installation's own directories (its prefixes, `site-packages` and `sysconfig` paths), so a virtual
  environment kept inside the checkout is not repository code;
- *files:* the data files the build may read (the caller's pinned list), each by whole-file digest;
- *versions:* the Python implementation and version, and the versions of DuckDB, pyarrow (if installed) and jsonschema;
- *root:* whether the repository was a checkout or an installed package (`patterns/_resources.py`);
- *settings:* what shapes the build, as the build process itself saw it (the caller's `settings`).

Checks, each stopping the build by name:
- the process was not started as above: not isolated, `site` imported, or no bytecode cache outside the repository
  (`PROCESS_NOT_ISOLATED`, `BYTECODE_NOT_ISOLATED`): bytecode is not source, and a stale or unchecked `.pyc` in the
  repository's `__pycache__` would run in place of its source. The starting process makes the cache a fresh, empty
  directory;
- an audit hook sees every file Python opens; a repository file read that is neither a recorded module's source nor
  pinned (bytecode in the repository's `__pycache__` included) stops the build (`UNRECORDED_FILE_READ`);
- a module of the engine's matching code loaded (`MATCHING_CODE_LOADED`): the build must not import it at all;
- a repository module loaded that the record could not describe (`UNRECORDED_MODULE`: no file, or not a plain `.py`);
- a recorded module or pinned file that changed while the build ran (`FILE_CHANGED_DURING_BUILD`).

What it does not cover:
- files read by native code outside Python's audit hooks. DuckDB's own reads are confined instead: the store's build
  connections may touch only the build's directories (`allowed_directories`, external access disabled);
- code outside the repository beyond the versions recorded (library internals, the operating system, the CPU);
- a repository file the build reaches only on some input and not in the run recorded (the record covers what the run
  loaded; the scaling benchmark's builds load the same modules at every size, and a reuse probes a new build);
- the code of the process that started the build and wrote its figures (`tools/benchmark_execution_store.py`), which
  its own report binds.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

RULE = 'runtime_record_v1'
# The engine's matching code, and the references and checkers built on it: a build loads none of them.
MATCHING_ONLY = ('patterns.trajectory_graph', 'patterns.temporal_stn', 'patterns.trajectory_graph_reference',
                 'patterns.trajectory_graph_worlds', 'patterns.execution_store_reference', 'patterns.prepared_reference')
VERSIONED = ('duckdb', 'pyarrow', 'jsonschema')


class RecordError(RuntimeError):
    def __init__(self, code, where=''):
        super().__init__(f'{code}: {where}' if where else code)
        self.code = code


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _versions():
    from importlib import metadata
    out = {'python': f'{sys.implementation.name} {sys.version.split()[0]}'}
    for name in VERSIONED:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def python_directories(extra=()):
    """The Python installation's own directories: its prefixes, `site-packages` and `sysconfig` paths, and any library
    directories passed explicitly. Nothing under them is repository code, wherever they lie."""
    import site
    import sysconfig
    found = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix, *extra}
    found.update(site.getsitepackages())
    found.add(site.getusersitepackages())
    found.update(sysconfig.get_paths().values())
    out = set()
    for d in found:
        try:
            out.add(Path(d).resolve())
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out)


class Recorder:
    """Install in the build process before it imports the build's modules; `start()` once they are imported, and
    `record()` at its end. `python_dirs` are the installation's directories (default: `python_directories()`)."""

    def __init__(self, root, python_dirs=None, hook=True):
        self.root = Path(root).resolve()
        self.python_dirs = python_directories() if python_dirs is None else [Path(d).resolve() for d in python_dirs]
        if any(self.root == d or self.root.is_relative_to(d) for d in self.python_dirs):
            raise RecordError('ROOT_INSIDE_PYTHON_PREFIX', str(self.root))
        self.opened = set()
        self.first = None
        self.outputs = []
        if hook:
            sys.addaudithook(self._hook)

    def ignore(self, *paths):
        """The build's own outputs (its work directory, its result and progress files): what it writes there is not
        something it reads from the repository, even when they lie inside it."""
        self.outputs.extend(Path(p).resolve() for p in paths)

    def check_process(self):
        """Refuse a process not started as `run_phase` starts a build: isolated, without `site`, with an empty bytecode
        cache outside the repository."""
        if not (sys.flags.isolated and sys.flags.no_site):
            raise RecordError('PROCESS_NOT_ISOLATED', f'isolated={sys.flags.isolated} no_site={sys.flags.no_site}')
        prefix = sys.pycache_prefix
        if not prefix:
            raise RecordError('BYTECODE_NOT_ISOLATED', 'no pycache_prefix')
        prefix = Path(prefix).resolve()
        if prefix.is_relative_to(self.root) or self.root.is_relative_to(prefix):
            raise RecordError('BYTECODE_NOT_ISOLATED', f'pycache_prefix {prefix} meets the repository')

    def _repository(self, path):
        """The repository-relative path of an absolute path, or None outside the repository."""
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return None
        if not resolved.is_relative_to(self.root) or any(resolved.is_relative_to(d) for d in self.python_dirs):
            return None
        return resolved.relative_to(self.root).as_posix()

    def _hook(self, event, args):
        if event == 'open' and args and isinstance(args[0], (str, bytes)):
            path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0])
            try:
                # A relative path may be relative to a directory descriptor (`shutil.rmtree` opens entries so), not to
                # the working directory: it counts when it names a file of the repository from the working directory.
                if not path.is_absolute() and not path.resolve().is_file():
                    return
            except (OSError, ValueError):
                return
            inside = self._repository(path)
            if inside is not None:
                self.opened.add(inside)

    def modules(self):
        """{repository path: sha256} of every repository module loaded, with the `__init__.py` of every package on its
        path; refuses matching code and modules it cannot describe."""
        found = set()
        for name, module in list(sys.modules.items()):
            if name in MATCHING_ONLY:
                raise RecordError('MATCHING_CODE_LOADED', name)
            spec = getattr(module, '__spec__', None)
            origin = getattr(module, '__file__', None) or getattr(spec, 'origin', None)
            if origin and not Path(origin).is_absolute():  # 'built-in', 'frozen': no file of ours
                origin = None
            paths = [origin] if origin else [x for x in (getattr(module, '__path__', None) or []) if Path(x).is_absolute()]
            inside = [p for p in (self._repository(x) for x in paths) if p is not None]
            if not inside:
                continue
            if not origin or not origin.endswith('.py'):
                raise RecordError('UNRECORDED_MODULE', f'{name} ({origin or "no file"})')
            found.add(inside[0])
        for path in list(found):
            parent = Path(path).parent
            while parent != Path('.'):
                if (self.root / parent / '__init__.py').is_file():
                    found.add((parent / '__init__.py').as_posix())
                parent = parent.parent
        return {path: _digest(self.root / path) for path in sorted(found)}

    def _files(self, data_files):
        return {p: _digest(self.root / p) for p in sorted(set(data_files))}

    def start(self, data_files=()):
        """Note the modules loaded so far and the pinned files, to show at the end that none changed while the build
        ran (the synthetic generator, a pinned file, runs after this)."""
        self.first = {**self.modules(), **self._files(data_files)}

    def record(self, data_files, settings=None):
        """The record: modules, pinned data files, versions, the root's kind and the build's settings, after checking
        that every repository file the process opened is a recorded module's source or pinned, and that nothing noted
        at `start` changed."""
        modules = self.modules()
        files = self._files(data_files)
        if self.first is not None:
            now = {**modules, **files}
            changed = sorted(p for p, d in self.first.items() if now.get(p) != d)
            if changed:
                raise RecordError('FILE_CHANGED_DURING_BUILD', ', '.join(changed))
        outputs = [o for o in self.outputs if o.is_relative_to(self.root)]
        mine = lambda p: any((self.root / p).is_relative_to(o) for o in outputs)
        stray = sorted(p for p in self.opened if p not in modules and p not in files and not mine(p))
        if stray:
            raise RecordError('UNRECORDED_FILE_READ', ', '.join(stray))
        from . import _resources
        root = 'installed' if isinstance(_resources.ROOT, _resources.InstalledRoot) else 'checkout'
        out = {'rule': RULE, 'modules': modules, 'files': files, 'versions': _versions(), 'root': root}
        out['settings'] = dict(settings or {})
        return out


def environment(tmp):
    """The fixed, minimal environment a build process runs in: a plain `PATH` (for `ps`), UTC, the C locale, one thread
    for any library that reads a thread count from the environment, and `TMPDIR` and `HOME` in a fresh directory."""
    return {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TZ': 'UTC', 'LC_ALL': 'C', 'LANG': 'C',
            'TMPDIR': str(tmp), 'HOME': str(tmp), 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
            'MKL_NUM_THREADS': '1'}
