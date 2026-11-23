#!/usr/bin/env python3
"""E0's and E0b's measurements: letter mentions timed from their letter's text (`normalised_v1`), scored against the
truth.

    python tools/e0_measure.py soundness [--committed] [--seeds 5201-5300] [--defect NAME] [--output FILE]
    python tools/e0_measure.py defects [--committed] [--seeds 5201-5230] [--output FILE]
    python tools/e0_measure.py arms [--seeds 5201-5230] [--extend 5231-5300] [--output FILE]

E0b (decision log): the generator reused truth event ids, so a mention's truth id could name several events. The
scorer and the oracle T name a mention's truth event by its concept among them (`truth_event`), and refuse by name when
they cannot (`REFUSALS`); a refused narrowed mention is listed and left out of rule 1, at most `MAX_EXCLUDED_SHARE` of
the narrowed ones. E0 read the truth by id alone, which is why its rule 1 missed four times. Since D54 the generator's
ids are unique, so an id names one event; `truth_event` keeps the rule as a guard.

This tool scores; it is the only E0 code that reads the generator's truth. The normaliser (`patterns/mention_time.py`)
and the wrapper (`patterns/synthetic_mentions.py`) never do.

- `soundness` (rules 1 and 2): every present mention about the patient converted under `normalised_v1`, against its
  truth event's day (`truth_event`; D30's invariant, `test_execution_store`: the truth is a day). A narrowed range that
  does not meet
  that day is a miss. Reported: mentions narrowed by rule, fallbacks by reason, the days each range covers (median and
  90th percentile) beside D30's, and whether each annotated time expression is a verbatim substring of the mention's
  clause and the very expression the normaliser read.
- `defects` (rule 6): `soundness` again under each planted defect of `mention_time.DEFECTS`.
- `arms` (rules 3 to 5): per cohort three stores, each built as `store_parity.py build` builds the page's store (the
  converter emitting series, through D38's gene wrapper):
  - **G**, today's: `store_parity.build` itself (D30's timing);
  - **GT**, the same through `synthetic_mentions` with `normalised_v1`;
  - **T**, a mention-truth oracle: GT with each mention's start set to its truth event's day (within GT's range). It
    exists only here, built from the truth; no page or build loads it.
  Each of the page's templates (`demos/mockups/e0_requests.cjs`: stated findings and coded only, the page's windows)
  is matched in each store through `store_conditions.match` (the page server's entry point). Reported per template and
  arm, under the owner's counting rule: candidates (a store candidate and an index window), distinct child types (PLAN
  keys; C001 is `reference`) unresolved, all candidates and the count without C001, status mixes, `exact` results, and
  G's statuses against GT's. Checked: G's worlds contain GT's, which contain T's, so every decided claim of a larger arm
  must hold in a smaller one (`transition_ok`), with the claims checked counted by type; no result moves away from
  decided from G to GT and at least one moves toward it (`decided`); and under coded only every arm's results are
  identical (the sha256 of each case's full per-patient results, bindings and witnesses included, and its candidate
  list).

Synthetic data only; the reports hold counts and, for a miss or a contradiction, record and patient identifiers of
generated cohorts (no patient data).
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / 'demos/mockups', ROOT / 'demos/mockups/synthetic'):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from patterns import execution_store as es  # noqa: E402
from patterns import mention_time as mt  # noqa: E402
from patterns import store_conditions as sc  # noqa: E402
from patterns import store_conditions_gene as gc  # noqa: E402
from patterns import synthetic_ingest as si  # noqa: E402
from patterns import synthetic_mentions as sm  # noqa: E402

GENERATOR = ROOT / 'demos/mockups/synthetic/generate.py'
REQUESTS = ROOT / 'demos/mockups/e0_requests.cjs'
DAY = 86_400_000_000
MINUTE = 60_000_000
COMMITTED = 'committed'  # the generator's default seed, the page's cohort
# E0b's fresh seeds (E0's 5001 to 5100 are not reused).
SOUNDNESS_SEEDS = tuple(range(5201, 5301))
ARM_SEEDS = tuple(range(5201, 5231))
ARM_EXTEND = tuple(range(5231, 5301))
DEFECT_SEEDS = tuple(range(5201, 5231))
REFUSALS = ('TRUTH_EVENT_MISSING', 'TRUTH_EVENT_CONCEPT_NONE', 'TRUTH_EVENT_AMBIGUOUS')
# E0b rule 1: narrowed mentions the scorer refuses are listed and left out, at most this share of the narrowed ones.
MAX_EXCLUDED_SHARE = 0.001
RULED_DEFECTS = mt.DEFECTS
MINIMUM, SHARE = 30, 0.3
STATED = ('sequence, stated findings', 'tree, stated findings', 'graph, stated findings', 'blank, stated findings')
CODED = ('sequence, coded only (1.3)', 'tree, coded only (joint)', 'graph, coded only (joint)',
         'event frequency, coded only (1.3)', 'blank, coded only (1.3)')
EXPECTED_REFUSALS = {'event frequency, stated findings': 'FREE_TEXT_COUNT_UNSUPPORTED'}
NO_CLAIM = ('unresolved', 'not_comparable')
CLAIM_TYPES = ('exact', 'complete_none', 'relaxed')
ARTIFACTS = ('tools/e0_measure.py', 'demos/mockups/e0_requests.cjs', 'patterns/mention_time.py',
             'patterns/synthetic_mentions.py', 'patterns/synthetic_ingest.py', 'patterns/store_conditions_gene.py',
             'demos/mockups/store_parity.py', 'demos/mockups/synthetic/generate.py', 'demos/mockups/shared/core.js',
             'demos/mockups/trajectory/app.js', 'demos/mockups/trajectory/store_request.js',
             'patterns/execution_store.py', 'patterns/store_conditions.py', 'patterns/trajectory_graph.py')


def artifacts():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in ARTIFACTS}


def seeds_of(text):
    """'5001-5030' or '5001,5003' as a tuple of ints."""
    out = []
    for part in text.split(','):
        lo, _, hi = part.partition('-')
        out.extend(range(int(lo), int(hi or lo) + 1))
    return tuple(out)


def generate(seed, path):
    """Generate a cohort (`COMMITTED`: the generator's default seed) into `path`; returns the cohort."""
    args = [sys.executable, str(GENERATOR), '--output', str(path)] + ([] if seed == COMMITTED else ['--seed', str(seed)])
    subprocess.run(args, check=True, capture_output=True, cwd=ROOT)
    return json.loads(Path(path).read_text(encoding='utf-8'))


@contextmanager
def defect(name):
    before = mt._DEFECT
    mt._DEFECT = name
    try:
        yield
    finally:
        mt._DEFECT = before


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def days_covered(start):
    """Days a start range covers, a range from a day's 00:00 to another's 23:59 counted in whole days."""
    return (start[1] - start[0] + MINUTE) / DAY


class Refused(Exception):
    """The scorer cannot name a mention's truth event (E0b): `code` is one of `REFUSALS`, `candidates` the events that
    share the mention's truth id."""

    def __init__(self, code, candidates=()):
        super().__init__(code)
        self.code, self.candidates = code, list(candidates)


def carries(event, concept, ancestors):
    """Whether a truth event carries a mention's concept (E0b): the event's concept is the mention's or one of its
    ancestors, or, for a gene concept `gene:G`, the event is a genetic test of gene G (the generator's genetic truth
    events have concept `genetic_test` and the gene as an attribute)."""
    if event['concept'] == concept or event['concept'] in ancestors.get(concept, ()):
        return True
    return concept.startswith('gene:') and event.get('kind') == 'genetic' and event.get('gene') == concept[5:]


def truth_event(patient, record, i, ancestors):
    """The truth event of a letter's i-th mention (E0b). An id that names one event names that event, whatever its
    concept (the generator links, for one, its French letter's febrile status epilepticus to a febrile seizure). Before
    D54 the generator reused ids, so an id could name several events: then the one that carries the mention's concept
    (kept as a guard; unique ids never reach it). Refused by
    name (`Refused`) when no event has the id (TRUTH_EVENT_MISSING), or, among several, none carries the concept
    (TRUTH_EVENT_CONCEPT_NONE) or several do (TRUTH_EVENT_AMBIGUOUS)."""
    m = record['mentions'][i]
    same = [e for e in patient['truth']['events'] if e['event_id'] == m['truth_event_id']]
    if not same:
        raise Refused('TRUTH_EVENT_MISSING')
    if len(same) == 1:
        return same[0]
    found = [e for e in same if carries(e, m['concept'], ancestors)]
    if not found:
        raise Refused('TRUTH_EVENT_CONCEPT_NONE', same)
    if len(found) > 1:
        raise Refused('TRUTH_EVENT_AMBIGUOUS', found)
    return found[0]


def day_bounds(patient, day):
    """The start and end, on the store's clock, of the patient's day `day` (D30's test: the truth is a day)."""
    birth = datetime.fromisoformat(patient['birth_date'][:10] + 'T00:00')
    lo = si.micros((birth + timedelta(days=day)).isoformat(timespec='minutes'))
    return lo, lo + DAY - 1


def truth_day(patient, record, i, ancestors):
    """The true day of a letter's i-th mention, on the store's clock (`truth_event`; may raise `Refused`)."""
    return day_bounds(patient, truth_event(patient, record, i, ancestors)['day'])


def score_cohort(cohort, name):
    """Rules 1 and 2 on one cohort: (summary, misses, widths)."""
    counts, skipped = {}, {}
    out = Counter()
    misses, excluded, widths = [], [], {'d30': [], 'normalised': [], 'd30_of_narrowed': [], 'narrowed': []}
    rules = Counter()
    ancestors = si.ancestor_closure(cohort['concepts'])
    for patient, converted in zip(sorted(cohort['patients'], key=lambda p: p['patient_id']),
                                  sm.iter_patients(cohort, skipped, sm.NORMALISED, counts=counts)):
        records = {r['record_id']: r for r in patient['records']}
        floor = si.earliest(patient)
        for event in converted['events']:
            if event['kind'] != 'mention':
                continue
            record_id, i = event['event_id'].split('/', 1)[1].split('#m')
            record, i = records[record_id], int(i)
            d30 = [floor, si.micros(record['occurred']['latest'])]
            start = event['start']
            out['mentions'] += 1
            timing = event['evidence'].get('mention_time')
            try:
                lo, hi = truth_day(patient, record, i, ancestors)
            except Refused as refused:
                # E0b: a mention whose truth event the scorer cannot name is counted by reason and, if narrowed, listed
                # and left out of rule 1 explicitly, with whether its range meets each candidate's day.
                out[f'refused_{refused.code}'] += 1
                lo = hi = None
                if timing:
                    out['excluded_narrowed'] += 1
                    meets = [start[0] <= b and a <= start[1] for a, b in (day_bounds(patient, e['day'])
                                                                          for e in refused.candidates)]
                    excluded.append({'cohort': name, 'event_id': event['event_id'], 'refusal': refused.code,
                                     'rule': timing['rule'], 'candidates': [
                                         {'concept': e['concept'], 'kind': e['kind'], 'meets': ok}
                                         for e, ok in zip(refused.candidates, meets)]})
            widths['d30'].append(days_covered(d30))
            widths['normalised'].append(days_covered(start))
            if timing:
                out['narrowed'] += 1
                rules[timing['rule']] += 1
                widths['narrowed'].append(days_covered(start))
                widths['d30_of_narrowed'].append(days_covered(d30))
                if lo is not None and not (start[0] <= hi and lo <= start[1]):
                    out['misses'] += 1
                    misses.append({'cohort': name, 'event_id': event['event_id'], 'rule': timing['rule'],
                                   'expression': timing['expression'], 'days_outside': round(
                                       ((start[0] - hi) if start[0] > hi else (lo - start[1])) / DAY, 2)})
            if lo is not None and not (d30[0] <= hi and lo <= d30[1]):
                out['d30_misses'] += 1
            m = record['mentions'][i]
            if m.get('time_expression'):
                out['annotated_time_expressions'] += 1
                a, b = mt.clause(record['text'], m['span'])
                out['annotated_verbatim_in_clause'] += m['time_expression'] in record['text'][a:b]
                out['annotated_read_by_normaliser'] += bool(timing) and timing['expression'] == m['time_expression']
    summary = {**dict(out), 'narrowed_by_rule': dict(sorted(rules.items())),
               'wrapper_counts': dict(sorted(counts.items()))}
    return summary, misses, widths, excluded


def soundness_run(cohorts, defect_name=None):
    """Rules 1 and 2 pooled over cohorts [(name, seed)]; each cohort generated into a scratch directory."""
    pooled, per, all_misses, all_excluded = Counter(), {}, [], []
    widths = {'d30': [], 'normalised': [], 'd30_of_narrowed': [], 'narrowed': []}
    rules, wrapper = Counter(), Counter()
    with tempfile.TemporaryDirectory(prefix='e0-soundness-') as tmp, defect(defect_name):
        for name in cohorts:
            cohort = generate(name, Path(tmp) / 'cohort.json')
            summary, misses, w, excluded = score_cohort(cohort, str(name))
            per[str(name)] = {k: v for k, v in summary.items() if k in ('mentions', 'narrowed', 'misses', 'excluded_narrowed')}
            for k, v in summary.items():
                if isinstance(v, int):
                    pooled[k] += v
            rules.update(summary['narrowed_by_rule'])
            wrapper.update(summary['wrapper_counts'])
            all_misses.extend(misses)
            all_excluded.extend(excluded)
            for k in widths:
                widths[k].extend(w[k])
    stats = {k: {'median_days': statistics.median(v) if v else None, 'p90_days': _quantile(v, 0.9), 'n': len(v)}
             for k, v in widths.items()}
    return {'cohorts': [str(c) for c in cohorts], 'defect': defect_name, 'pooled': dict(pooled),
            'narrowed_share': pooled['narrowed'] / pooled['mentions'] if pooled['mentions'] else None,
            'narrowed_by_rule': dict(sorted(rules.items())), 'wrapper_counts': dict(sorted(wrapper.items())),
            'widths': stats, 'misses': all_misses[:200], 'misses_total': len(all_misses),
            'refusals': {k[len('refused_'):]: v for k, v in sorted(pooled.items()) if k.startswith('refused_')},
            'excluded': all_excluded, 'excluded_total': len(all_excluded),
            'excluded_bound': MAX_EXCLUDED_SHARE * pooled['narrowed'],
            'met': pooled['misses'] == 0 and len(all_excluded) <= MAX_EXCLUDED_SHARE * pooled['narrowed'],
            'per_cohort': per}


def cohorts_of(args, default):
    out = [COMMITTED] if args.committed else []
    return out + list(seeds_of(args.seeds) if args.seeds else default)


def soundness(args):
    report = soundness_run(cohorts_of(args, SOUNDNESS_SEEDS), args.defect)
    report.update(profile='e0b-mention-time-soundness-1.0', rule=(
        'E0b rules 1 and 2: every present letter mention about the patient converted under normalised_v1 '
        '(patterns/synthetic_mentions.py); a narrowed range that does not meet its truth event\'s day (the event with the '
        'mention\'s truth id that carries its concept) is a miss; a narrowed mention whose truth event the scorer refuses '
        'by name is listed and left out (rule 1: 0 misses, and at most 0.1% of narrowed mentions left out); the share narrowed, the days each range covers (median, 90th percentile) beside D30\'s, and the '
        'annotated time expressions found verbatim in the clause and read by the normaliser are reported (rule 2)'),
                  artifacts=artifacts(), contains_patient_level_data=False)
    write(args.output, report)
    print(f"mentions {report['pooled'].get('mentions')}, narrowed {report['pooled'].get('narrowed')}, "
          f"misses {report['misses_total']}; median days D30 {report['widths']['d30']['median_days']} -> "
          f"{report['widths']['normalised']['median_days']}", file=sys.stderr)


def defects(args):
    cohorts = cohorts_of(args, DEFECT_SEEDS)
    found = {}
    for name in mt.DEFECTS:
        r = soundness_run(cohorts, name)
        found[name] = {'misses': r['misses_total'], 'caught': r['misses_total'] > 0, 'ruled': name in RULED_DEFECTS,
                       'example': r['misses'][:3]}
        print(f'{name}: {r["misses_total"]} misses', file=sys.stderr)
    write(args.output, {'profile': 'e0b-mention-time-defects-1.0', 'cohorts': [str(c) for c in cohorts],
                        'rule': 'E0b rule 6: each ruled planted defect gives at least one miss of rule 1',
                        'defects': found, 'met': all(found[n]['caught'] for n in RULED_DEFECTS),
                        'artifacts': artifacts(), 'contains_patient_level_data': False})


# Arms.

def build_arm(cohort, root, arm, refusals=None):
    """The snapshot directory of one arm (G, GT or T) of a cohort."""
    import store_parity
    if arm == 'G':
        return store_parity.build(cohort, root)
    skipped, counts = {}, {}
    patients = list(sm.iter_patients(cohort, skipped, sm.NORMALISED, series=True, base=gc, counts=counts))
    provenance = sm.synthetic_provenance(cohort, skipped, sm.NORMALISED, series=True, base=gc, counts=counts)
    if arm == 'T':
        patients = oracle_patients(cohort, patients, refusals)
        provenance = {**provenance, 'mention_time': 'E0 mention-truth oracle: each mention at its truth event\'s day'}
    with es.StoreBuilder(root, si.synthetic_header(cohort)) as builder:
        for p in patients:
            builder.add(p)
        return builder.finish(provenance)


def oracle_patients(cohort, patients, refusals=None):
    """GT's converted patients with each mention's start set to its truth event's day (`truth_event`), within GT's range
    when the two meet (rule 1 says they do); otherwise the truth day itself. A mention whose truth event the scorer
    refuses keeps GT's range, counted by reason in `refusals`."""
    by = {p['patient_id']: p for p in cohort['patients']}
    ancestors = si.ancestor_closure(cohort['concepts'])
    refusals = {} if refusals is None else refusals
    out = []
    for converted in patients:
        patient = by[converted['patient_id']]
        records = {r['record_id']: r for r in patient['records']}
        events = []
        for event in converted['events']:
            if event['kind'] == 'mention':
                record_id, i = event['event_id'].split('/', 1)[1].split('#m')
                try:
                    lo, hi = truth_day(patient, records[record_id], int(i), ancestors)
                except Refused as refused:
                    refusals[refused.code] = refusals.get(refused.code, 0) + 1
                    events.append(event)
                    continue
                a, b = max(lo, event['start'][0]), min(hi, event['start'][1])
                event = {**event, 'start': [a, b] if a <= b else [lo, hi]}
            events.append(event)
        out.append({**converted, 'events': events})
    return out


def requests_of(cohort_path, node):
    return json.loads(subprocess.run([node, str(REQUESTS), '--cohort', str(cohort_path)], check=True, cwd=ROOT,
                                     capture_output=True, text=True).stdout)


KEEP = ('status', 'certainty', 'relaxations', 'cost', 'reason', 'complete', 'note')


def match_arm(snapshot, cohort, cases):
    """{case: {'results': {pid: result fields}, 'with_candidate': [pid], 'seconds': s}} through `store_conditions.match`."""
    import store_parity
    births = store_parity.birth_micros(cohort)
    out = {}
    with es.open_store(snapshot) as store:
        ids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
        for case in cases:
            windows = {pid: None if w is None else {'start': births[pid] + w['start'] * MINUTE,
                                                    'end': births[pid] + w['end'] * MINUTE}
                       for pid, w in case['windows'].items()}
            query, conditions = store_parity.to_micros(case['query']), store_parity.conditions_micros(case.get('conditions'))
            started = time.perf_counter()
            result = sc.match(store, query, windows, conditions=conditions)
            seconds = time.perf_counter() - started
            found = sc.candidates(store, query, conditions)
            results = {}
            for r in result['results']:
                keep = {k: r[k] for k in KEEP if k in r}
                if 'relaxed_match' in r:
                    keep['relaxed_match'] = {k: r['relaxed_match'][k] for k in ('relaxations', 'certainty')}
                results[r['patient_id']] = keep
            out[case['name']] = {'results': results, 'seconds': seconds, 'digest': hashlib.sha256(
                                     json.dumps(result['results'], sort_keys=True).encode()).hexdigest(),
                                 'with_candidate': sorted(ids[i] for i in found['patient_idx'] if windows.get(ids[i]))}
    return out


def transition_ok(big, small):
    """Whether `small`, a result over a subset of `big`'s worlds (the same events, each start within big's), is consistent
    with every claim `big` decides: an `exact` holds in every smaller world, a `none` in none, a `relaxed` result's
    unrelaxed query in none and, if it was complete and exact, its relaxed set in every one. A smaller result that
    decides nothing (`unresolved`, `not_comparable`) is never a contradiction; an index window is the same in both."""
    b, s = big['status'], small['status']
    if (b == 'excluded') != (s == 'excluded'):
        return False
    if s in NO_CLAIM or b in NO_CLAIM + ('possible', 'excluded'):
        return True
    if b == 'exact':
        return s == 'exact'
    if b == 'none':
        return s == 'none' or (s == 'relaxed' and not big.get('complete', True))
    if b == 'relaxed':
        if s in ('exact', 'possible'):
            return False
        if not big.get('complete', True):
            return True
        if big.get('certainty') == 'exact':
            return s == 'relaxed' and small.get('relaxations') == big.get('relaxations') and small.get('certainty') == 'exact'
        return s == 'none' or (s == 'relaxed' and float(small.get('cost', 0)) >= float(big.get('cost', 0)))
    return False


def status_of(r):
    return f"{r['status']}/{r['certainty']}" if r.get('certainty') else r['status']


def plan_keys(seed):
    import generate as g
    return {c.pid: c.key for c in g.build(int(seed))}


def arms_seed(seed, node, tmp):
    cohort_path = Path(tmp) / f'cohort-{seed}.json'
    cohort = generate(seed, cohort_path)
    asked = requests_of(cohort_path, node)
    out = {'refusals': asked['refusals'], 'keys': plan_keys(seed), 'arms': {}, 'snapshots': {}}
    out['oracle_refusals'] = {}
    for arm in ('G', 'GT', 'T'):
        snapshot = build_arm(cohort, Path(tmp) / f'store-{seed}-{arm}', arm, out['oracle_refusals'])
        out['snapshots'][arm] = Path(snapshot).name
        out['arms'][arm] = match_arm(snapshot, cohort, asked['cases'])
    out['cohort_sha256'] = hashlib.sha256(cohort_path.read_bytes()).hexdigest()
    return out


def tally(seed_runs, name, arm):
    """The owner's counting rule for one case and arm, pooled over seeds."""
    t = {'candidates': 0, 'unresolved': 0, 'exact': 0, 'statuses': Counter(), 'by_key': {}, 'seconds': 0.0}
    for run in seed_runs.values():
        got = run['arms'][arm][name]
        t['seconds'] += got['seconds']
        for pid in got['with_candidate']:
            r = got['results'][pid]
            t['candidates'] += 1
            t['statuses'][status_of(r)] += 1
            t['exact'] += r['status'] == 'exact'
            k = t['by_key'].setdefault(run['keys'][pid], {'candidates': 0, 'unresolved': 0})
            k['candidates'] += 1
            if r['status'] == 'unresolved':
                t['unresolved'] += 1
                k['unresolved'] += 1
    keys = t['by_key']
    no_ref = {k: v for k, v in keys.items() if k != 'reference'}
    t['distinct_child_types'] = {'types': len(keys), 'types_unresolved': sum(v['unresolved'] > 0 for v in keys.values())}
    t['without_C001'] = {'candidates': sum(v['candidates'] for v in no_ref.values()),
                         'unresolved': sum(v['unresolved'] for v in no_ref.values())}
    t['statuses'] = dict(sorted(t['statuses'].items()))
    t['seconds'] = round(t['seconds'], 3)
    return t


def decided(r):
    """A result's decided claim, or None: `exact`, a complete `none`, or a complete `relaxed` match whose certainty is
    `exact` (with its relaxations). `possible`, `unresolved`, `not_comparable`, an incomplete `none` or relaxed result,
    and a `possible` relaxed match decide less."""
    if r['status'] == 'exact' or (r['status'] == 'none' and r.get('complete', True)):
        return (r['status'],)
    if r['status'] == 'relaxed' and r.get('certainty') == 'exact' and r.get('complete', True):
        return ('relaxed', tuple(r.get('relaxations') or ()))
    return None


def claim_type(r):
    """The kind of claim `transition_ok` checks in a larger arm's result (rule 4's count, S3), or None."""
    if r['status'] == 'exact':
        return 'exact'
    if r['status'] == 'none' and r.get('complete', True):
        return 'complete_none'
    if r['status'] == 'relaxed':
        return 'relaxed'
    return None


def compare(seed_runs, name, big, small):
    """Transitions from arm `big` to arm `small` over every patient of a case: the status matrix (every patient and
    among candidates), the contradictions (`transition_ok`), the claims checked by type, the results that move toward or
    away from decided (`decided`), and, among candidates, the results that become `exact`."""
    matrix, every, wrong, became_exact = Counter(), Counter(), [], 0
    claims, toward, away = Counter(), 0, 0
    for seed, run in seed_runs.items():
        a, b = run['arms'][big][name], run['arms'][small][name]
        cand = set(a['with_candidate'])
        for pid, r in a['results'].items():
            s = b['results'][pid]
            kind = claim_type(r)
            if kind:
                claims[kind] += 1
            if not transition_ok(r, s):
                wrong.append({'seed': seed, 'patient_id': pid, big: r, small: s})
            x, y = decided(r), decided(s)
            toward += x is None and y is not None
            away += x is not None and y != x
            every[f'{status_of(r)} -> {status_of(s)}'] += 1
            if pid in cand:
                matrix[f'{status_of(r)} -> {status_of(s)}'] += 1
                became_exact += r['status'] != 'exact' and s['status'] == 'exact'
    return {'contradictions': len(wrong), 'examples': wrong[:20], 'claims_checked': dict(sorted(claims.items())),
            'toward_decided': toward, 'away_from_decided': away, 'became_exact_among_candidates': became_exact,
            'candidate_transitions': dict(sorted(matrix.items())), 'all_transitions': dict(sorted(every.items()))}


def arms(args):
    node = args.node or os.environ.get('NODE', 'node')
    seed_runs, used = {}, []
    declared = seeds_of(args.seeds) if args.seeds else ARM_SEEDS
    extension = () if args.extend == 'none' else seeds_of(args.extend) if args.extend else ARM_EXTEND
    with tempfile.TemporaryDirectory(prefix='e0-arms-') as tmp:
        def measure(seed):
            seed_runs[seed] = arms_seed(seed, node, tmp)
            used.append(seed)
            print(f'seed {seed}: ' + '; '.join(
                f"{n.split(',')[0]} {sum(seed_runs[seed]['arms']['GT'][n]['results'][p]['status'] == 'exact' for p in seed_runs[seed]['arms']['GT'][n]['with_candidate'])}"
                for n in STATED), file=sys.stderr)
        for seed in declared:
            measure(seed)
        pooled = lambda n: sum(len(seed_runs[s]['arms']['GT'][n]['with_candidate']) for s in used)
        for seed in extension:
            if not any(pooled(n) < MINIMUM for n in STATED):
                break
            measure(seed)
    refusals = [r['refusals'] for r in seed_runs.values()]
    coverage = {'refusals': refusals[0], 'expected': EXPECTED_REFUSALS,
                'met': all(r == EXPECTED_REFUSALS for r in refusals)}
    names = STATED + CODED
    templates = {n: {arm: tally(seed_runs, n, arm) for arm in ('G', 'GT', 'T')} for n in names}
    bar = {}
    for n in STATED:
        gt = templates[n]['GT']
        d = gt['distinct_child_types']
        bar[n] = {**d, 'candidates': gt['candidates'], 'unresolved': gt['unresolved'],
                  'enough_candidates': gt['candidates'] >= MINIMUM,
                  'within_owners_bar': gt['candidates'] >= MINIMUM and d['types'] > 0
                  and d['types_unresolved'] <= SHARE * d['types']}
    movement = {n: {'G->GT': compare(seed_runs, n, 'G', 'GT'), 'GT->T': compare(seed_runs, n, 'GT', 'T'),
                    'G->T': compare(seed_runs, n, 'G', 'T')} for n in names}
    wrong = sum(movement[n][k]['contradictions'] for n in names for k in ('G->GT', 'GT->T'))
    claims = {k: dict(sum((Counter(movement[n][k]['claims_checked']) for n in names), Counter()))
              for k in ('G->GT', 'GT->T')}
    claims_met = all(claims[k].get(t, 0) > 0 for k in claims for t in CLAIM_TYPES)
    away = sum(movement[n]['G->GT']['away_from_decided'] for n in names)
    toward = sum(movement[n]['G->GT']['toward_decided'] for n in STATED)
    decidedness = {'away_from_decided_G_to_GT': away, 'toward_decided_G_to_GT_stated': toward,
                   'met': away == 0 and toward >= 1}
    control = {}
    for n in CODED:
        same = {arm: all(seed_runs[s]['arms'][arm][n]['digest'] == seed_runs[s]['arms']['G'][n]['digest']
                         and seed_runs[s]['arms'][arm][n]['with_candidate'] == seed_runs[s]['arms']['G'][n]['with_candidate']
                         for s in used) for arm in ('GT', 'T')}
        control[n] = same
    for s in used:
        for arm in ('G', 'GT', 'T'):
            for n in names:
                seed_runs[s]['arms'][arm][n].pop('results')
                seed_runs[s]['arms'][arm][n].pop('with_candidate')
    report = {
        'profile': 'e0b-mention-time-arms-1.0',
        'rule': ('E0b rules 3 to 5 (E0\'s, as amended at E0\'s review, S2 and S3; the oracle T names truth events as '
                 'the scorer does): the page\'s templates (e0_requests.cjs) '
                 'matched through store_conditions.match in three stores per cohort (G: store_parity.build, D30 timing; '
                 'GT: the same through synthetic_mentions with normalised_v1; T: GT with each mention at its truth '
                 'event\'s day). Rule 3: from G to GT, 0 results move away from decided (every case, every patient) and '
                 'at least one moves toward decided (stated findings); the owner\'s bar reported. Rule 4: no G result is '
                 'contradicted by GT, nor GT by T (transition_ok), and the claims checked of each type (exact, complete '
                 'none, relaxed) number more than 0 for each pair. Rule 5: under coded only, GT\'s and T\'s results '
                 'equal G\'s'),
        'declared_seeds': list(declared), 'extension_seeds': list(extension), 'seeds_used': used,
        'coverage': coverage,
        'usefulness': {'decidedness': decidedness, 'owners_bar_reported': bar, 'met': decidedness['met']},
        'wrong_answers': {'contradictions': wrong, 'claims_checked': claims, 'met': wrong == 0 and claims_met},
        'negative_control': {'identical': control, 'met': all(all(v.values()) for v in control.values())},
        'templates': templates, 'movement': movement,
        'oracle_refusals': dict(sum((Counter(r['oracle_refusals']) for r in seed_runs.values()), Counter())),
        'per_seed': {s: {'cohort_sha256': r['cohort_sha256'], 'snapshots': r['snapshots'], 'arms': r['arms'],
                         'oracle_refusals': r['oracle_refusals']}
                     for s, r in seed_runs.items()},
        'artifacts': artifacts(), 'contains_patient_level_data': False,
    }
    report['met'] = (coverage['met'] and report['usefulness']['met'] and report['wrong_answers']['met']
                     and report['negative_control']['met'])
    write(args.output, report)
    print('usefulness: ' + '; '.join(f"{n.split(',')[0]} {bar[n]['types_unresolved']}/{bar[n]['types']} types, exact G "
                                     f"{templates[n]['G']['exact']} GT {templates[n]['GT']['exact']}" for n in STATED)
          + f'; toward {toward}, away {away}; contradictions {wrong}, claims {claims}; '
          + f'control {"met" if report["negative_control"]["met"] else "missed"}',
          file=sys.stderr)


def write(output, report):
    text = json.dumps(report, indent=1, sort_keys=False) + '\n'
    if output:
        Path(output).write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('soundness', 'defects', 'arms'):
        p = sub.add_parser(name)
        p.add_argument('--seeds')
        p.add_argument('--output')
        if name != 'arms':
            p.add_argument('--committed', action='store_true', help='also the generator\'s default seed')
        if name == 'soundness':
            p.add_argument('--defect', choices=mt.DEFECTS)
        if name == 'arms':
            p.add_argument('--extend')
            p.add_argument('--node')
    args = parser.parse_args(argv)
    {'soundness': soundness, 'defects': defects, 'arms': arms}[args.command](args)


if __name__ == '__main__':
    main()
