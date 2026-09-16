"""Regenerate the portable replay using the parent repository's reference oracle."""
from pathlib import Path
import copy
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent
sys.path.insert(0, str(SOURCE))
from reference_oracle import evaluate


def read(path):
    return json.loads(path.read_text())


def main():
    cases = read(SOURCE / 'examples/cases.json')
    pattern = read(SOURCE / 'examples/exemplar.pattern.json')
    taxonomy = read(SOURCE / 'ontology/toy-taxonomy.json')
    semantic = read(ROOT / 'evidence/semantic/result.json')
    if semantic.get('status') != 'READY':
        raise ValueError('Rebuild requires a READY semantic example, not a failed pipeline report.')
    runs = {}
    for budget in ['0', '0.5', '1', '1.5', '2']:
        for count in range(3):
            for complete in [True, False]:
                key = f'{budget}:{count}:{int(complete)}'
                runs[key] = []
                for case in cases:
                    c = copy.deepcopy(case)
                    c['budget_override'] = {'max_total_cost': budget, 'max_relaxed_constraints': count}
                    c['source_search_complete'] = complete
                    runs[key].append(evaluate(c, pattern, taxonomy))
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
    data = {
        'commit': commit, 'cases': cases, 'pattern': pattern, 'taxonomy': taxonomy, 'runs': runs,
        'graphEvidence': read(ROOT / 'evidence/pro-solid/evidence.json'),
        'graphCase': read(ROOT / 'evidence/pro-solid/matcher-case.json'),
        'graphResult': read(ROOT / 'evidence/pro-solid/match.json'),
        'graphTurtle': (ROOT / 'evidence/pro-solid/graph.ttl').read_text(),
        'semantic': semantic,
        'oracle_sha256': hashlib.sha256((SOURCE / 'reference_oracle.py').read_bytes()).hexdigest(),
    }
    encoded = json.dumps(data, separators=(',', ':'))
    (ROOT / 'demo-data.json').write_text(encoded)
    template = (ROOT / 'ui.html').read_text()
    packed = template.replace('/*DEMO_DATA*/null', encoded.replace('</', '<\\/'))
    (ROOT / 'Patient_Trajectory_Demo.html').write_text(packed)
    print(f'Built standalone replay: {len(runs) * len(cases)} oracle evaluations')


if __name__ == '__main__':
    main()
