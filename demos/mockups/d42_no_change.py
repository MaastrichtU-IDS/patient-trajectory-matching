"""D42's differential against main's code: every profile but the joint one answers as main does, through the dispatcher.

    python demos/mockups/d42_no_change.py extract REF DIR                    # prints the commit extracted
    python demos/mockups/d42_no_change.py compare SNAPSHOT MAIN < cases.json  # a JSON summary

`extract` writes main's `patterns/`, `ontology/` and `schemas/` at the git ref REF into DIR (`git archive`), the code the
comparison runs as main's. `compare` reads `{"cohort", "cases": [{"name", "query", "conditions", "windows"}]}` (the
parity test's form, minutes from birth), converts each case to the store's clock with this tree's `store_parity.py`,
and answers every case whose profile is not the joint one (`execution-store-conditions-gene-coded-1.0`, which main
refuses) twice on the same snapshot: through `store_conditions.match` and `candidates` of this tree, and of MAIN, each in
a fresh interpreter with that tree's `patterns` first on its path. The two answers must be equal in every field except
the `context_id` and, in the context's artifacts, the digests of the two modules D42 edits
(`patterns/store_conditions_gene.py`, `patterns/store_conditions_coded.py`; a gene request's context gains the second's
key). Candidates must be equal, and a refusal must be the same refusal. Synthetic data only; the summary holds counts,
profiles and case names.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
TREE = HERE.parents[1]
JOINT = 'execution-store-conditions-gene-coded-1.0'
EDITED = ('patterns/store_conditions_gene.py', 'patterns/store_conditions_coded.py')


def extract(ref, root):
    """Main's code at `ref` into `root`; the commit it names."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    commit = subprocess.run(['git', 'rev-parse', ref], cwd=TREE, check=True, capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(['git', 'archive', '--format=tar', commit, 'patterns', 'ontology', 'schemas'], cwd=TREE,
                             check=True, capture_output=True).stdout
    subprocess.run(['tar', '-x', '-C', str(root)], input=archive, check=True)
    return commit


def answer(root, snapshot, requests, out):
    """Every request through `store_conditions` of the tree at `root` (run in its own interpreter): one JSON line per
    request, {name, answer | refused, candidates}."""
    sys.path.insert(0, str(Path(root).resolve()))
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    import patterns
    if not Path(patterns.__file__).resolve().is_relative_to(Path(root).resolve()):
        raise SystemExit(f'patterns imported from {patterns.__file__}, not {root}')
    with es.open_store(snapshot) as store, open(out, 'w') as sink:
        for r in json.loads(Path(requests).read_text()):
            line = {'name': r['name']}
            try:
                line['answer'] = sc.match(store, r['query'], r['windows'], conditions=r['conditions'])
                line['candidates'] = sc.candidates(store, r['query'], r['conditions'])
            except ValueError as error:  # a named refusal must be the same on both sides
                line = {'name': r['name'], 'refused': getattr(error, 'code', None) or str(error).split(':')[0]}
            sink.write(json.dumps(line) + '\n')


def strip(out):
    """An answer without what D42's edits move (see the docstring)."""
    out = {k: v for k, v in out.items() if k != 'context_id'}
    context = dict(out['context'])
    context['artifacts'] = {k: v for k, v in context['artifacts'].items() if k not in EDITED}
    out['context'] = context
    return out


def compare(snapshot, main_root, cases, cohort):
    sys.path.insert(0, str(HERE))
    import store_parity
    births = store_parity.birth_micros(cohort)
    requests, summary = [], {'cases': 0, 'joint_cases_skipped': 0, 'results': 0, 'refused': 0, 'by_profile': {},
                             'differences': []}
    for case in cases:
        conditions = store_parity.conditions_micros(case.get('conditions'))
        if (conditions or {}).get('profile') == JOINT:
            summary['joint_cases_skipped'] += 1
            continue
        windows = {pid: None if w is None else {'start': births[pid] + w['start'] * store_parity.MINUTE,
                                                'end': births[pid] + w['end'] * store_parity.MINUTE}
                   for pid, w in case['windows'].items()}
        requests.append({'name': case['name'], 'query': store_parity.to_micros(case['query']), 'conditions': conditions,
                         'windows': windows})
    with tempfile.TemporaryDirectory(prefix='d42-no-change-') as tmp:
        tmp = Path(tmp)
        (tmp / 'requests.json').write_text(json.dumps(requests))
        for side, root in (('tree', TREE), ('main', Path(main_root))):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), 'answer', str(root), str(snapshot),
                            str(tmp / 'requests.json'), str(tmp / f'{side}.jsonl')], check=True)
        mine = [json.loads(line) for line in (tmp / 'tree.jsonl').read_text().splitlines()]
        theirs = [json.loads(line) for line in (tmp / 'main.jsonl').read_text().splitlines()]
    for r, a, b in zip(requests, mine, theirs, strict=True):
        profile = (r['conditions'] or {}).get('profile') or 'none'
        summary['cases'] += 1
        summary['by_profile'][profile] = summary['by_profile'].get(profile, 0) + 1
        if 'refused' in a or 'refused' in b:
            summary['refused'] += 1
            if a != b:
                summary['differences'].append({'case': r['name'], 'what': 'refusal', 'tree': a.get('refused'),
                                               'main': b.get('refused')})
            continue
        summary['results'] += len(a['answer']['results'])
        x, y = strip(a['answer']), strip(b['answer'])
        if json.dumps(x) != json.dumps(y):
            keys = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
            summary['differences'].append({'case': r['name'], 'profile': profile, 'what': keys})
        if a['candidates'] != b['candidates']:
            summary['differences'].append({'case': r['name'], 'profile': profile, 'what': 'candidates'})
    return summary


def main(argv):
    if argv[:1] == ['extract'] and len(argv) == 3:
        print(extract(argv[1], argv[2]))
        return 0
    if argv[:1] == ['answer'] and len(argv) == 5:
        answer(*argv[1:])
        return 0
    if argv[:1] == ['compare'] and len(argv) == 3:
        data = json.load(sys.stdin)
        cohort = json.loads(Path(data['cohort']).read_text())
        print(json.dumps(compare(Path(argv[1]), Path(argv[2]), data['cases'], cohort)))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
