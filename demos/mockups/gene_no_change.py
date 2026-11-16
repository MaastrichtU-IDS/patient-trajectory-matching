"""D38's differential against main's builds: without a gene step, the wrapper changes nothing but identifiers.

    python demos/mockups/gene_no_change.py build-main COHORT.json ROOT        # prints the snapshot directory
    python demos/mockups/gene_no_change.py compare WRAPPER MAIN < cases.json  # a JSON summary

`build-main` builds the cohort as `store_parity.build` did before D38: the converter emitting series, with D36's period
label, and no gene reports. `compare` reads `{"cohort", "cases": [{"name", "query", "conditions", "windows"}]}` (the
parity test's form, minutes from birth) and runs every case without a gene step on both snapshots: through
`store_conditions_gene.match` on the snapshot `store_parity.build` makes now (WRAPPER), and through
`store_conditions_relative.match`, main's path, on MAIN. Every output field must be equal, except the snapshot's
identifier, the context and its identifier (which bind the snapshot) and the copy of the snapshot's labels (the wrapper
adds `gene_report_kinds`); the candidates must be equal too, and a refusal must be the same refusal. Synthetic data
only; the summary holds counts and case names.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from patterns import execution_store as es  # noqa: E402
from patterns import store_conditions_gene as gc  # noqa: E402
from patterns import store_conditions_relative as rc  # noqa: E402
from patterns import synthetic_ingest as si  # noqa: E402
import store_parity  # noqa: E402

IDENTIFIERS = ('snapshot_id', 'context', 'context_id', 'labels')


def build_main(cohort, root):
    """The cohort as main's `store_parity.build` built it (D36, D37): no gene reports, no gene label."""
    skipped = {}
    with es.StoreBuilder(root, si.synthetic_header(cohort)) as builder:
        for patient in si.iter_patients(cohort, skipped, series=True):
            builder.add(patient)
        return builder.finish(rc.synthetic_provenance(cohort, skipped, series=True))


def _gene(case):
    return any(isinstance(raw, dict) and 'gene' in raw for raw in ((case.get('conditions') or {}).get('steps') or {}).values())


def _answer(call):
    try:
        return call()
    except ValueError as error:  # a named refusal must be the same on both sides
        return {'refused': getattr(error, 'code', None) or str(error).split(':')[0]}


def compare(wrapper, main, cases, cohort):
    births = store_parity.birth_micros(cohort)
    summary = {'cases': 0, 'gene_cases_skipped': 0, 'results': 0, 'refused': 0, 'differences': []}
    with es.open_store(wrapper) as mine, es.open_store(main) as theirs:
        for case in cases:
            if _gene(case):
                summary['gene_cases_skipped'] += 1
                continue
            windows = {pid: None if w is None else {'start': births[pid] + w['start'] * store_parity.MINUTE,
                                                    'end': births[pid] + w['end'] * store_parity.MINUTE}
                       for pid, w in case['windows'].items()}
            query = store_parity.to_micros(case['query'])
            conditions = store_parity.conditions_micros(case.get('conditions'))
            a = _answer(lambda: gc.match(mine, query, windows, conditions=conditions))
            b = _answer(lambda: rc.match(theirs, query, windows, conditions=conditions))
            summary['cases'] += 1
            if 'refused' in a or 'refused' in b:
                summary['refused'] += 1
                if a != b:
                    summary['differences'].append({'case': case['name'], 'what': 'refusal', 'wrapper': a, 'main': b})
                continue
            strip = lambda out: {k: v for k, v in out.items() if k not in IDENTIFIERS}
            summary['results'] += len(a['results'])
            if json.dumps(strip(a)) != json.dumps(strip(b)):
                keys = sorted(k for k in set(a) | set(b) if k not in IDENTIFIERS and a.get(k) != b.get(k))
                patients = sorted(x['patient_id'] for x, y in zip(a['results'], b['results']) if x != y)
                summary['differences'].append({'case': case['name'], 'what': keys, 'patients': patients[:5]})
            if gc.candidates(mine, query, conditions) != rc.candidates(theirs, query, conditions):
                summary['differences'].append({'case': case['name'], 'what': 'candidates'})
    return summary


def main(argv):
    if argv[:1] == ['build-main'] and len(argv) == 3:
        print(build_main(json.loads(Path(argv[1]).read_text()), Path(argv[2])))
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
