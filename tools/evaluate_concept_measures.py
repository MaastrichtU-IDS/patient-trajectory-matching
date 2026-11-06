"""Retrieval benchmark for the concept measures, with the diagnosis hidden.

    python -m tools.evaluate_concept_measures [--cohort PATH] [--output PATH | --check PATH]

Each patient of the synthetic paediatric cohort (demos/mockups/synthetic/generate.py) is
a query in turn. Its profile is what the record says about it, not the generator's truth:
clinical findings from coded problems, registry entries and letters that state them about
the patient, and, on a second axis, medicines. Diagnoses and genetic results are hidden,
since they would nearly name the answer. Every other patient is ranked by each concept
measure of patterns/patient_similarity.py, computed by its own functions, with IC from
the cohort. A patient is relevant when the generator put it in the query's condition
group. Mean average precision, precision at 5, recall at 10 and reciprocal rank are
averaged over queries, beside what a random ranking would score.

The labels are the generator's, and the cohort is small, invented and built to separate
its groups. The benchmark compares measures under one protocol; it says nothing about
clinical performance.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from patterns.patient_similarity import CONCEPT_MEASURES, concept_distance, information_content_table  # noqa: E402

COHORT = 'demos/mockups/data/synthetic-paediatric-rare.json'
GENERATOR = 'demos/mockups/synthetic/generate.py'
ARTIFACTS = ('tools/evaluate_concept_measures.py', 'patterns/patient_similarity.py', GENERATOR)
CODED = ('condition', 'registry_entry', 'interpretation', 'dispensing', 'medication_order')
AXES = {'findings': ('phenotype',), 'findings_and_medications': ('phenotype', 'medication')}
K_PRECISION, K_RECALL = 5, 10


def sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def load_cohort(path=None):
    """The committed generator's output; generated into a temporary file when absent."""
    path = Path(path) if path else ROOT / COHORT
    if not path.exists():
        path = Path(tempfile.mkdtemp()) / 'cohort.json'
        subprocess.run([sys.executable, str(ROOT / GENERATOR), '--output', str(path)], check=True,
                       capture_output=True)
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def catalogue_for(concepts, kinds):
    """Each concept of the axis with its ancestors: parents, transitively, and drug classes."""
    def ancestors(cid):
        out, stack = set(), list(concepts[cid]['parents']) + list(concepts[cid].get('classes', []))
        while stack:
            c = stack.pop()
            if c not in out:
                out.add(c)
                stack.extend(concepts[c]['parents'] + concepts[c].get('classes', []))
        return sorted(out)
    return {cid: ancestors(cid) for cid, c in concepts.items() if c['kind'] in kinds}


def recorded_concepts(patient, concepts, codes, kinds):
    """What the record states: coded entries, and mentions asserted present of the patient."""
    found = set()
    for r in patient['records']:
        if r['kind'] in CODED and 'coding' in r:
            cid = codes.get((r['coding']['system'], r['coding']['code']))
            if cid and concepts[cid]['kind'] in kinds:
                found.add(cid)
        for m in r.get('mentions', []):
            if m['assertion'] == 'present' and m['experiencer'] == 'patient' and concepts.get(m['concept'], {}).get('kind') in kinds:
                found.add(m['concept'])
    return found


def average_precision(ranked, relevant):
    hits, total = 0, Fraction(0)
    for i, pid in enumerate(ranked, 1):
        if pid in relevant:
            hits += 1
            total += Fraction(hits, i)
    return total / len(relevant) if relevant else None


def metrics(ranked, relevant):
    first = next((i for i, pid in enumerate(ranked, 1) if pid in relevant), None)
    return {'average_precision': average_precision(ranked, relevant),
            'precision_at_5': Fraction(sum(p in relevant for p in ranked[:K_PRECISION]), K_PRECISION),
            'recall_at_10': Fraction(sum(p in relevant for p in ranked[:K_RECALL]), len(relevant)),
            'reciprocal_rank': Fraction(1, first) if first else Fraction(0)}


def rank(query, others, profiles, ic, catalogue, measure):
    """Nearest first; an undefined distance ranks last; ties by patient ID."""
    def key(pid):
        distance, _ = concept_distance(ic, catalogue, profiles[query], profiles[pid], measure)
        return (distance is None, distance if distance is not None else 0, pid)
    return sorted(others, key=key)


def evaluate_cohort(cohort, axes=AXES, measures=CONCEPT_MEASURES):
    concepts = cohort['concepts']
    codes = {(c['system'], c['code']): cid for cid, x in concepts.items() for c in x['codings']}
    patients = sorted(cohort['patients'], key=lambda p: p['patient_id'])
    group = {p['patient_id']: p['truth']['condition_group'] for p in patients}
    pids = list(group)
    out = {}
    for axis, kinds in axes.items():
        catalogue = catalogue_for(concepts, kinds)
        recorded = {p['patient_id']: recorded_concepts(p, concepts, codes, kinds) for p in patients}
        expanded = {pid: {t for c in cs for t in (c, *catalogue[c])} for pid, cs in recorded.items()}
        table, _, _ = information_content_table(catalogue, list(expanded.values()))
        ic = {c: Fraction(v) for c, v in table.items()}
        profiles = {pid: (expanded[pid], recorded[pid]) for pid in pids}
        queries = [pid for pid in pids if sum(group[o] == group[pid] for o in pids) > 1]
        results, per_query = {}, {}
        for measure in measures:
            scores = []
            for q in queries:
                relevant = {o for o in pids if o != q and group[o] == group[q]}
                scores.append(metrics(rank(q, [o for o in pids if o != q], profiles, ic, catalogue, measure), relevant))
            results[measure] = {name: sum(s[name] for s in scores) / len(scores) for name in scores[0]}
            per_query[measure] = [s['average_precision'] for s in scores]
        # Paired by query against ancestor-Jaccard: an average alone hides how often a
        # measure helps and how often it hurts, which on 48 queries is most of the signal.
        baseline = per_query.get('ancestor_jaccard')
        paired = {m: {'better': sum(x > y for x, y in zip(v, baseline)), 'worse': sum(x < y for x, y in zip(v, baseline)),
                      'tied': sum(x == y for x, y in zip(v, baseline))}
                  for m, v in per_query.items() if baseline is not None and m != 'ancestor_jaccard'}
        others = len(pids) - 1
        chance = {'precision_at_5': sum(Fraction(sum(group[o] == group[q] for o in pids) - 1, others) for q in queries) / len(queries),
                  'recall_at_10': Fraction(K_RECALL, others)}
        out[axis] = {'queries': len(queries), 'empty_profiles': sum(not recorded[p] for p in pids),
                     'measures': results, 'chance': chance, 'average_precision_vs_ancestor_jaccard': paired}
    return out


def report(cohort_path=None):
    cohort, cohort_sha = load_cohort(cohort_path)
    groups = {}
    for p in cohort['patients']:
        groups[p['truth']['condition_group']] = groups.get(p['truth']['condition_group'], 0) + 1
    decimal = lambda f: f'{float(f):.4f}'
    axes = evaluate_cohort(cohort)
    for axis in axes.values():
        axis['measures'] = {m: {k: decimal(v) for k, v in s.items()} for m, s in axis['measures'].items()}
        axis['chance'] = {k: decimal(v) for k, v in axis['chance'].items()}
    return {
        'schema': 'concept-measure-benchmark-1',
        'clinical_validity_claim': False,
        'label_origin': 'synthetic_generator_ground_truth',
        'cohort': {'generator': cohort['generator'], 'dataset_sha256': cohort_sha,
                   'patients': len(cohort['patients']), 'condition_groups': dict(sorted(groups.items()))},
        'protocol': {
            'query_profile': 'recorded concepts only: coded problems, registry entries, interpretations, dispensings, '
                             'orders, and letter mentions asserted present of the patient; diagnoses and genetic results hidden',
            'relevance': 'same generator condition group',
            'ranking': 'every other patient, nearest first; undefined distance last; ties by patient ID',
            'information_content': 'patterns.patient_similarity.information_content_table over the whole cohort',
            'measures': list(CONCEPT_MEASURES)},
        'axes': axes,
        'artifacts': {path: sha256(path) for path in ARTIFACTS},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--cohort', help=f'cohort JSON (default {COHORT}, generated if absent)')
    out = parser.add_mutually_exclusive_group()
    out.add_argument('--output', help='write the report here')
    out.add_argument('--check', help='fail unless this committed report is reproduced')
    args = parser.parse_args(argv)
    value = report(args.cohort)
    text = json.dumps(value, indent=2, sort_keys=True) + '\n'
    if args.check:
        committed = Path(args.check).read_text()
        if committed != text:
            print(f'{args.check} is stale: rerun with --output {args.check}', file=sys.stderr)
            return 1
        print(f'{args.check} reproduced')
        return 0
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
