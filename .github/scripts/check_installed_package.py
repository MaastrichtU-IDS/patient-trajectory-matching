"""Check the installed wheel behaves exactly like the checkout.

Run with ``python -I`` from outside the checkout, in a venv holding only the lock file and
the wheel:  python -I check_installed_package.py CHECKOUT WHEEL EXPECTED.json

1. The wheel's file list must match: the ``patterns/`` modules are derived from the checkout
   (tracked ``patterns/*.py`` without ``test_*`` and ``verify_*``, ``_resources.py``, the
   requirements files, ``reference_oracle.py``), so a new module needs no edit here; only the
   bundled contracts are a committed list. Every wheel file must also be byte-identical to
   its checkout source, with ``patterns/_contracts/...`` mapped back to ``schemas/`` and
   ``ontology/``.
2. Each probe runs twice: here, against the installed package, and in a child process whose
   working directory is the checkout, which imports the repository copy. The results must be
   identical, and the two runs must have imported from site-packages and the checkout
   respectively, so the comparison cannot pass vacuously.
"""
import json
import os
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

checkout, wheel, expected_path = (Path(a).resolve() for a in sys.argv[1:4])
problems = []

# 1. What the wheel contains.
with zipfile.ZipFile(wheel) as z:
    listed = sorted(n for n in z.namelist() if '.dist-info/' not in n)
    contents = {n: z.read(n) for n in listed}
tracked = subprocess.run(['git', 'ls-files', 'patterns/*.py'], cwd=checkout, capture_output=True,
                         text=True, check=True).stdout.split()
modules = {n for n in tracked if n.count('/') == 1 and not n.split('/')[1].startswith(('test_', 'verify_'))}
modules.add('patterns/_resources.py')   # may still be untracked when the check runs
modules.update(f'patterns/{p.name}' for p in (checkout / 'patterns').glob('requirements*.txt'))
modules.add('reference_oracle.py')
contracts = {n for n in json.loads(expected_path.read_text())['files'] if n.startswith('patterns/_contracts/')}
if not any(n.startswith('patterns/_contracts/schemas/') for n in contracts):
    problems.append('the committed contract list is empty or lacks schemas')
expected = modules | contracts
for name in sorted(expected - set(listed)):
    problems.append(f'wheel is missing {name}')
for name in sorted(set(listed) - expected):
    problems.append(f'wheel has unexpected {name}')
for name, data in contents.items():
    source = name.replace('patterns/_contracts/', '', 1) if name.startswith('patterns/_contracts/') else name
    path = checkout / source
    if not path.is_file():
        problems.append(f'{name}: no checkout source {source}')
    elif path.read_bytes() != data:
        problems.append(f'{name}: differs from {source}')

# 2. Probes. The same source runs in both places.
PROBE = r'''
import json, sys
from pathlib import Path
repo = Path(sys.argv[1])
import patterns, reference_oracle
out = {'_files': {'patterns': patterns.__file__, 'reference_oracle': reference_oracle.__file__}}

case = json.loads((repo / 'examples/cases.json').read_text())[0]
pattern = json.loads((repo / 'examples/exemplar.pattern.json').read_text())
taxonomy = json.loads((repo / 'ontology/toy-taxonomy.json').read_text())
out['oracle'] = reference_oracle.evaluate(case, pattern, taxonomy)

from patterns import trajectory_graph
out['trajectory_graph'] = trajectory_graph.run(
    json.loads((repo / 'examples/trajectory-graph/query.json').read_text()),
    json.loads((repo / 'examples/trajectory-graph/patients.json').read_text()))

from patterns import bounded_intervals
out['bounded_context_id'] = bounded_intervals.prepare(
    json.loads((repo / 'examples/bounded-interval/source.json').read_text())).context_id

from patterns import pro_solid
graph = pro_solid.build_graph(json.loads((repo / 'examples/pro-solid/source-rows.json').read_text()))
pro_solid.validate_graph(graph)   # ontology pin, disjointness checks and SHACL
case_rows, evidence = pro_solid.project(graph, json.loads((repo / 'examples/pro-solid/manifest.json').read_text()))
out['pro_solid'] = {'triples': len(graph), 'case': case_rows, 'evidence': evidence}
print(json.dumps(out, sort_keys=True))
'''


def run(label, cwd, isolated):
    cmd = [sys.executable] + (['-I'] if isolated else []) + ['-c', PROBE, str(checkout)]
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        problems.append(f'{label} probe failed:\n{done.stderr[-2000:]}')
        return None
    return json.loads(done.stdout.splitlines()[-1])


installed = run('installed', os.getcwd(), True)
repository = run('checkout', checkout, False)   # '' on sys.path, so the checkout wins
if installed and repository:
    site = Path(sysconfig.get_paths()['purelib']).resolve()
    for module in ('patterns', 'reference_oracle'):
        there = Path(installed['_files'][module]).resolve()
        here = Path(repository['_files'][module]).resolve()
        if site not in there.parents:
            problems.append(f'installed run imported {module} from {there}, not site-packages')
        if checkout not in here.parents or site in here.parents:
            problems.append(f'checkout run imported {module} from {here}, not the checkout')
    for key in sorted(k for k in repository if k != '_files'):
        same = installed.get(key) == repository[key]
        print(f'{key}: {"identical" if same else "DIFFERENT"}')
        if not same:
            problems.append(f'{key} differs between installed package and checkout')

for p in problems:
    print(f'::error::{p}')
print(f'installed package: {len(listed)} wheel files, {len(problems)} problem(s)')
sys.exit(1 if problems else 0)
