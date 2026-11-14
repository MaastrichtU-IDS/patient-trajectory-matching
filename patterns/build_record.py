"""A runtime record of what a build loaded and read (D34): how a report binds its build phase apart from the rest.

The scaling report (`tools/benchmark_execution_store.py`) builds snapshots of up to 1,000,000 patients and then queries
them. A report bound by whole-file digests of everything goes stale when matching code changes, though the builds could
not have changed. So the build runs in a process of its own (`tools/scaling_build_worker.py`, in Python's isolated mode
with an explicit `sys.path`), and at its end this module records what that process actually used:

- *modules:* every module loaded from the repository, with every package `__init__.py` on its path, each by the
  sha256 of its whole file (rule `runtime_record_v1`);
- *files:* the data files the build may read (the caller's pinned list), each by whole-file digest. An audit hook
  sees every file the process opens; one inside the repository that is neither a recorded module (or its bytecode)
  nor pinned stops the build (`UNRECORDED_FILE_READ`);
- *versions:* the Python implementation and version, and the versions of DuckDB, pyarrow (if installed) and
  jsonschema;
- *root:* whether the repository was a checkout or an installed package (`patterns/_resources.py`).

Checks at runtime, each stopping the build by name:
- a module of the engine's matching code loaded (`MATCHING_CODE_LOADED`): the build must not import it at all;
- a repository module loaded that the record could not describe (`UNRECORDED_MODULE`: no file, or not a plain
  `.py` file);
- a recorded file that changed while the build ran (`FILE_CHANGED_DURING_BUILD`).

A record is not a static analysis: it says exactly which repository files ran or were read in this build, so a new
module, a package's `__init__` side effect or a module shadowing another on `sys.path` (inside the repository) is in it.
What it does not cover: code outside the repository beyond the versions recorded (library internals, the operating
system, the CPU), and a repository file the build reaches only on some input and not in the run recorded (the record
covers what this run loaded; the scaling benchmark's builds load the same modules at every size).
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


class Recorder:
    """Install in the build process before it imports the build's modules; `record()` at its end."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.opened = set()
        self.first = None
        self.outputs = []
        sys.addaudithook(self._hook)

    def ignore(self, *paths):
        """The build's own outputs (its work directory, its result and progress files): what it writes there is not
        something it reads from the repository, even when they lie inside it."""
        self.outputs.extend(Path(p).resolve() for p in paths)

    def _hook(self, event, args):
        if event == 'open' and args and isinstance(args[0], (str, bytes)) and not isinstance(args[0], int):
            path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0])
            try:
                # A relative path may be relative to a directory descriptor (`shutil.rmtree` opens entries so), not
                # to the working directory: it counts when it names a file of the repository from the working
                # directory, which is the repository's root in a build worker.
                resolved = path.resolve()
                if not path.is_absolute() and not resolved.is_file():
                    return
            except (OSError, ValueError):
                return
            if resolved.is_relative_to(self.root):
                self.opened.add(resolved.relative_to(self.root).as_posix())

    def _inside(self, path):
        try:
            return Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None

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
            inside = [p for p in (self._inside(x) for x in paths) if p is not None]
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

    def start(self):
        """Note the modules loaded so far, to show at the end that none changed while the build ran."""
        self.first = self.modules()

    def record(self, data_files):
        """The record: modules, pinned data files, versions and the root's kind, after checking that every repository
        file the process opened is a recorded module (or its bytecode) or pinned, and that no file noted at `start`
        changed."""
        modules = self.modules()
        if self.first is not None:
            changed = sorted(p for p, d in self.first.items() if modules.get(p) != d)
            if changed:
                raise RecordError('FILE_CHANGED_DURING_BUILD', ', '.join(changed))
        pinned = set(data_files)
        outputs = [o for o in self.outputs if o.is_relative_to(self.root)]
        mine = lambda p: any((self.root / p).is_relative_to(o) for o in outputs)
        stray = sorted(p for p in self.opened if p not in modules and p not in pinned
                       and '/__pycache__/' not in f'/{p}' and not mine(p))
        if stray:
            raise RecordError('UNRECORDED_FILE_READ', ', '.join(stray))
        from . import _resources
        root = 'installed' if isinstance(_resources.ROOT, _resources.InstalledRoot) else 'checkout'
        return {'rule': RULE, 'modules': modules, 'files': {p: _digest(self.root / p) for p in sorted(pinned)},
                'versions': _versions(), 'root': root}
