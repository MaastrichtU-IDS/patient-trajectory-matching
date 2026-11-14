"""Fingerprints of the code a build reaches (D33): what lets a report bind its build phase apart from the rest.

The scaling report (`tools/benchmark_execution_store.py`) builds snapshots of up to 1,000,000 patients and then queries
them. It bound `patterns/execution_store.py` by its whole digest, so any edit to matching made the whole report stale
and forced the builds again, though they could not have changed. A fingerprint binds the build by the source of the
definitions it reaches instead:

- *Roots.* The build's entry points, per module (for the scaling benchmark: its `build_worker`, and from there
  whatever it names in other modules, such as the store's `StoreBuilder`).
- *Closure within a module.* Every top-level definition (function, class, or assignment to a name) whose name a
  reached definition mentions, followed transitively. Methods, decorators and nested code are part of their
  definition. A name assigned more than once at top level contributes every assignment.
- *Across modules.* A reached definition that names another module of this repository reaches into it: `alias.name`
  (the module imported as `alias`, at top level or inside a function) or a name imported from it (`from ._resources
  import ROOT`) adds that name to the other module's roots; a module named without an attribute (passed as a value)
  adds every top-level name of it. This is followed to a fixed point, so each module gets one fingerprint over
  everything any build path reaches in it.
- *Data files.* A reached string constant that is the repository path of an existing file (the manifest schema, the
  synthetic generator) is listed, for the report to bind by its whole digest.
- *The digest.* Per module, sha256 over the rule, its sorted roots and each reached definition's source text, by name.
  A change to any reached definition changes it; a change to a definition no build reaches does not.

What it does not see, and why that is safe here. A name reached only through a string (`getattr`, `globals()`) would
not be followed, so `fingerprints` refuses reached code that names `getattr`, `globals`, `vars`, `eval`, `exec` or
`__import__` (`DYNAMIC_REFERENCE`). Third-party code (DuckDB, jsonschema) and the standard library are not
fingerprinted; the report binds the DuckDB version separately. The store's snapshot identity names the builder file's
whole digest (`builder_artifacts`), so a snapshot rebuilt after a matching-only edit gets a new identifier with the same
content digests; a report that reuses a build keeps the build's own identifier.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath

from ._resources import ROOT

RULE = 'build_closure_v1'
DYNAMIC = ('getattr', 'globals', 'eval', 'exec', 'vars', '__import__')
PACKAGES = ('patterns', 'tools')


class FingerprintError(ValueError):
    def __init__(self, code, where=''):
        super().__init__(f'{code}: {where}' if where else code)
        self.code = code


def _module_path(root, package, name):
    """The repository path of module `name` of `package` (dotted), or None outside this repository."""
    if package.split('.')[0] not in PACKAGES:
        return None
    path = '/'.join(package.split('.') + [name]) + '.py'
    return path if (Path(root) / path).is_file() else None


def _imports(node, path, root):
    """[(alias, module path, imported name or None)] for one import statement: a module imported whole (name None), or
    one name imported from a module."""
    out = []
    if isinstance(node, ast.ImportFrom):
        if node.level:
            parts = PurePosixPath(path).parent.parts[:len(PurePosixPath(path).parent.parts) - (node.level - 1)]
            package = '.'.join(parts + ((node.module,) if node.module else ()))
        else:
            package = node.module or ''
        for alias in node.names:
            whole = _module_path(root, package, alias.name)
            if whole is not None:
                out.append((alias.asname or alias.name, whole, None))
                continue
            head, _, last = package.rpartition('.')
            owner = _module_path(root, head, last) if last else None
            if owner is not None:
                out.append((alias.asname or alias.name, owner, alias.name))
    elif isinstance(node, ast.Import):
        for alias in node.names:
            head, _, last = alias.name.rpartition('.')
            whole = _module_path(root, head, last) if head else None
            if whole is not None and alias.asname:
                out.append((alias.asname, whole, None))
    return out


class _Module:
    def __init__(self, path, root):
        self.path, self.text = path, (Path(root) / path).read_text(encoding='utf-8')
        tree = ast.parse(self.text)
        self.defs, self.aliases = {}, {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.defs.setdefault(node.name, []).append(node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name):
                            self.defs.setdefault(name.id, []).append(node)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
                self.defs.setdefault(node.target.id, []).append(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias, module, name in _imports(node, path, root):
                    self.aliases[alias] = (module, name)

    def source(self, node):
        lines = self.text.splitlines(keepends=True)
        first = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
        return ''.join(lines[first - 1:node.end_lineno])


def fingerprints(roots, root=ROOT):
    """({module path: {'rule', 'roots', 'definitions', 'sha256'}}, [data file paths]) for the build entry points
    `roots` ({module path: [names]}), followed within and across this repository's modules to a fixed point."""
    modules, want, files = {}, {path: set(names) for path, names in roots.items()}, set()
    reached = {}
    changed = True
    while changed:
        changed = False
        for path in sorted(want):
            module = modules.get(path) or modules.setdefault(path, _Module(path, root))
            missing = sorted(n for n in want[path] if n not in module.defs)
            if missing:
                raise FingerprintError('UNKNOWN_ROOT', f'{path}: {", ".join(missing)}')
            seen = reached.setdefault(path, set())
            stack = [n for n in want[path] if n not in seen]
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                for node in module.defs[name]:
                    local = {}
                    for n in ast.walk(node):
                        if isinstance(n, (ast.Import, ast.ImportFrom)):
                            for alias, other, imported in _imports(n, path, root):
                                local[alias] = (other, imported)
                    for n in ast.walk(node):
                        if isinstance(n, ast.Name):
                            if n.id in DYNAMIC:
                                raise FingerprintError('DYNAMIC_REFERENCE', f'{path}: {name} names {n.id}')
                            if n.id in module.defs and n.id not in seen:
                                stack.append(n.id)
                        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and '/' in n.value \
                                and not n.value.startswith('/') and len(n.value) < 200 \
                                and (Path(root) / n.value).is_file():
                            files.add(n.value)
                    names = {**module.aliases, **local}
                    used = {}
                    for n in ast.walk(node):
                        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in names:
                            other, imported = names[n.value.id]
                            if imported is None:
                                used.setdefault(other, set()).add(n.attr)
                    for n in ast.walk(node):
                        if isinstance(n, ast.Name) and n.id in names:
                            other, imported = names[n.id]
                            if imported is not None:
                                used.setdefault(other, set()).add(imported)
                            elif not any(isinstance(p, ast.Attribute) and p.value is n for p in ast.walk(node)):
                                used.setdefault(other, set()).add('*')
                    for other, attrs in used.items():
                        if '*' in attrs:
                            attrs = (attrs - {'*'}) | set(modules.get(other) or modules.setdefault(
                                other, _Module(other, root)).defs)
                        mine = modules.get(other) or modules.setdefault(other, _Module(other, root))
                        attrs = {a for a in attrs if a in mine.defs}  # a submodule or re-export is not a definition
                        if not attrs <= want.setdefault(other, set()):
                            want[other] |= attrs
                            changed = True
    out = {}
    for path in sorted(want):
        if not want[path]:
            continue
        module = modules[path]
        names = sorted(reached[path])
        sources = [[name, [module.source(node) for node in module.defs[name]]] for name in names]
        digest = hashlib.sha256(json.dumps([RULE, sorted(want[path]), sources], ensure_ascii=False,
                                           separators=(',', ':')).encode('utf-8')).hexdigest()
        out[path] = {'rule': RULE, 'roots': sorted(want[path]), 'definitions': len(names), 'sha256': digest}
    return out, sorted(f for f in files if f not in out)


def fingerprint(path, roots, root=ROOT):
    """One module's fingerprint from the given roots, as the provenance gate recomputes it: the roots a report records
    for a module are already closed under every build path, so recomputing from them reaches the same definitions."""
    module = _Module(path, root)
    missing = sorted(n for n in roots if n not in module.defs)
    if missing:
        raise FingerprintError('UNKNOWN_ROOT', f'{path}: {", ".join(missing)}')
    seen, stack = set(), list(roots)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in module.defs[name]:
            for n in ast.walk(node):
                if isinstance(n, ast.Name):
                    if n.id in DYNAMIC:
                        raise FingerprintError('DYNAMIC_REFERENCE', f'{path}: {name} names {n.id}')
                    if n.id in module.defs and n.id not in seen:
                        stack.append(n.id)
    names = sorted(seen)
    sources = [[name, [module.source(node) for node in module.defs[name]]] for name in names]
    return hashlib.sha256(json.dumps([RULE, sorted(roots), sources], ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()
