"""Check the installed wheel behaves exactly like the checkout.

Run with ``python -I`` from outside the checkout, in a venv holding only the lock file and
the wheel:  python -I check_installed_package.py CHECKOUT WHEEL EXPECTED.json

Each probe runs twice: here, against the installed package, and in a child process whose
working directory is the checkout, which imports the repository copy. The results must be
identical. The wheel's file list must equal the committed expected list, so a missing
contract or a leaked test fails.
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

# 1. Where the code came from, and what the wheel contains.
import patterns, reference_oracle
site = Path(sysconfig.get_paths()['purelib']).resolve()
for module in (patterns, reference_oracle):
    if site not in Path(module.__file__).resolve().parents:
        problems.append(f'{module.__name__} imported from {module.__file__}, not site-packages')
with zipfile.ZipFile(wheel) as z:
    listed = sorted(n for n in z.namelist() if '.dist-info/' not in n)
expected = json.loads(expected_path.read_text())['files']
for name in sorted(set(expected) - set(listed)):
    problems.append(f'wheel is missing {name}')
for name in sorted(set(listed) - set(expected)):
    problems.append(f'wheel has unexpected {name}')

# 2. Probes. The same source runs in both places.
PROBE = r'''
import json, sys
from pathlib import Path
repo = Path(sys.argv[1])
out = {}

import reference_oracle
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
    for key in sorted(repository):
        same = installed.get(key) == repository[key]
        print(f'{key}: {"identical" if same else "DIFFERENT"}')
        if not same:
            problems.append(f'{key} differs between installed package and checkout')

for p in problems:
    print(f'::error::{p}')
print(f'installed package: {len(listed)} wheel files, {len(problems)} problem(s)')
sys.exit(1 if problems else 0)
