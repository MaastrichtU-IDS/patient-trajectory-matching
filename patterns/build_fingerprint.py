"""Fingerprints of the code a build reaches (D34): what lets a report bind its build phase apart from the rest.

The scaling report (`tools/benchmark_execution_store.py`) builds snapshots of up to 1,000,000 patients and then queries
them. It bound `patterns/execution_store.py` by its whole digest, so any edit to matching made the whole report stale
and forced the builds again, though they could not have changed. A fingerprint binds the build by the source of what
it reaches instead. Rule `build_closure_v1`, per module:

- *Roots.* The build's entry points, per module (`roots`): for the scaling benchmark, the build process's own entry
  point and the parent's code that measures and records a build.
- *Every top-level statement that is not a definition* is covered, whether reached or not: imports, expression
  statements, conditional, looping and `with` blocks, and assignments to anything but plain names (the module's
  docstring is left out). Each such statement runs when the module is imported, so it can change a build without any
  definition changing (`if True: BUCKETS = 9`, an import swapped for another). The names such a statement mentions are
  followed as roots, except inside an `if __name__ == '__main__':` block, which does not run when the module is
  imported (an entry script lists its `main` as a root).
- *Definitions* (a top-level function, class, or assignment to plain names) are covered when reached: when a root, a
  covered statement or a reached definition names them, transitively. A name assigned more than once contributes every
  assignment. Methods, decorators and nested code are part of their definition.
- *Across modules.* A covered node that names another module of this repository reaches into it: `alias.name` (the
  module imported as `alias`, at top level or inside a function) or a name imported from it (`from ._resources import
  ROOT`) adds that name to the other module's roots; a module named as a value adds every definition of it. This is
  followed to a fixed point, so each module gets one fingerprint over everything any build path reaches in it.
- *Data files.* A covered string constant that is the repository path of an existing file is reported (`files`), for
  the caller to check against the files it pins.
- *The digest.* Per module, sha256 over the rule, its sorted roots, every non-definition statement's source and each
  reached definition's source, by name.

Refused (`DYNAMIC_REFERENCE`), since a name reached through a string would not be followed: anywhere in covered code,
`getattr`, `setattr`, `delattr`, `globals`, `vars`, `eval`, `exec`, `__import__`, `importlib.import_module` and
`sys.modules`; and `__dict__` or `operator.attrgetter` where they could reach a module: `__dict__` of a module alias,
or `attrgetter(...)` applied to a module alias. On other objects they are allowed: the engine reads its own events with
`attrgetter` and builds them through `__dict__`, which reaches no module name.

Limits. *Re-exports:* a name module A imports from module B and module C then reads as `A.name` is followed into B only
through A's import statement (covered whole) and B's definition of it if a reached definition of B names it; the build's
modules re-export nothing (a test lists the imports followed). *Third-party code* (DuckDB, jsonschema) and the standard
library are not fingerprinted; the scaling report binds the DuckDB version separately. *Conservative:* an edit to any
non-definition statement changes the fingerprint even if only matching reads it. The store's snapshot identity names
the builder file's whole digest (`builder_artifacts`), so a snapshot rebuilt after a matching-only edit would get a new
identifier with the same content digests; a report that reuses a build keeps the build's own identifier.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath

from ._resources import ROOT

RULE = 'build_closure_v1'
DYNAMIC_NAMES = ('getattr', 'setattr', 'delattr', 'globals', 'vars', 'eval', 'exec', '__import__')
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
    one name imported from a module of this repository."""
    out = []
    if isinstance(node, ast.ImportFrom):
        if node.level:
            parts = PurePosixPath(path).parent.parts
            parts = parts[:len(parts) - (node.level - 1)]
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


def _is_definition(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True
    if isinstance(node, ast.Assign):
        return all(isinstance(n, (ast.Name, ast.Tuple, ast.List, ast.Store)) for t in node.targets for n in ast.walk(t))
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return isinstance(node.target, ast.Name)
    return False


def _is_main_guard(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name)
            and node.test.left.id == '__name__' and any(isinstance(c, ast.Constant) and c.value == '__main__'
                                                        for c in node.test.comparators))


class _Module:
    def __init__(self, path, root):
        self.path, self.text = path, (Path(root) / path).read_text(encoding='utf-8')
        tree = ast.parse(self.text)
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], 'value', None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]  # the module's docstring
        self.defs, self.aliases, self.statements = {}, {}, []
        for node in body:
            if _is_definition(node):
                targets = [node] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else (
                    node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.defs.setdefault(node.name, []).append(node)
                else:
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name):
                                self.defs.setdefault(name.id, []).append(node)
            else:
                self.statements.append(node)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias, module, name in _imports(node, path, root):
                        self.aliases[alias] = (module, name)

    def source(self, node):
        lines = self.text.splitlines(keepends=True)
        first = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
        return ''.join(lines[first - 1:node.end_lineno])


def _scan(module, node, root, follow=True):
    """(names of the module's definitions it mentions, {other module: names it reaches there}, data files) for one
    covered node, refusing dynamic references. With `follow` False (a `__main__` block) only refusals are checked."""
    where = f'{module.path}: line {node.lineno}'
    local = {}
    for n in ast.walk(node):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for alias, other, imported in _imports(n, module.path, root):
                local[alias] = (other, imported)
    aliases = {**module.aliases, **local}
    modules = {a for a, (_, imported) in aliases.items() if imported is None}
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in DYNAMIC_NAMES:
            raise FingerprintError('DYNAMIC_REFERENCE', f'{where} names {n.id}')
        if isinstance(n, ast.Attribute):
            if n.attr == 'import_module' or (n.attr == 'modules' and isinstance(n.value, ast.Name) and n.value.id == 'sys'):
                raise FingerprintError('DYNAMIC_REFERENCE', f'{where} names {n.attr}')
            if n.attr == '__dict__' and isinstance(n.value, ast.Name) and n.value.id in modules:
                raise FingerprintError('DYNAMIC_REFERENCE', f'{where} reads {n.value.id}.__dict__')
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Call):
            inner = n.func.func
            name = inner.id if isinstance(inner, ast.Name) else inner.attr if isinstance(inner, ast.Attribute) else None
            if name == 'attrgetter' and any(isinstance(a, ast.Name) and a.id in modules for a in n.args):
                raise FingerprintError('DYNAMIC_REFERENCE', f'{where} applies attrgetter to a module')
    if not follow:
        return set(), {}, set()
    names, used, files = set(), {}, set()
    attributed = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in aliases:
            other, imported = aliases[n.value.id]
            attributed.add(id(n.value))
            if imported is None:
                used.setdefault(other, set()).add(n.attr)
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            if n.id in module.defs:
                names.add(n.id)
            if n.id in aliases:
                other, imported = aliases[n.id]
                if imported is not None:
                    used.setdefault(other, set()).add(imported)
                elif id(n) not in attributed:
                    used.setdefault(other, set()).add('*')
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and '/' in n.value \
                and not n.value.startswith('/') and len(n.value) < 200 and (Path(root) / n.value).is_file():
            files.add(n.value)
    return names, used, files


def fingerprints(roots, root=ROOT):
    """({module path: {'rule', 'roots', 'definitions', 'statements', 'sha256'}}, [data file paths]) for the build entry
    points `roots` ({module path: [names]}), followed within and across this repository's modules to a fixed point."""
    modules, want, files, reached = {}, {path: set(names) for path, names in roots.items()}, set(), {}

    def get(path):
        if path not in modules:
            modules[path] = _Module(path, root)
        return modules[path]

    def reach(other, attrs):
        mine = get(other)
        if '*' in attrs:
            attrs = (attrs - {'*'}) | set(mine.defs)
        attrs = {a for a in attrs if a in mine.defs}  # a submodule or a re-export is not a definition here
        added = not attrs <= want.setdefault(other, set())
        want[other] |= attrs
        return added
    changed = True
    while changed:
        changed = False
        for path in sorted(want):
            module = get(path)
            missing = sorted(n for n in want[path] if n not in module.defs)
            if missing:
                raise FingerprintError('UNKNOWN_ROOT', f'{path}: {", ".join(missing)}')
            if path not in reached:  # its top-level statements: covered, and their names followed
                reached[path] = set()
                for node in module.statements:
                    names, used, found = _scan(module, node, root, follow=not _is_main_guard(node))
                    files |= found
                    want[path] |= names
                    for other, attrs in used.items():
                        changed |= reach(other, attrs)
            seen = reached[path]
            stack = [n for n in want[path] if n not in seen]
            while stack:
                name = stack.pop()
                if name in seen:
                    continue
                seen.add(name)
                for node in module.defs[name]:
                    names, used, found = _scan(module, node, root)
                    files |= found
                    stack.extend(n for n in names if n not in seen)
                    want[path] |= names
                    for other, attrs in used.items():
                        changed |= reach(other, attrs)
    out = {}
    for path in sorted(want):
        module = modules[path]
        names = sorted(reached[path])
        statements = [module.source(node) for node in module.statements]
        sources = [[name, [module.source(node) for node in module.defs[name]]] for name in names]
        digest = hashlib.sha256(json.dumps([RULE, sorted(want[path]), statements, sources], ensure_ascii=False,
                                           separators=(',', ':')).encode('utf-8')).hexdigest()
        out[path] = {'rule': RULE, 'roots': sorted(want[path]), 'definitions': len(names),
                     'statements': len(statements), 'sha256': digest}
    return out, sorted(f for f in files if f not in out)
