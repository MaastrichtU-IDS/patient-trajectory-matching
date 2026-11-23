"""The synthetic cohort generator must build every seed, and the store must accept what the converter makes of it.

Regression for generator defects the scaling benchmark found: a child born after the snapshot had no events
(`min()` of nothing), the reference child could have no visit in the stale-letter window (`StopIteration`), and the
date-typo defect put a different year on each bound of a range that crossed New Year (INVERTED_BOUNDS). D54: ids
unique by construction and every link resolving (`IdTests`, no DuckDB needed).
Needs DuckDB only for the store's ingest validation (`patterns/requirements-store.lock.txt`); skipped without it.
"""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover - the store extra is optional
    duckdb = None

spec = importlib.util.spec_from_file_location('synthetic_generate', ROOT / 'demos/mockups/synthetic/generate.py')
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)

BASE = 20261103
# Seeds that crashed or produced inverted bounds before the fixes.
KNOWN_BAD = [20261121, 20261123, 20261168, 20261185, 20261192, 20261194, 20261248, 20261262, 20261266, 20261311]
SEEDS = KNOWN_BAD + list(range(BASE, BASE + 40))
# D54: E0's two seeds whose misses were a reused truth id (seed 5005's C030, seed 5024's C040).
E0_MISS_SEEDS = [5005, 5024]


class IdTests(unittest.TestCase):
    """D54: the generator's ids are unique by construction and every link resolves. Needs no DuckDB."""

    @classmethod
    def setUpClass(cls):
        cls.cohorts = {seed: json.loads(json.dumps(generate.serialise(generate.build(seed), seed), ensure_ascii=False))
                       for seed in SEEDS + E0_MISS_SEEDS}

    def test_every_id_is_unique_and_every_link_resolves(self):
        for seed, cohort in self.cohorts.items():
            record_ids = [r['record_id'] for p in cohort['patients'] for r in p['records']]
            self.assertEqual(len(record_ids), len(set(record_ids)), seed)
            for p in cohort['patients']:
                with self.subTest(seed=seed, patient=p['patient_id']):
                    events = [e['event_id'] for e in p['truth']['events']]
                    cases = [q['case_id'] for q in p['quality']]
                    self.assertEqual(len(events), len(set(events)))
                    self.assertEqual(len(cases), len(set(cases)))
                    records = {r['record_id'] for r in p['records']}
                    links = [r.get('truth_event_id') for r in p['records']]
                    links += [m.get('truth_event_id') for r in p['records'] for m in r.get('mentions', [])]
                    links += [q['truth_event_id'] for q in p['quality']]
                    self.assertEqual([t for t in links if t is not None and events.count(t) != 1], [])
                    self.assertEqual([rid for q in p['quality'] for rid in q['record_ids'] if rid not in records], [])

    def test_the_seeds_exercise_what_was_reused(self):
        """Not vacuous: the sites where main's generator reused an id or left a dangling link are on these seeds, and each
        now holds distinct ids. On the page's cohort main gave C032's weight at day 729 the id of a surviving
        seizure-frequency event, C032-T117; on seed 5005 main gave two of C026's cases one id (the dravet-negative-fr
        child, whose diagnosis cases are removed), and left C001's developmental-delay `recorded-late` case naming a
        removed record."""
        page = {p['patient_id']: p for p in self.cohorts[BASE]['patients']}
        events = page['C032']['truth']['events']
        weight = next(e for e in events if e['concept'] == 'weight' and e['day'] == 729)
        held = next(e for e in events if e['event_id'] == 'C032-T117')
        self.assertEqual(held['concept'], 'seizure_frequency')
        self.assertNotEqual(weight['event_id'], 'C032-T117')
        numbers = [int(q['case_id'].split('-Q')[1]) for q in {p['patient_id']: p for p in self.cohorts[5005]['patients']}['C026']['quality']]
        self.assertNotEqual(numbers, sorted(numbers))  # a case made after the removal took a freed, lower number
        c001 = {p['patient_id']: p for p in self.cohorts[5005]['patients']}['C001']
        delay = {e['event_id'] for e in c001['truth']['events'] if e['concept'] == 'dev_delay'}
        self.assertEqual([q for q in c001['quality'] if q['kind'] == 'recorded-late' and q['truth_event_id'] in delay], [])

    def test_an_item_made_after_removals_takes_the_lowest_free_number(self):
        child = generate.Child('C900', 'probe', 'dravet', generate.random.Random(1), 'F', generate.date(2020, 1, 1))
        for day in range(5):
            child.event('weight', day, value=3.0, unit='kg')
        child.truth = [e for e in child.truth if e['day'] not in (1, 2)]  # T002 and T003 dropped; T004 and T005 remain
        made = [child.event('height', 10 + i, value=50.0, unit='cm')['event_id'] for i in range(4)]
        # T004 (the length plus one) is held, so the lowest free number (T002); then T005 held -> T003; then T006, T007.
        self.assertEqual(made, ['C900-T002', 'C900-T003', 'C900-T006', 'C900-T007'])
        for i in range(3):
            child.case('missing-unit', [], child.truth[i])
        child.quality = child.quality[1:]
        child.case('missing-unit', [], child.truth[0])
        self.assertEqual([q['case_id'] for q in child.quality], ['C900-Q002', 'C900-Q003', 'C900-Q001'])

    def test_a_case_naming_a_removed_record_loses_it(self):
        child = generate.Child('C901', 'probe', 'dravet', generate.random.Random(1), 'F', generate.date(2020, 1, 1))
        ev = child.event('seizure', 10)
        a, b = (child.record('ehr', 'condition', ev, occurred=child.on_day(10)) for _ in range(2))
        child.case('recorded-late', [a], ev)
        child.case('duplicate-record', [a, b], ev)
        child.case('missing-measurement', [], ev)
        child.records.remove(a)
        generate.prune_case_records(child)
        self.assertEqual([(q['case_id'], q['record_ids']) for q in child.quality],
                         [('C901-Q002', [b['record_id']]), ('C901-Q003', [])])

    def test_serialise_refuses_a_duplicate_id_and_a_dangling_link(self):
        for plant in ('truth', 'case', 'record link', 'mention link', 'case link', 'case record'):
            children = generate.build(BASE)
            c = next(c for c in children if c.key == 'reference')
            if plant == 'truth':
                c.truth[1]['event_id'] = c.truth[0]['event_id']
            elif plant == 'case':
                c.quality[1]['case_id'] = c.quality[0]['case_id']
            elif plant == 'record link':
                next(r for r in c.records if r['truth_event_id'])['truth_event_id'] = 'C001-T999'
            elif plant == 'mention link':
                next(m for r in c.records for m in r.get('mentions', []) if m['truth_event_id'])['truth_event_id'] = 'C001-T999'
            elif plant == 'case link':
                next(q for q in c.quality if q['truth_event_id'])['truth_event_id'] = 'C001-T999'
            else:
                next(q for q in c.quality if q['record_ids'])['record_ids'].append('C001-R9999')
            with self.subTest(plant=plant), self.assertRaises(generate.IdError):
                generate.serialise(children, BASE)


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class SeedTests(unittest.TestCase):
    def test_every_sampled_seed_builds_and_the_store_accepts_it(self):
        from patterns import execution_store as es
        from patterns.synthetic_ingest import synthetic_ingest
        for seed in SEEDS:
            with self.subTest(seed=seed):
                cohort = json.loads(json.dumps(generate.serialise(generate.build(seed), seed), ensure_ascii=False))
                es.validate_ingest(synthetic_ingest(cohort))


if __name__ == '__main__':
    unittest.main()
