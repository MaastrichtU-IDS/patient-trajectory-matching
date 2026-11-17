"""Gene steps in the execution store's match request (D38): the first report that is not negative, timed by its
recording, across reclassification, from the classifications the converter wrapper keeps in each genetic result's
evidence.

- Small cases (`gene_cases`): the store's per-patient path against the true semantics (`trajectory_graph_worlds`,
  gene steps read from the written rule) and against the reference (`gene_reference`), on the plain, derived and
  relative generators; the planted defects.
- Snapshots: the store against the reference, result for result, on fixtures made for it (negatives, VUS,
  reclassification each way, missing and unknown classifications, two genes under a wider concept, gene mentions,
  rises, shared variables) and on the synthetic cohort built by the wrapper, at 1 and 2 workers, both detail levels,
  pruning on and off; an undetermined record ignored is caught there; refusals; the output and context.
- The module boundary: gene steps live in `store_conditions_gene`, which no build loads; every other request is
  `store_conditions_relative`'s own answer; the wrapper changes evidence and provenance only; the functions it copies
  are pinned.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
import hashlib
import inspect
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import execution_store as es
from . import gene_cases as gk
from . import gene_reference as gr
from . import store_conditions as sc
from . import store_conditions_gene as gc
from . import store_conditions_relative as rc
from . import synthetic_ingest as si
from . import test_relative_conditions as trc
from . import trajectory_graph as tg

ROOT = Path(__file__).resolve().parents[1]
DAY = trc.DAY
T0 = trc.T0
MINUTE = 60_000_000
GENE = dict(gc.GENE_SPEC)
SMALL = 500  # small cases per generator in the suite (the decision log reports the ad hoc runs)


def run_small(generator, n, defect=None, reference=True, first=0):
    """(violations, mismatches, status mix) over n small cases."""
    bad, mismatched, mix = [], [], Counter()
    with patch.object(gc, '_D38_DEFECT', defect):
        for seed in range(first, first + n):
            q, c, p, m, r, rd = gk.case(seed, generator)
            got = gk.store_result(q, c, p, m, r, rd)
            mix[got['status']] += 1
            wrong = gk.check(got, q, c, p, m, r, rd)
            if wrong:
                bad.append((seed, wrong))
            if reference and json.dumps(got) != json.dumps(gk.reference_result(q, c, p, m, r, rd)):
                mismatched.append(seed)
    return bad, mismatched, mix


class FindingTests(unittest.TestCase):
    """The rule on a record's reports, against the reference's own reading."""

    def at(self, *pairs):
        return [{'record_id': f'r{j}', 'recorded_at': t, 'classification': c, 'status': 'final'}
                for j, (t, c) in enumerate(pairs)]

    def test_the_rule(self):
        cases = [
            (self.at((5, 'pathogenic')), ('found', (5, 5))),
            (self.at((5, 'uncertain significance'), (9, 'pathogenic')), ('found', (5, 5))),  # VUS, then reclassified
            (self.at((5, 'no relevant variant'), (9, 'pathogenic')), ('found', (9, 9))),  # negative, then amended
            (self.at((5, 'pathogenic'), (9, 'likely benign')), ('found', (5, 5))),  # downgraded: the first report
            (self.at((5, 'no relevant variant')), ('none', None)),
            (self.at((5, 'benign'), (7, 'likely benign')), ('none', None)),
            (self.at((5, None), (9, 'pathogenic')), ('undetermined', (5, 9))),
            (self.at((5, 'no relevant variant'), (9, 'see report')), ('undetermined', (5, 9))),
            (self.at((5, 'pathogenic'), (9, None)), ('found', (5, 5))),
            (self.at((9, 'pathogenic'), (5, 'no relevant variant')), ('found', (9, 9))),  # by recorded_at, not position
            (self.at((5, 'homozygous deletion')), ('found', (5, 5))),
        ]
        for reports, want in cases:
            self.assertEqual(gc.finding(reports, (0, 1)), want, reports)
            verdict, at = gr.report_finding(reports)
            self.assertEqual({'found': 'finding', 'none': 'negative'}.get(want[0], want[0]), verdict)
            if verdict == 'finding':
                self.assertEqual((at, at), want[1])
        for bad in ([], None, [{'recorded_at': '5'}], [{'classification': 'pathogenic'}]):
            with self.assertRaises(es.StoreError) as raised:
                gc.finding(bad)
            self.assertEqual(raised.exception.code, 'GENE_REPORTS_MALFORMED')

    def test_the_two_vocabularies_are_the_references(self):
        self.assertEqual(set(gc.NEGATIVE), gr.NEGATIVE)
        self.assertEqual(set(gc.NOT_NEGATIVE), gr.NOT_NEGATIVE)
        self.assertFalse(set(gc.NEGATIVE) & set(gc.NOT_NEGATIVE))


class SmallCaseTests(unittest.TestCase):
    """The store's gene steps are sound on small grids and agree with the reference."""

    def test_no_claim_is_false_and_the_reference_agrees(self):
        for generator in gk.GENERATORS:
            bad, mismatched, mix = run_small(generator, SMALL)
            self.assertEqual(bad, [], generator)
            self.assertEqual(mismatched, [], generator)
            self.assertTrue({'exact', 'none'} <= set(mix), (generator, mix))


class PlantedDefectTests(unittest.TestCase):
    """Each D38 planted defect is caught: by the worlds check on small cases, or (an undetermined record ignored) by the
    reference on snapshots."""

    def test_each_defect_is_caught_by_the_worlds_check(self):
        for defect in ('negative_counted', 'reclassification_ignored', 'sample_time', 'vus_negative', 'mention_bound'):
            found = sum(len(run_small(g, 400, defect, reference=False)[0]) for g in gk.GENERATORS)
            self.assertGreater(found, 0, defect)

    def test_an_undetermined_record_ignored_is_caught_by_the_reference(self):
        with patch.object(gc, '_D38_DEFECT', 'undetermined_ignored'):
            wrong, _ = compare('fixture', range(40))
        self.assertGreater(len(wrong), 0)


# Snapshots: fixtures made for gene steps, and the synthetic cohort built by the wrapper.

GENE_ANCESTORS = {**trc.ANCESTORS, 'g:gene': [], 'g:SCN1A': ['g:gene'], 'g:CDKL5': ['g:gene']}


def gene_ingest(seed, patients=40, shared=0.2):
    """D36's relative fixture with gene reports added: per patient up to three genetic results of g:SCN1A or g:CDKL5
    (a sample a day wide or exact; one to three reports, minutes apart from days, classifications from both vocabularies,
    missing or unknown sometimes), letter mentions of g:SCN1A or c:febrile, and genetics and letters coverage."""
    ingest = trc.relative_ingest(seed, patients=patients, shared=shared)
    rng = random.Random(f'gene-fixture/{seed}')
    for p in ingest['patients']:
        birth = min([e['start'][0] for e in p['events']] or [T0])
        for k in range(rng.choice((0, 1, 1, 2, 3))):
            lo = birth + rng.randint(0, 300) * DAY
            times = sorted(lo + rng.randint(20, 200) * DAY + rng.randint(0, 1439) * MINUTE
                           for _ in range(rng.choice((1, 1, 2, 3))))
            chain = [{'record_id': f'{p["patient_id"]}-G{k}-{j}', 'recorded_at': t, 'classification': rng.choice(gk.CLASSES),
                      'status': 'final' if j == 0 else 'amended'} for j, t in enumerate(times)]
            p['events'].append({'event_id': f'g{k}', 'kind': 'genetic_result', 'source': 'genetics',
                                'concepts': [rng.choice(('g:SCN1A', 'g:SCN1A', 'g:CDKL5'))],
                                'start': [lo, lo + rng.choice((0, DAY - 1))], 'recorded_at': times[-1],
                                'evidence': {'gene_reports': chain}})
        for k in range(rng.choice((0, 0, 1, 2))):
            p['events'].append({'event_id': f'n{k}', 'kind': 'mention', 'source': 'letters',
                                'concepts': [rng.choice(('g:SCN1A', 'c:febrile'))],
                                'start': [birth, birth + rng.randint(0, 460) * DAY], 'evidence': {}})
        p['events'].sort(key=lambda e: e['event_id'])
        p['coverage'] = sorted(p['coverage'] + [{'source': s, 'lo': birth, 'hi': birth + 460 * DAY, 'basis': 'declared'}
                                                for s in ('genetics', 'letters') if rng.random() < 0.85],
                               key=lambda c: c['source'])
    ingest['closure'] = {'version': 'gene-fixture-1', 'ancestors': GENE_ANCESTORS}
    ingest['dataset_id'] = f'gene-fixture-{seed}'
    labels = dict((ingest.get('provenance') or {}).get('labels') or {})
    labels[gc.GENE_LABEL] = 'genetic_result'
    ingest['provenance'] = {**(ingest.get('provenance') or {}), 'labels': labels}
    return ingest


def gene_request(rng, ingest, pool):
    """(query, conditions, windows): a gene step, and up to two other steps (a rise sometimes, or a concept step under
    any, first or last), related to it in either direction, to each other, or to the period."""
    gene = rng.choice(pool['genes'])
    steps, conds, relaxations = [{'id': 'G', 'concept': gene}], {'G': {'gene': dict(GENE)}}, []
    for i in range(rng.randint(0, 2)):
        sid = 'AB'[i]
        if rng.random() < 0.3:
            concept = rng.choice(pool['measured'])
            unit = rng.choice(pool['units'][concept])
            within = rng.choice((31, 62, 75, 120)) * DAY if 'freq' in concept else rng.choice((1, 30, 90, 200)) * DAY
            conds[sid] = {'relative': {'kind': rng.choice(('rise', 'fall')), 'factor': rng.choice(('1.5', '2')),
                                       'unit': unit, 'floor': rng.choice(('1', '0.000000001')),
                                       'baseline': {'previous': rng.randint(1, 2), 'within': within}}}
            steps.append({'id': sid, 'concept': concept})
        else:
            concept = rng.choice(pool['other'])
            steps.append({'id': sid, 'concept': concept, 'occurrence': rng.choice(('any', 'any', 'first', 'last'))})
            if pool['parent'].get(concept) and rng.random() < 0.3:
                relaxations.append({'id': f'w{sid}', 'kind': 'widen_concept', 'step': sid,
                                    'concept': pool['parent'][concept], 'cost': rng.choice(('0.5', '1'))})
    rng.shuffle(steps)
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        lo = rng.choice((-200, -60, 0, 0, 30)) * DAY
        r = {'id': f'R{i}', 'kind': 'after', 'from': a, 'to': b, 'min': lo, 'max': lo + rng.choice((90, 365, 730)) * DAY}
        relations.append(r)
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + 180 * DAY, 'cost': rng.choice(('0.25', '0.5'))})
    if rng.random() < 0.25:
        relations.append({'id': 'RS', 'kind': 'after', 'from': 'START', 'to': 'G', 'min': 0,
                          'max': rng.choice((180, 730)) * DAY})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1')), 'search_budget': rng.choice((300, 40000))}
    return query, {'profile': gc.GENE_PROFILE, 'steps': conds}, windows_for(rng, ingest)


def windows_for(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        first = min([e['start'][0] for e in p['events']] or [T0])
        start = first + rng.randint(-30, 300) * DAY
        out[p['patient_id']] = {'start': start, 'end': start + rng.randint(120, 900) * DAY}
    return out


FIXTURE_POOL = {'genes': ['g:SCN1A', 'g:SCN1A', 'g:CDKL5', 'g:gene'], 'measured': ['m:freq', 'm:weight'],
                'other': ['d:a', 'c:febrile', 'c:seizure'], 'parent': {'c:febrile': 'c:seizure', 'd:a': 'd:drug'},
                'units': {'m:freq': ['/month'], 'm:weight': ['kg', 'lb']}}
COHORT_POOL = {'genes': ['gene:SCN1A', 'gene:SCN1A', 'gene:CDKL5', 'gene:PCDH19'],
               'measured': ['seizure_frequency', 'weight'],
               'other': ['sodium_channel_blocker', 'febrile_seizure', 'seizure', 'stiripentol', 'dravet'],
               'parent': {}, 'units': {'seizure_frequency': ['/month'], 'weight': ['kg']}}

_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='gene-steps-')
    tmp = Path(_STATE['tmp'].name)
    for name, seed, options in (('fixture', 51, {}), ('shared', 52, {'shared': 0.7})):
        ingest = gene_ingest(seed, **options)
        _STATE[name] = (ingest, es.open_store(es.build(ingest, tmp / name), verify='content'), FIXTURE_POOL)
    subprocess.run([sys.executable, str(ROOT / 'demos/mockups/synthetic/generate.py'), '--output', str(tmp / 'cohort.json')],
                   check=True, capture_output=True, cwd=ROOT)
    cohort = json.loads((tmp / 'cohort.json').read_text(encoding='utf-8'))
    _STATE['cohort_json'] = cohort
    ingest = gc.synthetic_ingest(cohort, series=True)
    _STATE['cohort'] = (ingest, es.open_store(es.build(ingest, tmp / 'cohort'), verify='content'), COHORT_POOL)
    plain = rc.synthetic_ingest(cohort, series=True)  # main's build: no gene reports, no gene label
    _STATE['main'] = (plain, es.open_store(es.build(plain, tmp / 'main'), verify='content'), COHORT_POOL)


def tearDownModule():
    for name in ('fixture', 'shared', 'cohort', 'main'):
        if name in _STATE:
            _STATE[name][1].close()
    es.close_pools()
    _STATE['tmp'].cleanup()


def case(seed, cohort):
    ingest, _, pool = _STATE[cohort]
    rng = random.Random(f'{cohort}/gene/{seed}')
    for _ in range(50):
        query, conditions, windows = gene_request(rng, ingest, pool)
        try:
            gc.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions, windows
    raise AssertionError(seed)


def compare(cohort, seeds, **options):
    ingest, store, _ = _STATE[cohort]
    wrong, mix = [], Counter()
    for seed in seeds:
        query, conditions, windows = case(seed, cohort)
        got = gc.match(store, query, windows, conditions=conditions, **options)['results']
        want = gr.gene_exhaustive_match(ingest, query, windows, conditions)
        if options.get('detail') == 'decided':
            listed = {r['patient_id'] for r in got}
            want = [w for w in want if w['patient_id'] in listed]
        for g, w in zip(got, want, strict=True):
            if json.dumps(trc.unfiltered(g)) != json.dumps(w):
                wrong.append((seed, g, w))
        mix.update(g['status'] for g in got)
    return wrong, mix


DIFFERENTIAL = {'fixture': 40, 'shared': 20, 'cohort': 20}  # gene requests per snapshot in the suite


class GeneDifferentialTests(unittest.TestCase):
    """The store against the reference, result for result, on snapshots."""

    def test_the_store_equals_the_reference(self):
        for cohort, n in DIFFERENTIAL.items():
            wrong, mix = compare(cohort, range(n))
            self.assertEqual(wrong[:2], [], cohort)
            self.assertTrue({'exact', 'none'} <= set(mix), (cohort, mix))

    def test_undetermined_records_reach_the_gene_step(self):
        """The fixtures hold undetermined records, and some result turns on them (ADMISSION_EXCLUDED_RECORDS)."""
        ingest, store, _ = _STATE['fixture']
        undetermined = sum(1 for p in ingest['patients'] for e in p['events'] if e['kind'] == 'genetic_result'
                           and gr.report_finding(e['evidence']['gene_reports'])[0] == 'undetermined')
        self.assertGreater(undetermined, 3)
        reasons = Counter()
        for seed in range(40):
            query, conditions, windows = case(seed, 'fixture')
            for r in gc.match(store, query, windows, conditions=conditions)['results']:
                if r.get('reason') == 'ADMISSION_EXCLUDED_RECORDS' and 'G' in r.get('excluded_steps', []):
                    reasons[r['status']] += 1
        self.assertGreater(sum(reasons.values()), 0, reasons)

    def test_workers_detail_and_pruning_change_nothing(self):
        for cohort in ('fixture', 'cohort'):
            _, store, _ = _STATE[cohort]
            for seed in range(0, 20, 2):
                query, conditions, windows = case(seed, cohort)
                base = gc.match(store, query, windows, conditions=conditions)
                two = gc.match(store, query, windows, conditions=conditions, workers=2, chunk=7)
                self.assertEqual(json.dumps(base), json.dumps(two), (cohort, seed))
                decided = gc.match(store, query, windows, conditions=conditions, detail='decided')
                listed = {r['patient_id']: r for r in decided['results']}
                for r in base['results']:
                    if r['patient_id'] in listed:
                        self.assertEqual(listed[r['patient_id']], r)
                with patch.object(es, '_PRUNE', False):
                    off = gc.match(store, query, windows, conditions=conditions)
                self.assertEqual(json.dumps(base), json.dumps(off), (cohort, seed))

    def test_pruning_acts_on_gene_steps(self):
        pruned = 0
        _, store, _ = _STATE['fixture']
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        for seed in range(40):
            query, conditions, windows = case(seed, 'fixture')
            request = gc.Request(query, conditions)
            pq = tg.PreparedQuery(request.query)
            found = gc._candidates(store, pq, request)
            by_idx = {ids[p]: w for p, w in windows.items()}
            live = [i for i in found['patient_idx'] if by_idx[i] is not None]
            pruned += len(gc._prune(store, pq, live, by_idx, request, tags=request.used, derived=request.derived_used))
        self.assertGreater(pruned, 0)

    def test_candidates_are_exactly_the_patients_with_a_finding(self):
        ingest, store, _ = _STATE['fixture']
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'G', 'concept': 'g:SCN1A'}], 'relations': []}
        found = gc.candidates(store, q, {'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': dict(GENE)}}})
        ids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
        want = sorted(p['patient_id'] for p in ingest['patients'] if any(
            e['kind'] == 'genetic_result' and 'g:SCN1A' in e['concepts']
            and gr.report_finding(e['evidence']['gene_reports'])[0] == 'finding' for e in p['events']))
        self.assertEqual(sorted(ids[i] for i in found['patient_idx']), want)
        self.assertGreater(len(want), 5)


class CohortTests(unittest.TestCase):
    """The committed cohort, built by the wrapper: gene steps bind the page's reports."""

    def test_the_wrapper_changes_evidence_and_provenance_only(self):
        cohort = _STATE['cohort_json']
        ingest, _, _ = _STATE['cohort']
        converted = rc.synthetic_ingest(cohort, series=True)
        self.assertEqual({k: v for k, v in ingest.items() if k not in ('provenance', 'patients')},
                         {k: v for k, v in converted.items() if k not in ('provenance', 'patients')})
        self.assertEqual(ingest['provenance'], {**converted['provenance'], 'gene_reports': gc.GENE_REPORT_RULE,
                                                'labels': {**converted['provenance']['labels'], gc.GENE_LABEL: 'genetic_result'}})
        changed = 0
        for a, b in zip(ingest['patients'], converted['patients'], strict=True):
            self.assertEqual({k: v for k, v in a.items() if k != 'events'}, {k: v for k, v in b.items() if k != 'events'})
            for x, y in zip(a['events'], b['events'], strict=True):
                if x['kind'] == 'genetic_result':
                    self.assertEqual({k: v for k, v in x['evidence'].items() if k != 'gene_reports'}, y['evidence'])
                    self.assertNotIn('gene_reports', y['evidence'])
                    x = {**x, 'evidence': y['evidence']}
                    changed += 1
                self.assertEqual(x, y)
        self.assertEqual(changed, 39)

    def test_reclassification_and_negatives_in_the_cohort(self):
        """C001's SCN1A variant was reported VUS and amended to pathogenic: the store keeps only the amendment as an
        event, and its finding is the VUS report's time; a negative result gives no finding."""
        ingest, store, _ = _STATE['cohort']
        cohort = _STATE['cohort_json']
        c001 = next(p for p in ingest['patients'] if p['patient_id'] == 'C001')
        event = next(e for e in c001['events'] if e['kind'] == 'genetic_result')
        reports = event['evidence']['gene_reports']
        self.assertEqual([r['classification'] for r in reports], ['uncertain significance', 'pathogenic'])
        self.assertEqual(event['recorded_at'], reports[-1]['recorded_at'])
        self.assertEqual(gc.finding(reports), ('found', (reports[0]['recorded_at'],) * 2))
        raw = next(p for p in cohort['patients'] if p['patient_id'] == 'C001')
        first = min((r for r in raw['records'] if r['kind'] == 'genetic_result'), key=lambda r: r['recorded_at'])
        self.assertEqual(reports[0]['recorded_at'], si.micros(first['recorded_at']))
        negatives = [(p['patient_id'], e['event_id']) for p in ingest['patients'] for e in p['events']
                     if e['kind'] == 'genetic_result'
                     and [r['classification'] for r in e['evidence']['gene_reports']] == ['no relevant variant']]
        self.assertEqual(len(negatives), 3)
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'B', 'concept': 'gene:SCN1A'}], 'relations': [],
             'relaxations': [], 'budget': '0'}
        births = {p['patient_id']: si.micros(p['birth_date'] + 'T00:00') for p in cohort['patients']}
        windows = {pid: {'start': b, 'end': b + 3653 * 1440 * MINUTE} for pid, b in births.items()}
        out = gc.match(store, q, windows, conditions={'profile': gc.GENE_PROFILE, 'steps': {'B': {'gene': dict(GENE)}}})
        by = {r['patient_id']: r for r in out['results']}
        self.assertEqual(by['C001']['binding'], {'B': f'derived-step:B/own@{event["event_id"]}'})
        for pid, eid in negatives:  # a negative result is never bound
            self.assertNotIn(f'derived-step:B/own@{eid}', json.dumps(by[pid]))

    def test_letters_do_not_cover_a_gene_step(self):
        """D38 review, M1: C002, C003 and C016 hold no genetic result and no genetics coverage, only letters (which
        mention genes). Over a lifetime window a gene step is not comparable for them, not `none`; the reference agrees."""
        ingest, store, _ = _STATE['cohort']
        cohort = _STATE['cohort_json']
        three = ('C002', 'C003', 'C016')
        for p in ingest['patients']:
            if p['patient_id'] in three:
                self.assertFalse(any(e['kind'] == 'genetic_result' for e in p['events']), p['patient_id'])
                self.assertEqual({c['source'] for c in p['coverage']} & {'genetics', 'letters'}, {'letters'})
        self.assertIn(('letters', 'gene:SCN1A'), store.query("SELECT source, concept FROM source_concepts"))
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'B', 'concept': 'gene:SCN1A'}], 'relations': [],
             'relaxations': [], 'budget': '0'}
        c = {'profile': gc.GENE_PROFILE, 'steps': {'B': {'gene': dict(GENE)}}}
        births = {p['patient_id']: si.micros(p['birth_date'] + 'T00:00') for p in cohort['patients']}
        windows = {pid: {'start': b, 'end': b + 3653 * 1440 * MINUTE} for pid, b in births.items()}
        got = gc.match(store, q, windows, conditions=c)['results']
        by = {r['patient_id']: r for r in got}
        for pid in three:
            self.assertEqual(by[pid], {'patient_id': pid, 'status': 'not_comparable', 'reason': 'NO_SOURCE_COVERAGE',
                                       'uncovered_steps': ['B']})
        want = gr.gene_exhaustive_match(ingest, q, windows, c)
        self.assertEqual([json.dumps(trc.unfiltered(r)) for r in got], [json.dumps(r) for r in want])
        self.assertEqual(gc._gene_sources(store, gc.Request(q, c)), {'derived-step:B/own': {'genetics'}})

    def test_a_snapshot_built_without_the_wrapper_refuses_by_name(self):
        plain, store, _ = _STATE['main']
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'B', 'concept': 'gene:SCN1A'}], 'relations': []}
        c = {'profile': gc.GENE_PROFILE, 'steps': {'B': {'gene': dict(GENE)}}}
        pid = plain['patients'][0]['patient_id']
        for call in (lambda: gc.match(store, q, {pid: {'start': 0, 'end': 10}}, conditions=c),
                     lambda: gc.candidates(store, q, c)):
            with self.assertRaises(es.StoreError) as raised:
                call()
            self.assertEqual(raised.exception.code, 'GENE_REPORTS_NOT_DECLARED')


class RequestTests(unittest.TestCase):
    """The gene profile's contract: refusals by name, output and context."""

    def setUp(self):
        self.ingest, self.store, _ = _STATE['fixture']
        self.pid = self.ingest['patients'][0]['patient_id']
        self.windows = {self.pid: {'start': T0, 'end': T0 + 900 * DAY}}

    def test_refusals(self):
        one = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'G', 'concept': 'g:SCN1A'}], 'relations': []}
        cases = [
            ({'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE, 'unit': 'kg'}}}, 'GENE_WITH_OTHER_CONDITION'),
            ({'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': {'report': 'first'}}}}, 'INVALID_GENE'),
            ({'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': {**GENE, 'timed_by': 'available_at'}}}}, 'INVALID_GENE'),
            ({'profile': gc.GENE_PROFILE, 'steps': {'X': {'gene': GENE}}}, 'UNKNOWN_CONDITION_STEP'),
            ({'profile': gc.GENE_PROFILE, 'steps': {}}, 'INVALID_CONDITIONS'),
            ({'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}}, 'other': 1}, 'INVALID_CONDITIONS'),
            ({'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}}, 'relaxations': {}}, 'INVALID_CONDITIONS'),
            ({'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}},
              'relaxations': [{'id': 'v', 'kind': 'widen_value', 'step': 'G', 'cost': '1', 'condition': {'unit': 'kg'}}]},
             'GENE_STEP_RELAXATION_UNSUPPORTED'),
        ]
        for conditions, code in cases:
            with self.assertRaises(es.StoreError) as raised:
                gc.Request(one, conditions)
            self.assertEqual(raised.exception.code, code, conditions)
        for occurrence in ('first', 'last'):
            with self.assertRaises(es.StoreError) as raised:
                gc.Request({**one, 'steps': [{'id': 'G', 'concept': 'g:SCN1A', 'occurrence': occurrence}]},
                           {'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}}})
            self.assertEqual(raised.exception.code, 'OCCURRENCE_ON_GENE_STEP')
        widened = {**one, 'relaxations': [{'id': 'w', 'kind': 'widen_concept', 'step': 'G', 'concept': 'g:gene',
                                           'cost': '1'}], 'budget': '1'}
        with self.assertRaises(es.StoreError) as raised:
            gc.Request(widened, {'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}}})
        self.assertEqual(raised.exception.code, 'GENE_STEP_RELAXATION_UNSUPPORTED')
        for profile in (es.CONDITIONS_PROFILE, es.CONDITIONS_PROFILE_1_1, rc.CONDITIONS_PROFILE_1_2):
            with self.assertRaises(es.StoreError) as raised:
                rc.Request(one, {'profile': profile, 'steps': {'G': {'gene': GENE}}})
            self.assertEqual(raised.exception.code, 'INVALID_CONDITION', profile)
        with self.assertRaises(es.StoreError) as raised:
            gc.match(self.store, one, self.windows, conditions={'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}}},
                     workers=0)
        self.assertEqual(raised.exception.code, 'INVALID_WORKERS')

    def test_output_and_context(self):
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'G', 'concept': 'g:SCN1A'}, {'id': 'A', 'concept': 'm:freq'}],
             'relations': []}
        c = {'profile': gc.GENE_PROFILE, 'steps': {'G': {'gene': GENE}, 'A': trc.rel(unit='/month', within=75 * DAY)}}
        out = gc.match(self.store, q, self.windows, conditions=c)
        self.assertEqual(out['derived_steps']['steps'], {'A': {'relative': 'rise'}, 'G': {'gene': 'first_not_negative'}})
        self.assertEqual(out['derived_steps']['tags']['derived-step:G/own'],
                         {'kind': 'gene', 'concept': 'g:SCN1A', **GENE})
        self.assertEqual(out['derived_steps']['gene_rules'], gc.GENE_RULES)
        self.assertEqual(out['derived_steps']['profile'], gc.GENE_PROFILE)
        for path in (gc.MODULE_PATH, rc.MODULE_PATH, 'patterns/store_conditions_coded.py'):  # D42: the hooks' module
            self.assertEqual(out['context']['artifacts'][path], hashlib.sha256((ROOT / path).read_bytes()).hexdigest())
        self.assertEqual(out['context']['gene_rules'], es._digest(gc.GENE_RULES))
        self.assertEqual(out['labels'][gc.GENE_LABEL], 'genetic_result')


class ModuleBoundaryTests(unittest.TestCase):
    """D38: gene steps live in a module that builds never load; every other request is the relative module's answer."""

    COPIED = {  # the functions this module copies or builds on, as they were when it was written (e682817)
        'store_conditions_relative.Request.__init__': '4ee2cfcbd2b09dfc018c07541ee26cfc02d0bd23b1c71f056a0e9f4b7697aa01',
        'store_conditions_relative.Request.describe_derived': '86ac8dbcec41bd159c0ec272b8031d73a1c7b3be54c73b97c2d93e78830ecdf4',
        'store_conditions_relative._candidates': 'aa1edf2a8cf3c2a3996844e2ed22a6836759bc7d17400751ee156c3cb4257b5e',
        'store_conditions_relative._derived_chunk': '4acb5558dfc794ec7f1432ee9b5841d64fa9fb42c609715245be3644fbdeed61',
        'store_conditions_relative._match_derived_chunk': '79c7e419d93989f345499770d0048dcc93b6e09f83308d40f32049b4e3bdc257',
        'store_conditions_relative._derived_reach': '21aa453ee3f9f70072c6eb91c5a56a2a0adfce0125c0798416dbf27a6c19fd6f',
        'store_conditions_relative._worker_unit': 'f5c58944a229d1a9b91bd3b00ca70507c5e9b09883faa6592a77fc3f6f7cd55b',
        'store_conditions_relative._run': '8cb4044758cf408e05a4060c7867202da8a0184384542d539ade7e36c85b72e8',
        'store_conditions_relative.match': '7090b8dbed6afc0f35784df33466367ab798486f04456573970d11e8c35864aa',
        'store_conditions_relative.match_context': '440b9ab9d76c51b58701f3ff9d8f4aac477d09b374cf82657a5342befcdaca93',
        'store_conditions_relative.derive_patient': 'f9cd8cba1b5703139623644846d179b1b37bffe9000d32403014114aefcb1999',
        'execution_store._prune': 'ee5bcab69b7fc71f85c370bfc01600b0fee14652509ef98cddb5e41464b4e67d',
        # D42: the copy with the gene recorders moved to `store_conditions_coded._comparability`, which this module
        # calls (pinned there too); the original is unchanged, so the pin is.
        'execution_store._comparability': 'b16a9f9e578f2ae7f9d6f41a496b1134190d209da26c6ec03ec9b0ee8fcac729',
    }

    def test_the_copied_functions_have_not_changed(self):
        modules = {'store_conditions_relative': rc, 'execution_store': es}
        for name, digest in self.COPIED.items():
            head, rest = name.split('.', 1)
            obj = modules[head]
            for part in rest.split('.'):
                obj = getattr(obj, part)
            got = hashlib.sha256(inspect.getsource(obj).encode()).hexdigest()
            self.assertEqual(got, digest, f'{name} changed: update store_conditions_gene, whose copy of it (or code '
                                          f'built on it) must follow, then this pin')

    def test_the_dispatcher_routes_by_profile(self):
        """`store_conditions` is the one entry point: a gene request is the gene module's answer, any other the relative
        module's, byte for byte; it lists every profile it accepts."""
        ingest, store, _ = _STATE['fixture']
        self.assertEqual(sc.GENE_PROFILE, gc.GENE_PROFILE)
        self.assertEqual(sc.profiles(), [*rc.CONDITIONS_PROFILES, gc.GENE_PROFILE, sc.CODED_PROFILE,  # D39 adds 1.3
                                         gc.GENE_CODED_PROFILE])  # D42 the joint profile
        for seed in range(3):
            query, conditions, windows = case(seed, 'fixture')
            self.assertEqual(json.dumps(sc.match(store, query, windows, conditions=conditions)),
                             json.dumps(gc.match(store, query, windows, conditions=conditions)))
            self.assertEqual(sc.candidates(store, query, conditions), gc.candidates(store, query, conditions))
            self.assertEqual(sc.match_context(store, query, windows, conditions),
                             gc.match_context(store, query, windows, conditions))
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'm:freq'}], 'relations': []}
        windows = windows_for(random.Random('d38/dispatch'), ingest)
        for conditions in (None, {'profile': rc.CONDITIONS_PROFILE_1_2, 'steps': {'A': trc.rel(unit='/month', within=75 * DAY)}}):
            self.assertEqual(json.dumps(sc.match(store, q, windows, conditions=conditions)),
                             json.dumps(rc.match(store, q, windows, conditions=conditions)))

    def test_a_missing_superseded_record_is_named(self):
        patient = {'records': [{'source': 'genetics', 'record_id': 'b', 'supersedes': 'a', 'recorded_at': '2020-01-01T00:00',
                                'attributes': {'classification': 'pathogenic'}}]}
        with self.assertRaises(ValueError) as raised:
            gc.gene_reports(patient, 'genetics', 'b')
        self.assertTrue(str(raised.exception).startswith('GENE_REPORT_RECORD_MISSING'))

    def test_every_other_request_is_the_relative_modules_answer(self):
        ingest, store, _ = _STATE['fixture']
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'm:freq'},
                                                                 {'id': 'B', 'concept': 'c:seizure', 'occurrence': 'first'}],
             'relations': [{'id': 'R', 'kind': 'after', 'from': 'B', 'to': 'A', 'min': 0, 'max': 400 * DAY}]}
        windows = windows_for(random.Random('d38/boundary'), ingest)
        for conditions in (None, {'profile': es.CONDITIONS_PROFILE, 'steps': {'A': {'unit': '/month', 'ge': '2'}}},
                           {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'count': {'at_least': 2, 'within': 'window'}}}},
                           {'profile': rc.CONDITIONS_PROFILE_1_2, 'steps': {'A': trc.rel(unit='/month', within=75 * DAY)}}):
            for workers in (1, 2):
                a = rc.match(store, q, windows, conditions=conditions, workers=workers)
                b = gc.match(store, q, windows, conditions=conditions, workers=workers)
                self.assertEqual(json.dumps(a), json.dumps(b), conditions)
            self.assertEqual(rc.candidates(store, q, conditions), gc.candidates(store, q, conditions))

    def test_a_build_never_loads_the_module(self):
        """A snapshot built from the synthetic cohort, in a fresh interpreter, does not load this module, and nothing a
        build loads names it."""
        script = ('import json, sys, tempfile\n'
                  'from patterns import execution_store as es, synthetic_ingest as si\n'
                  'cohort = json.load(open(sys.argv[1]))\n'
                  'with tempfile.TemporaryDirectory() as tmp:\n'
                  '    es.build(si.synthetic_ingest(cohort, series=True), tmp)\n'
                  'print(json.dumps(sorted(m for m in sys.modules if m.startswith("patterns"))))\n')
        out = subprocess.run([sys.executable, '-c', script, str(Path(_STATE['tmp'].name) / 'cohort.json')],
                             check=True, capture_output=True, text=True, cwd=ROOT).stdout
        loaded = json.loads(out)
        self.assertNotIn('patterns.store_conditions_gene', loaded)
        self.assertNotIn('patterns.store_conditions', loaded)
        for path in ('patterns/execution_store.py', 'patterns/synthetic_ingest.py', 'tools/scaling_build_worker.py',
                     'patterns/store_conditions_relative.py'):
            self.assertNotIn('store_conditions_gene', (ROOT / path).read_text(encoding='utf-8'), path)


if __name__ == '__main__':
    unittest.main()
