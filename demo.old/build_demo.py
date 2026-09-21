"""Build the portable fixture replay from the unchanged repository oracle."""
from pathlib import Path
import copy, hashlib, json, sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'source'))
from reference_oracle import evaluate

def read(path):
    return json.loads((ROOT / path).read_text())

cases = read('source/examples/cases.json')
pattern = read('source/examples/exemplar.pattern.json')
taxonomy = read('source/ontology/toy-taxonomy.json')
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
data = {
    'commit': 'adeeee27de3a1915d667cce9cec10ed559456df8',
    'cases': cases, 'pattern': pattern, 'taxonomy': taxonomy, 'runs': runs,
    'graphEvidence': read('evidence/pro-solid/evidence.json'),
    'graphCase': read('evidence/pro-solid/matcher-case.json'),
    'graphResult': read('evidence/pro-solid/match.json'),
    'graphTurtle': (ROOT / 'evidence/pro-solid/graph.ttl').read_text(),
    'semantic': read('evidence/semantic/result.json'),
    'oracle_sha256': hashlib.sha256((ROOT / 'source/reference_oracle.py').read_bytes()).hexdigest(),
}
(ROOT / 'demo-data.json').write_text(json.dumps(data, separators=(',', ':')))
template = (ROOT / 'ui.html').read_text()
packed = template.replace('/*DEMO_DATA*/null', json.dumps(data, separators=(',', ':')).replace('</', '<\\/'))
(ROOT / 'Patient_Trajectory_Demo.html').write_text(packed)
print(f'Built standalone replay: {len(runs) * len(cases)} oracle evaluations')
