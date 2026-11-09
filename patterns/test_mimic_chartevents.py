"""Tests for chartevents route (b) and its sampled graph check, on synthetic fixtures only.

The fixture (`patterns/chartevents_fixture.py`, committed as examples/mimic-chartevents/) holds invented
patients, items and values; no MIMIC row is copied.
"""
import copy
import gzip
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import chartevents_fixture as fx
from . import exact_intervals as ei
from . import execution_store_reference as ref
from . import merkle_patients as mp
from . import mimic_chartevents as chart
from . import mimic_chartevents_check as check
from . import mimic_measurement_import as importer
from . import mimic_store_ingest as msi

DATASET = 'synthetic-chartevents-2.2'
TMP = None


def setUpModule():
    global TMP
    TMP = Path(tempfile.mkdtemp(prefix='chartevents-test-'))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def fixture(variant='full', gz=False):
    directory = TMP / f'{variant}-{"gz" if gz else "csv"}'
    if not directory.exists():
        fx.write(directory, variant)
        if gz:
            for name in ('chartevents.csv', 'icustays.csv', 'd_items.csv'):
                data = (directory / name).read_bytes()
                (directory / (name + '.gz')).write_bytes(gzip.compress(data, mtime=0))
                (directory / name).unlink()
    return directory


def batches(run, shards=3, patients=None):
    work = Path(tempfile.mkdtemp(dir=TMP))
    return list(run.batches(work, shards, patients))


def run_check(run, sampler_kwargs=None):
    """Bulk pass, sample, then route (a) on the sampled stores; returns (summary, outcomes, batches)."""
    out = batches(run)
    sampler = check.Sampler(DATASET, **(sampler_kwargs or {}))
    digests = {}
    for b in out:
        for s in b['stores']:
            sampler.offer(s['key'], s['strata'])
            digests[s['key']] = s['rows_sha256']
    selected = sampler.selected()
    work = Path(tempfile.mkdtemp(dir=TMP))
    paths = chart.spill(run.paths['chartevents'], run.manifest, work, 2)
    outcomes = []
    for pid, rows in chart.patient_rows(paths, run.manifest['columns']):
        mine = {k: v for k, v in selected.items() if k[0] == pid}
        if mine:
            outcomes += check.check_patient(run, pid, rows, mine, digests)
    return check.summarise(outcomes, sampler), outcomes, out


class FixtureTests(unittest.TestCase):
    def test_committed_fixture_is_the_generators(self):
        committed = ei.ROOT / 'examples/mimic-chartevents'
        for name in ('chartevents.csv', 'icustays.csv', 'd_items.csv'):
            self.assertEqual((committed / name).read_bytes(), (fixture() / name).read_bytes(), name)


class ReaderTests(unittest.TestCase):
    def test_manifest_equals_the_importers(self):
        for gz in (False, True):
            directory = fixture(gz=gz)
            paths = chart.source_paths(directory)
            manifest, itemids = chart.read_manifest(paths['chartevents'])
            _, want = importer.read_observations(paths['chartevents'])
            self.assertEqual(manifest, want)
            self.assertEqual(itemids, {r[6] for r in fx.chart_rows('full')})

    def test_file_changed_between_passes_is_refused(self):
        directory = TMP / 'changing'
        fx.write(directory)
        run = chart.Run(directory, DATASET)
        with (directory / 'chartevents.csv').open('a') as handle:
            handle.write('5000002,6000003,7000003,,2185-01-01 07:00:00,,910001,72,72,per min,0\n')
        with self.assertRaisesRegex(chart.ChartError, 'SOURCE_CHANGED_DURING_RUN:chartevents'):
            batches(run)

    def test_header_and_record_shape(self):
        directory = TMP / 'bad-header'
        fx.write(directory)
        text = (directory / 'chartevents.csv').read_text().replace('warning', 'warn', 1)
        (directory / 'chartevents.csv').write_text(text)
        with self.assertRaisesRegex(chart.ChartError, 'HEADER_MISMATCH:chartevents'):
            chart.Run(directory, DATASET)
        fx.write(directory)
        with (directory / 'chartevents.csv').open('a') as handle:
            handle.write('1,2,3\n')
        with self.assertRaisesRegex(chart.ChartError, 'MALFORMED_CSV_RECORD:chartevents:'):
            chart.Run(directory, DATASET)

    def test_no_row_limit(self):
        # The importer refuses more than 100,000 rows; the bulk reader streams any number.
        self.assertGreater(importer.MAX_ROWS, len(fx.chart_rows('full')))
        with mock.patch.object(importer, 'MAX_ROWS', 3):
            with self.assertRaisesRegex(ei.ContractError, 'ROW_LIMIT'):
                importer.read_observations(chart.source_paths(fixture())['chartevents'])
            run = chart.Run(fixture(), DATASET)
            self.assertEqual(run.manifest['rows'], len(fx.chart_rows('full')))


class ImporterEquivalenceTests(unittest.TestCase):
    """Where the importer can import the file whole, its ledger, origins and claim stores are ours."""

    def test_stores_ledger_and_origins(self):
        directory = fixture('importer')
        run = chart.Run(directory, DATASET)
        admitted_items = sorted({r[6] for r in fx.chart_rows('importer') if r[6].startswith('9100')})
        request = {'profile': importer.PROFILE, 'id': 'equivalence', 'dataset_id': DATASET,
                   'mode': 'retrospective_source_records', 'time_policy': 'recorded-point-label-exact-v1',
                   'patient_ids': 'all', 'itemids': admitted_items}
        result = importer.run(directory, request)
        self.assertEqual(result['summary']['status'], 'COMPLETED_PENDING_IMPORT')
        work = Path(tempfile.mkdtemp(dir=TMP))
        paths = chart.spill(run.paths['chartevents'], run.manifest, work, 2)
        ours_ledger, ours_stores, ours_origins = {}, {}, {}
        for pid, rows in chart.patient_rows(paths, run.manifest['columns']):
            entries = chart.admit_patient(rows, manifest=run.manifest, stays=run.stays, items=run.items,
                                          dataset_id=DATASET)
            for e in entries:
                ours_ledger[e['record_id']] = (e['outcome'], e['reasons'], e['duplicate_of'])
            origin = chart.origin_of(entries)
            if origin:
                ours_origins[pid] = origin
            groups = chart.store_groups(entries)
            keys = {(pid, stay, part) for stay, part in groups}
            for key, item in run.claim_stores(pid, rows, keys).items():
                self.assertEqual(key[2], 0)  # no stay exceeds 32 admitted rows in this variant
                ours_stores[key[1]] = item
        theirs = {e['record_id']: (e['admission_outcome'], e['reasons'], e['duplicate_of'])
                  for e in result['reconciliation']}
        self.assertEqual(ours_ledger, theirs)
        self.assertEqual(ours_origins, result['origins'])
        pending = [e for e in result['episodes'] if e['status'] == 'PENDING_MEASUREMENT_CLAIMS']
        self.assertEqual(sorted(e['episode_id'] for e in pending), sorted(ours_stores))
        for episode in pending:
            mine = ours_stores[episode['episode_id']]
            theirs_store = dict(episode['store'], snapshot_id=None)
            # The snapshot id names the run context (ours includes the store part), as in D15's converter.
            self.assertEqual(dict(mine['store'], snapshot_id=None), theirs_store)
            for claim in mine['store']['claims']:
                self.assertEqual(check.cr.digest(claim),
                                 episode['claim_provenance'][claim['id']]['claim_sha256'])


class BulkRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conv = chart.Run(fixture(), DATASET)
        cls.batches = {b['patient_id']: b for b in batches(cls.conv)}

    def test_patients_events_and_series(self):
        b = self.batches
        self.assertEqual(sorted(b), ['5000001', '5000002', '5000003', '5000004'])
        self.assertEqual([len(b[p]['patient']['events']) for p in sorted(b)], [47, 3, 0, 0])
        self.assertEqual([len(b[p]['stores']) for p in sorted(b)], [3, 1, 0, 0])
        self.assertEqual(sorted(s['claims'] for s in b['5000001']['stores']), [4, 11, 32])
        for batch in b.values():
            events, series = batch['patient']['events'], batch['series']
            self.assertEqual([e['event_id'] for e in events], [s['event_id'] for s in series])
            for e, s in zip(events, series):
                self.assertNotIn('end', e)
                self.assertNotIn('proper', e)
                self.assertEqual(e['start'][0], e['start'][1])
                self.assertEqual(e['concepts'], [s['concept']])
                self.assertEqual(e['kind'], chart.KIND)
                self.assertEqual(e['source'], chart.SOURCE)
                self.assertEqual(set(s), set(chart.SERIES_COLUMNS))

    def test_times_against_the_raw_rows(self):
        for batch in self.batches.values():
            for e, s in zip(batch['patient']['events'], batch['series']):
                number = e['evidence']['source_record_number']
                row = fx.chart_rows('full')[number - 1]
                self.assertEqual(e['start'], [msi.micros(row[4])] * 2)
                self.assertEqual(s['value_lexical'], row[8])
                self.assertEqual(s['unit'], row[9])
                self.assertEqual(e['evidence']['recorded_at_us'], msi.micros(row[5]) if row[5] else None)

    def test_values(self):
        values = {s['value_lexical']: (s['value_decimal'], s['value_micro'])
                  for b in self.batches.values() for s in b['series']}
        self.assertEqual(values['7.35'], ('7.35', 7_350_000))
        self.assertEqual(values['.5'], ('0.5', 500_000))
        self.assertEqual(values['5'], ('5', 5_000_000))
        self.assertEqual(values['-1.25'], ('-1.25', -1_250_000))
        self.assertEqual(values['1200'], ('1200', 1_200_000_000))
        self.assertEqual(values['1e-7'], ('0.0000001', None))  # not exact in micro-units: decimal text only
        self.assertEqual(chart.value_columns('1e20'), ('100000000000000000000', None))  # beyond BIGINT

    def test_excluded_records_and_counts(self):
        p1 = self.batches['5000001']
        reasons = sorted(x['reason'] for x in p1['patient']['excluded'])
        self.assertEqual(len(reasons), 8)
        self.assertIn('ITEM_TABLE_MISMATCH', reasons)
        self.assertIn('SOURCE_WARNING_FLAGGED', reasons)
        self.assertIn('MISSING_NUMERIC_VALUE|MISSING_UNIT|TEXTUAL_OR_QUALIFIED_VALUE', reasons)
        self.assertEqual(p1['counts']['excluded:rows_with_unknown_item'], 1)
        self.assertEqual(p1['counts']['excluded:duplicate_rows_skipped'], 1)
        self.assertEqual(p1['counts']['series_without_micro_value'], 1)
        self.assertEqual(len(self.batches['5000003']['patient']['excluded']), 1)
        self.assertNotIn('excluded', self.batches['5000004']['patient'])

    def test_coverage_widened_to_records(self):
        p1 = self.batches['5000001']['patient']['coverage']
        self.assertEqual(len(p1), 2)
        widened = [c for c in p1 if c['basis'] == chart.EXTENDED_COVERAGE_BASIS]
        self.assertEqual(len(widened), 1)
        self.assertEqual(widened[0]['lo'], msi.micros('2190-03-01 07:30:00'))
        p2 = self.batches['5000002']['patient']['coverage']
        self.assertEqual(p2[0]['hi'], msi.micros('2185-01-02 03:00:00'))
        self.assertEqual(len(self.batches['5000004']['patient']['coverage']), 1)
        self.assertEqual(self.batches['5000001']['counts']['events_outside_own_stay'], 1)

    def test_closure(self):
        a, b = self.conv.ancestors, self.conv.bases
        self.assertEqual(a[chart.ITEM_NS + '910001'], [chart.group_iri('Synthetic Vitals')])
        self.assertEqual(a[chart.ITEM_NS + '910004'], [])  # no category, no ancestor (D14)
        self.assertNotIn(chart.ITEM_NS + '999999', a)  # unknown to the dictionary
        self.assertEqual(b[chart.group_iri('Synthetic Labs')], chart.GROUP_BASIS)
        self.assertEqual(chart.GROUP_NS, msi.GROUP_NS)
        self.assertEqual(chart.group_iri('Synthetic Labs'), msi.group_iri('Synthetic Labs'))
        self.assertEqual(chart.MAX_TIME, msi.es.MAX_TIME)
        self.assertEqual(self.conv.unknown_items, 1)

    def test_store_patients_validate(self):
        from . import execution_store as es
        for batch in self.batches.values():
            es.validate_patient(copy.deepcopy(batch['patient']), self.conv.ancestors)

    def test_shard_count_and_file_order_do_not_matter(self):
        one = {b['patient_id']: b for b in batches(self.conv, shards=1)}
        many = {b['patient_id']: b for b in batches(self.conv, shards=7)}
        for pid in self.batches:
            for other in (one, many):
                self.assertEqual(other[pid]['patient'], self.batches[pid]['patient'])
                self.assertEqual(other[pid]['series'], self.batches[pid]['series'])
                self.assertEqual(other[pid]['stores'], self.batches[pid]['stores'])

    def test_patient_subset(self):
        some = batches(self.conv, patients={'5000002', '5000004'})
        self.assertEqual(sorted(b['patient_id'] for b in some), ['5000002', '5000004'])
        self.assertEqual(some[0]['patient'], self.batches[some[0]['patient_id']]['patient'])

    def test_merkle_root_is_free_of_shard_plan(self):
        roots = set()
        for shards in (1, 4):
            pairs = [(b['patient_id'], ref.patient_digest(b['patient'])) for b in batches(self.conv, shards=shards)]
            roots.add(mp.patient_root(pairs))
        self.assertEqual(len(roots), 1)


class GraphCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conv = chart.Run(fixture(), DATASET)
        cls.summary, cls.outcomes, cls.batches = run_check(cls.conv, {'rate': 1.0})

    def test_every_store_equal_through_the_graph(self):
        s = self.summary
        self.assertEqual(s['stores_checked'], 4)
        self.assertEqual(s['claims_checked'], 50)
        self.assertEqual((s['stores_equal'], s['mismatched_stores']), (4, 0))
        self.assertEqual(s['stores_with_bulk_digest_mismatch'], 0)
        self.assertEqual(s['isolation_statuses'], {'VERIFIED_EMPTY_PROCESS_MODEL': 4})
        self.assertIn('not each validated through the graph', s['statement'])

    def test_compare_names_fields_not_values(self):
        work = Path(tempfile.mkdtemp(dir=TMP))
        paths = chart.spill(self.conv.paths['chartevents'], self.conv.manifest, work, 1)
        pid, rows = next((p, r) for p, r in chart.patient_rows(paths, self.conv.manifest['columns']) if p == '5000002')
        (key, item), = self.conv.claim_stores(pid, rows, {(pid, '7000003', 0)}).items()
        bulk = chart.bulk_rows(item['chunk'], item['origin'], pid, DATASET)
        graph, _ = check.graph_rows(item['store'], item['provenance'])
        self.assertEqual(check.compare(bulk, graph), (True, []))
        tampered = copy.deepcopy(graph)
        tampered[0][0]['start'] = [tampered[0][0]['start'][0] + 1] * 2
        tampered[1][1]['unit'] = 'other'
        tampered[2][0]['evidence']['claim_sha256'] = '0' * 64
        self.assertEqual(check.compare(bulk, tampered),
                         (False, ['event.start', 'evidence.claim_sha256', 'series.unit']))
        self.assertEqual(check.compare(bulk, graph[:-1]), (False, ['row_count']))

    def test_a_defective_bulk_builder_is_caught(self):
        # A bulk builder that ignored the clock origin's seconds, or rounded values, must fail the check.
        original = chart.claim_fields

        def shifted(claim, clock):
            fields = original(claim, clock)
            fields['start_offset_us'] = [v + 60_000_000 for v in fields['start_offset_us']]
            return fields

        with mock.patch.object(chart, 'claim_fields', shifted):
            summary, outcomes, _ = run_check(self.conv, {'rate': 1.0})
        self.assertEqual(summary['mismatched_stores'], 4)
        self.assertEqual(summary['differing_fields'], {'event.start': 4})

    def test_ingest_rows_must_be_the_rows_checked(self):
        with mock.patch.object(chart, 'rows_digest', lambda rows: 'recorded-at-ingest'):
            out = batches(self.conv)
        digests = {s['key']: s['rows_sha256'] for b in out for s in b['stores']}
        work = Path(tempfile.mkdtemp(dir=TMP))
        paths = chart.spill(self.conv.paths['chartevents'], self.conv.manifest, work, 1)
        pid, rows = next((p, r) for p, r in chart.patient_rows(paths, self.conv.manifest['columns']) if p == '5000002')
        (result,) = check.check_patient(self.conv, pid, rows, {(pid, '7000003', 0): ['rate']}, digests)
        self.assertFalse(result['bulk_rows_match_ingest'])
        self.assertFalse(result['equal'])

    def test_graph_route_refuses_a_changed_store(self):
        work = Path(tempfile.mkdtemp(dir=TMP))
        paths = chart.spill(self.conv.paths['chartevents'], self.conv.manifest, work, 1)
        pid, rows = next((p, r) for p, r in chart.patient_rows(paths, self.conv.manifest['columns']) if p == '5000002')
        (_, item), = self.conv.claim_stores(pid, rows, {(pid, '7000003', 0)}).items()
        store = copy.deepcopy(item['store'])
        store['claims'][0]['bundle']['events'][0]['value_lexical'] = 'not a number'
        with self.assertRaises(ei.ContractError):
            check.graph_rows(store, item['provenance'])


class SamplerTests(unittest.TestCase):
    def stores(self, n=400, seed=0):
        rng = random.Random(seed)
        out = []
        for i in range(n):
            key = (f'P{rng.randrange(60):03d}', f'S{i}', i % 3)
            strata = [('item_unit', str(rng.randrange(40)), 'u'), ('value_shape', rng.choice(('int', 'frac', '-frac')))]
            out.append((key, strata))
        return out

    def test_order_free_and_deterministic(self):
        items = self.stores()
        a = check.Sampler(DATASET)
        for key, strata in items:
            a.offer(key, strata)
        b = check.Sampler(DATASET)
        for key, strata in reversed(items):
            b.offer(key, strata)
        self.assertEqual(a.selected(), b.selected())
        c = check.Sampler(DATASET, salt='another salt')
        for key, strata in items:
            c.offer(key, strata)
        self.assertNotEqual(a.selected(), c.selected())

    def test_rules(self):
        items = self.stores()
        sampler = check.Sampler(DATASET, rate=0.05, per_batch=3)
        for key, strata in items:
            sampler.offer(key, strata)
        chosen = sampler.selected()
        ranks = {key: check.rank(DATASET, key) for key, _ in items}
        # Rate: exactly the stores whose rank falls below the threshold.
        self.assertEqual({k for k, r in chosen.items() if 'rate' in r},
                         {k for k in ranks if ranks[k] < int(0.05 * (1 << 256))})
        # Batch minimum: the three lowest ranks of each bucket.
        by_batch = {}
        for key in ranks:
            by_batch.setdefault(check.batch_of(key), []).append(key)
        want = {k for keys in by_batch.values() for k in sorted(keys, key=lambda k: (ranks[k], k))[:3]}
        self.assertEqual({k for k, r in chosen.items() if 'batch_minimum' in r}, want)
        # Strata: every stratum is held by some sampled store, the lowest-ranked one holding it.
        for stratum in {tuple(s) for _, strata in items for s in strata}:
            holders = [k for k, strata in items if stratum in {tuple(s) for s in strata}]
            self.assertIn('stratum', chosen[min(holders, key=lambda k: (ranks[k], k))])
        summary = sampler.summary()
        self.assertEqual(summary['stores_offered'], len(items))
        self.assertEqual(summary['stores_sampled'], len(chosen))

    def test_nothing_when_every_rule_is_off(self):
        sampler = check.Sampler(DATASET, rate=0, per_batch=0, stratify=False)
        for key, strata in self.stores(50):
            sampler.offer(key, strata)
        self.assertEqual(sampler.selected(), {})
        with self.assertRaisesRegex(check.CheckError, 'INVALID_SAMPLE_PARAMETERS'):
            check.Sampler(DATASET, rate=2)


class StoreSmokeTests(unittest.TestCase):
    """The route-(b) patients build into today's store (without the series table, which waits for the
    store hook) and match as the reference matches them, points included."""

    def test_build_and_match_points(self):
        from . import execution_store as es
        run = chart.Run(fixture(), DATASET)
        patients = [b['patient'] for b in batches(run)]
        ingest = {**run.header(), 'provenance': {'labels': chart.LABELS,
                                                 'selection': {'mode': 'retrospective', 'version': 'as_released',
                                                               'available_at': 'not_applied'}},
                  'patients': patients}
        snapshot = es.build(ingest, TMP / 'store')
        item = chart.ITEM_NS
        queries = [
            {'profile': 'trajectory-graph-1.0', 'scope': 'any', 'steps': [
                {'id': 'A', 'concept': item + '910002', 'occurrence': 'first'},
                {'id': 'B', 'concept': item + '910001', 'occurrence': 'any'}],
             'relations': [{'id': 'R1', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 120 * 60_000_000}],
             'relaxations': [], 'budget': 0},
            {'profile': 'trajectory-graph-1.0', 'scope': 'within', 'steps': [
                {'id': 'A', 'concept': chart.group_iri('Synthetic Labs'), 'occurrence': 'any'},
                {'id': 'B', 'concept': item + '910003', 'occurrence': 'any'}],
             'relations': [{'id': 'R1', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0}],
             'relaxations': [], 'budget': 0},
        ]
        windows = {p['patient_id']: {'start': 0, 'end': chart.MAX_TIME} for p in patients}
        with es.open_store(snapshot, verify='content') as store:
            for query in queries:
                got = es.match(store, query, windows)['results']
                want = ref.exhaustive_match(ingest, query, windows)
                # As the demo verifier compares: every status, and in full where the store ran the engine.
                self.assertEqual([g['status'] for g in got], [w['status'] for w in want])
                for g, w in zip(got, want):
                    if g.get('reason') != 'FILTERED':
                        self.assertEqual(g, w)
            first = [r['status'] for r in es.match(store, queries[0], windows)['results']]
            second = es.match(store, queries[1], windows)['results']
        self.assertEqual(first, ['exact', 'exact', 'none', 'none'])  # a lab, then a rate within two hours
        # Item 910003 appears only in rows admission left out: no source records it.
        self.assertEqual({r['status'] for r in second}, {'not_comparable'})
        self.assertEqual({r['reason'] for r in second}, {'NO_SOURCE_RECORDS_CONCEPT'})


class CommandLineTests(unittest.TestCase):
    def test_counts_only(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(chart.main(['--input-dir', str(fixture()), '--dataset-id', DATASET, '--shards', '2']), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['counts']['events'], 50)
        self.assertNotIn('5000001', out.getvalue())

    def test_sampled_run(self):
        result = check.sampled_run(fixture(), DATASET, rate=0.0, per_batch=1, shards=2)
        g = result['graph_check']
        self.assertEqual((g['stores_checked'], g['mismatched_stores']), (g['stores_equal'], 0))
        self.assertGreaterEqual(g['stores_checked'], 2)
        self.assertEqual(result['counts']['events'], 50)
        self.assertEqual(result['patients'], 4)
        text = json.dumps({k: v for k, v in result.items() if k != '_merkle_root_local_only'})
        for pid in ('5000001', '5000002', '7000001', '2190-03'):
            self.assertNotIn(pid, text)
        some = check.sampled_run(fixture(), DATASET, patients=2, graph=False)
        self.assertEqual(some['patients'], 2)
        self.assertEqual(check.patient_subset(['a', 'b', 'c', 'd'], 2), check.patient_subset(['d', 'c', 'b', 'a'], 2))


if __name__ == '__main__':
    unittest.main()
