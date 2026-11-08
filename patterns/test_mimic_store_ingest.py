"""MIMIC inputevents into the execution store through the SULO adapter, on the synthetic fixture.

`examples/mimic-store-ingest/` is synthetic and MIMIC-shaped: four patients, five ICU stays, 69
inputevents rows, one stay with more claims than a record claim store holds, an item with no
dictionary category and an event that starts before its ICU admission. Nine rows are not admitted:
boluses, drug pushes, a missing rate, an unsupported status, an exact duplicate, an unreadable start
and an unknown item; one item (medication F) appears only in them. No patient data is read.
"""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import claim_rdf as cr
from . import exact_intervals as ei
from . import execution_store as es
from . import execution_store_reference as ref
from . import mimic_claim_import as importer
from . import mimic_store_ingest as msi
from . import trajectory_graph as tg
from . import trajectory_graph_sulo as tgs
from . import verify_mimic_store_ingest as verify

MINUTE = 60_000_000
ITEM, GROUP = importer.ITEM_NS, msi.GROUP_NS
KIND = 'https://example.org/trajectory/bounded/RecordedInputSegment'
FIXTURE = ei.ROOT / msi.FIXTURE
STATE = {}


def setUpModule():
    STATE['tmp'] = tempfile.mkdtemp(prefix='mimic-store-test-')
    result = msi.convert(FIXTURE, msi.FIXTURE_DATASET)
    snapshot = es.build(result['ingest'], Path(STATE['tmp']) / 'store')
    STATE.update(result=result, snapshot=snapshot, store=es.open_store(snapshot, verify='content'))


def tearDownModule():
    STATE['store'].close()
    shutil.rmtree(STATE['tmp'], ignore_errors=True)


def result():
    return STATE['result']


def segments():
    return {s['id']: s for s in result()['prepared']['admission']['segments']}


def ingest_patient(pid):
    return next(p for p in result()['ingest']['patients'] if p['patient_id'] == pid)


def store_for(pid):
    return [s for s in result()['prepared']['stores'] if s['patient_id'] == pid]


class RouteTests(unittest.TestCase):
    def test_every_admitted_row_goes_through_claims_acceptance_and_the_graph_route(self):
        r = result()
        self.assertEqual(r['counts']['admission_outcomes']['STAGED_SEGMENT'], 60)
        self.assertEqual(r['counts']['claims'], 60)
        self.assertEqual(len(r['rows']), 60)
        self.assertEqual(r['route']['routes'], {'local-claim-selection-rdf': 5})
        self.assertEqual(r['route']['accepted_claims'], 60)
        self.assertEqual(r['route']['isolation_certified'], 5)
        steps = r['ingest']['provenance']['route']['steps']
        self.assertEqual(steps[:2], ['mimic_inputevents.stage', 'mimic_claim_import.describe'])
        self.assertIn('trajectory_graph_sulo.ingest_rows', steps)

    def test_a_stay_beyond_the_record_store_limit_is_split_in_record_order(self):
        parts = [s for s in store_for('11') if s['episode_id'] == '1101']
        self.assertEqual([len(s['store']['claims']) for s in parts], [32, 11])
        numbers = [int(c['source_record_id'].rsplit('_', 1)[1]) for s in parts for c in s['store']['claims']]
        self.assertEqual(numbers, sorted(numbers))
        self.assertTrue(all(len(s['store']['claims']) <= msi.CLAIMS_PER_STORE for s in result()['prepared']['stores']))
        # One clock per patient across all of its stores.
        self.assertEqual(len({json.dumps(s['store']['clocks']) for s in store_for('11')}), 1)

    def test_stores_equal_the_importers_for_a_stay_under_the_limit(self):
        request = {'profile': importer.PROFILE, 'id': 'store_ingest_check', 'dataset_id': msi.FIXTURE_DATASET,
                   'mode': 'retrospective_source_records', 'time_policy': 'recorded-label-exact-v1',
                   'patient_ids': ['12'], 'itemids': ['2001', '2002', '2003']}
        run = importer.run(FIXTURE, request)
        episode = next(e for e in run['episodes'] if e['status'] == 'PENDING_CLAIMS')
        mine = store_for('12')
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['store']['claims'], episode['store']['claims'])
        self.assertEqual(mine[0]['store']['clocks'], episode['store']['clocks'])
        for claim_id, provenance in episode['claim_provenance'].items():
            self.assertEqual(result()['prepared']['claim_provenance'][claim_id], provenance)

    def test_the_acceptance_policy_is_caller_declared_and_accepts_every_claim(self):
        entry = store_for('12')[0]
        policy = msi.accept_all(entry['store'], entry['semantic_policy'])
        self.assertEqual(policy['id'], msi.POLICY_ID)
        self.assertEqual({d['claim_id'] for d in policy['decisions']}, {c['id'] for c in entry['store']['claims']})
        self.assertTrue(all(d['action'] == 'accept' and 'no clinical review' in d['reason'] for d in policy['decisions']))


class MappingTests(unittest.TestCase):
    def test_store_rows_equal_the_adapter_rows_mapped(self):
        store, rows = STATE['store'], result()['rows']
        index = {pid: i for i, pid, *_ in store.rows("patients")}
        expected_events, expected_concepts = set(), set()
        for r in rows:
            origin = msi.micros(r['clock_origin'])
            i = index[r['patient_id']]
            expected_events.add((i, r['event_id'], r['event_kind'], msi.SOURCE, r['clock_id'],
                                 origin + r['start_us'][0], origin + r['start_us'][1],
                                 origin + r['end_us'][0], origin + r['end_us'][1], False, r['start_var'], r['end_var'], True))
            facts = [c['concept'] for c in r['concepts'] if any(b['kind'] == 'semantic_fact' for b in c['basis'])]
            for concept in {r['kind_class'], *facts}:
                expected_concepts.add((i, r['event_id'], concept, 'direct'))
        self.assertEqual({row[:-1] for row in store.rows('events')}, expected_events)
        self.assertEqual(set(store.rows('event_concepts')), expected_concepts)

    def test_every_table_equals_the_reference_row_for_row(self):
        store, ingest = STATE['store'], result()['ingest']
        tables = ref.build_tables(ingest)
        for name in es.TABLES:
            self.assertEqual(store.rows(name), [tuple(r) for r in tables[name]], name)
        digests = ref.table_digests(tables)
        for name in es.TABLES:
            recorded = store.manifest['tables'][name]
            self.assertEqual({k: recorded[k] for k in ('rows', 'content_digest', 'buckets')}, digests[name], name)
        self.assertEqual(store.manifest['input_digest'], ref.input_digest(ingest))

    def test_closure_reaches_exactly_the_adapter_concepts_plus_groupings(self):
        ancestors = result()['ingest']['closure']['ancestors']
        for r in result()['rows']:
            event = next(e for e in ingest_patient(r['patient_id'])['events'] if e['event_id'] == r['event_id'])
            expanded = ref.expanded(event['concepts'], ancestors)
            self.assertEqual({c for c in expanded if not c.startswith(GROUP)}, {c['concept'] for c in r['concepts']})

    def test_times_are_microseconds_from_the_fixed_origin(self):
        by_key = segments()
        for p in result()['ingest']['patients']:
            for e in p['events']:
                segment = by_key[e['evidence']['source_key']]
                self.assertEqual(e['start'], [msi.micros(segment['start']['raw_value'])] * 2)
                self.assertEqual(e['end'], [msi.micros(segment['end']['raw_value'])] * 2)
                self.assertEqual(e['start'][0], e['evidence']['clock_origin_us'] + e['evidence']['declared_start_us'][0])
        self.assertEqual(msi.micros('0001-01-02T00:00:00'), 86_400_000_000)

    def test_evidence_carries_claim_identity_and_recorded_time(self):
        by_key = segments()
        for p in result()['ingest']['patients']:
            for e in p['events']:
                ev, segment = e['evidence'], by_key[e['evidence']['source_key']]
                claim = importer.describe(segment, msi.FIXTURE_DATASET)
                self.assertEqual(ev['claim_id'], claim['id'])
                self.assertEqual(ev['claim_sha256'], cr.digest(claim))
                self.assertEqual(ev['recorded_at_us'], msi.micros(segment['recorded_at']['raw_value']))
                self.assertIsNone(ev['available_at_us'])
                self.assertEqual(ev['history_mode'], 'retrospective')
                self.assertFalse(ev['clinical_mapping_verified'])
                self.assertEqual(ev['source_row_sha256'], segment['source']['row_sha256'])
                self.assertEqual(ev['selection_state'], 'accepted')


class LabelTests(unittest.TestCase):
    def test_concepts_are_source_item_classes_never_clinical(self):
        manifest = STATE['store'].manifest
        self.assertEqual(manifest['labels']['concept_basis'], 'source_item_class')
        self.assertIs(manifest['labels']['clinical_mapping_verified'], False)
        self.assertEqual(manifest['labels']['ancestor_basis'], 'source_dictionary_grouping')
        self.assertTrue(manifest['dataset_id'].endswith(':source_item_class'))
        direct = {r[2] for r in STATE['store'].rows('event_concepts')}
        self.assertEqual(direct, {KIND} | {ITEM + i for i in ('2001', '2002', '2003', '2004')})

    def test_item_classes_take_their_dictionary_category_as_their_only_ancestor(self):
        ancestors = result()['ingest']['closure']['ancestors']
        self.assertEqual(ancestors[ITEM + '2001'], [GROUP + 'Fluids%2FIntake'])
        self.assertEqual(ancestors[ITEM + '2003'], [GROUP + 'Medications'])
        self.assertEqual(ancestors[ITEM + '2004'], [])  # no category: no grouping is invented
        self.assertEqual(ancestors[GROUP + 'Medications'], [])
        self.assertNotIn(ITEM + '2005', ancestors)  # a chartevents item no row carries
        self.assertEqual(ancestors[ITEM + '2006'], [GROUP + 'Medications'])  # only in rows not admitted
        self.assertNotIn(ITEM + '2999', ancestors)  # not in the dictionary: no concept is invented
        bases = result()['ingest']['provenance']['ancestor_bases']
        self.assertEqual(bases['source_item_class'], 5)
        self.assertEqual(bases['source_dictionary_grouping'], 2)
        self.assertEqual(result()['counts']['item_classes'], 5)

    def test_closure_rows_say_what_kind_of_concept_each_side_is(self):
        rows = {(r[0], r[1]): r for r in STATE['store'].rows('concept_closure')}
        item = rows[(ITEM + '2001', GROUP + 'Fluids%2FIntake')]
        self.assertEqual(item[2:4], ('declared', result()['ingest']['closure']['version']))
        self.assertEqual(item[4:], ('source_item_class', 'source_dictionary_grouping'))
        self.assertEqual(rows[(KIND, KIND)][4:], ('ontology_closure', 'ontology_closure'))
        self.assertEqual({r[4] for r in rows.values()}, {'source_item_class', 'source_dictionary_grouping',
                                                          'ontology_closure'})

    def test_structural_classes_are_refused_since_excluded_records_cannot_carry_them(self):
        query = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'K', 'concept': KIND}]}
        with self.assertRaises(es.StoreError) as caught:
            es.match(STATE['store'], query, {})
        self.assertEqual(caught.exception.code, 'STRUCTURAL_CONCEPT_EXCLUSION_UNKNOWN')
        self.assertEqual(STATE['store'].manifest['labels']['structural_concept_basis'], 'ontology_closure')

    def test_labels_name_the_admitted_subset(self):
        labels = STATE['store'].manifest['labels']
        self.assertEqual(labels['source'], 'inputevents:continuous_segments')
        self.assertEqual(labels['admitted_statuses'], 'ChangeDose/Rate|FinishedRunning|Paused|Stopped')
        self.assertEqual(labels['admitted_order_categories'], 'Continuous IV|Continuous Med')
        self.assertEqual({r[3] for r in STATE['store'].rows('events')}, {msi.SOURCE})

    def test_match_results_carry_the_labels(self):
        out = es.match(STATE['store'], MatchingTests.QUERY, MatchingTests().windows())
        self.assertEqual(out['labels'], STATE['store'].manifest['labels'])
        self.assertEqual(out['context']['labels'], STATE['store'].manifest['labels'])
        self.assertIs(out['labels']['clinical_mapping_verified'], False)

    def test_labels_must_be_flat_strings_or_booleans(self):
        ingest = deepcopy(result()['ingest'])
        ingest['provenance']['labels'] = {'clinical_mapping_verified': None}
        with self.assertRaises(es.IngestError) as caught:
            es.validate_ingest(ingest)
        self.assertEqual(caught.exception.code, 'INVALID_LABELS')


class CoverageTests(unittest.TestCase):
    def test_coverage_is_one_period_per_icu_stay(self):
        p11 = ingest_patient('11')
        self.assertEqual([(c['lo'], c['hi']) for c in p11['coverage']],
                         [(msi.micros('2150-01-01 08:00:00'), msi.micros('2150-01-05 08:00:00')),
                          (msi.micros('2150-03-01 08:00:00'), msi.micros('2150-03-03 08:00:00'))])
        self.assertTrue(all(c['source'] == msi.SOURCE for p in result()['ingest']['patients'] for c in p['coverage']))
        self.assertEqual([c['basis'] for p in result()['ingest']['patients'] for c in p['coverage']].count(
            'inferred:icustay'), 4)
        self.assertEqual(result()['counts']['coverage_periods'], 5)

    def test_a_patient_with_stays_but_no_admitted_rows_is_kept_with_coverage(self):
        p13 = ingest_patient('13')
        self.assertEqual(p13['events'], [])
        self.assertEqual(len(p13['coverage']), 1)
        self.assertEqual(result()['counts']['patients_with_events'], 3)
        self.assertEqual(result()['counts']['patients_in_store'], 4)

    def test_a_stay_is_extended_to_its_own_admitted_records(self):
        # Patient 14's first medication starts an hour before the recorded ICU admission.
        (period,) = ingest_patient('14')['coverage']
        self.assertEqual((period['lo'], period['hi'], period['basis']),
                         (msi.micros('2152-01-01 11:00:00'), msi.micros('2152-01-03 12:00:00'), 'inferred:icustay+records'))
        counts = result()['counts']
        self.assertEqual((counts['events_outside_own_stay'], counts['coverage_periods_extended_by_records']), (1, 1))
        self.assertEqual(counts['minutes_outside_own_stay'], {'max': 60, 'median': 60})
        self.assertEqual(len(ingest_patient('14')['events']), 4)


class ExcludedRecordTests(unittest.TestCase):
    def excluded(self, pid):
        return ingest_patient(pid).get('excluded', [])

    def test_every_row_admission_leaves_out_is_kept_as_a_concept_and_a_start(self):
        counts = result()['counts']['excluded']
        self.assertEqual(result()['counts']['admission_outcomes'],
                         {'STAGED_SEGMENT': 60, 'UNSUPPORTED_ROW': 6, 'INVALID_ROW': 2, 'DUPLICATE_ROW': 1})
        self.assertEqual((counts['rows'], counts['duplicate_rows_skipped'], counts['rows_with_unknown_item']), (7, 1, 1))
        self.assertEqual(self.excluded('14'), [{'source': msi.SOURCE, 'concept': ITEM + '2002',
                                                'start': [msi.micros('2152-01-02 14:00:00')] * 2,
                                                'reason': 'UNSUPPORTED_ORDER_CATEGORY'}])
        self.assertEqual(len(self.excluded('11')), 1)  # the duplicate adds nothing
        self.assertEqual(len(STATE['store'].rows('excluded_records')), 7)
        self.assertEqual(counts['item_classes_only_in_excluded_records'], 1)
        self.assertEqual(counts['patients_without_admitted_rows_with_excluded_records'], 1)

    def test_an_unreadable_start_covers_all_time(self):
        unreadable = [x for x in self.excluded('12') if x['start'] == [0, es.MAX_TIME]]
        self.assertEqual([x['concept'] for x in unreadable], [ITEM + '2001'])
        self.assertIn('INVALID_LOCAL_TIMESTAMP:starttime', unreadable[0]['reason'])

    def test_a_none_an_excluded_record_could_change_is_not_comparable(self):
        query = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'B', 'concept': ITEM + '2002'}]}
        stay = ingest_patient('14')['coverage'][0]
        for window, status in (({'start': stay['lo'], 'end': stay['hi']}, 'not_comparable'),
                               ({'start': stay['lo'], 'end': msi.micros('2152-01-02 13:00:00')}, 'none')):
            got = next(r for r in es.match(STATE['store'], query, {'14': window})['results'] if r['patient_id'] == '14')
            self.assertEqual(got['status'], status)
            if status == 'not_comparable':
                self.assertEqual(got, {'patient_id': '14', 'status': 'not_comparable',
                                       'reason': 'ADMISSION_EXCLUDED_RECORDS', 'excluded_steps': ['B']})
        # Through the grouping too: fluid B is a Fluids/Intake item.
        query['steps'][0]['concept'] = GROUP + 'Fluids%2FIntake'
        windows = {'14': {'start': msi.micros('2152-01-02 13:00:00'), 'end': msi.micros('2152-01-02 15:00:00')}}
        got = es.match(STATE['store'], query, windows)['results']
        self.assertEqual(next(r for r in got if r['patient_id'] == '14')['reason'], 'ADMISSION_EXCLUDED_RECORDS')
        self.assertEqual([r['status'] for r in got], [w['status'] for w in ref.exhaustive_match(result()['ingest'], query, windows)])

    def test_an_item_only_in_excluded_records_is_known_and_never_a_plain_none(self):
        query = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'F', 'concept': ITEM + '2006'}]}
        windows = {p['patient_id']: {'start': 0, 'end': es.MAX_TIME} for p in result()['ingest']['patients']}
        got = es.match(STATE['store'], query, windows)['results']
        self.assertEqual({r['status'] for r in got}, {'not_comparable'})
        self.assertEqual(got, ref.exhaustive_match(result()['ingest'], query, windows))


class RefusalTests(unittest.TestCase):
    def non_box_rows(self):
        """Patient 12's store with one source constraint a box cannot hold, through the graph route."""
        entry = deepcopy(store_for('12')[0])
        store = entry['store']
        first, second = store['claims'][0]['bundle'], store['claims'][1]['bundle']
        end0 = next(v for v in first['variables'] if v['id'].endswith('_end'))
        start1 = next(v for v in second['variables'] if v['id'].endswith('_start'))
        end0['local_upper'] = '2151-06-10 09:30:00'   # recorded 08:50, now 08:50 to 09:30
        start1['local_lower'] = '2151-06-10 08:40:00'  # recorded 09:00, now 08:40 to 09:00
        second['constraints'].append({'id': 'starts_before_previous_ends', 'left_var': start1['id'],
                                      'right_var': end0['id'], 'upper_us': 0, 'source_key': 'synthetic-test'})
        rows, _ = msi.adapter_rows(store, entry['semantic_policy'], isolation=False)
        return rows

    def test_non_box_patients_are_refused_and_counted(self):
        rows = self.non_box_rows()
        self.assertTrue(all(not r['box_representable'] and r['non_box_constraints'] for r in rows))
        others = [r for r in result()['rows'] if r['patient_id'] != '12']
        prepared = result()['prepared']
        patients, _, _, refused, counts = msi.store_patients(
            others + rows, claim_provenance=prepared['claim_provenance'], stays=prepared['stays'], items=prepared['items'])
        self.assertNotIn('12', {p['patient_id'] for p in patients})
        self.assertEqual(refused[msi.REFUSAL], {'patients': 1,
                                                'by_adapter_reason': {'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE': 1}})
        self.assertEqual(counts['events_refused'], len(rows))
        self.assertEqual({p['patient_id'] for p in patients}, {'11', '13', '14'})

    def test_a_variable_shared_across_stores_is_kept_and_shared_in_the_store(self):
        # Before D24 a time variable named by rows in two of a patient's stores refused the patient
        # (SHARED_SOURCE_VARIABLE). The store now keeps every event's variables, so the patient is kept and
        # the two events share one start. Patient 11's first 2001 infusion (first store) and its first 2002
        # infusion (third store) are given one uncertain start: `after` 0 to 0 between them is exact
        # through the shared variable and only possible without it, and the store decides as the engine
        # does on the adapter's rows directly.
        prepared = result()['prepared']
        query = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'a', 'concept': ITEM + '2001', 'occurrence': 'first'},
                                                      {'id': 'b', 'concept': ITEM + '2002', 'occurrence': 'first'}],
                 'relations': [{'id': 'R', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 0, 'max': 0}]}
        window = {'start': 0, 'end': es.MAX_TIME}
        statuses = {}
        for share in (True, False):
            rows = deepcopy(result()['rows'])
            p11 = {r['event_id'].rsplit('_', 1)[1]: r for r in rows if r['patient_id'] == '11'}
            first, other = p11['1'], p11['44']
            self.assertNotEqual(first['snapshot_context_id'], other['snapshot_context_id'])
            for r in (first, other):
                r['start_us'] = [0, 10 * MINUTE]
            if share:
                other['start_var'] = first['start_var']
            patients, ancestors, bases, refused, _ = msi.store_patients(
                rows, claim_provenance=prepared['claim_provenance'], stays=prepared['stays'], items=prepared['items'])
            self.assertEqual(refused[msi.REFUSAL], {'patients': 0, 'by_adapter_reason': {}})
            p = next(p for p in patients if p['patient_id'] == '11')
            self.assertEqual(es.shares_variables(p['events']), share)
            self.assertTrue(all(e['proper'] for e in p['events']))
            self.assertEqual(sum(e['start_var'] == first['start_var'] for e in p['events']), 2 if share else 1)
            ingest = msi.ingest_document(patients, ancestors, bases, dataset_id='shared-across-stores',
                                         provenance=result()['ingest']['provenance'])
            engine_input = verify.adapter_engine_input(rows, prepared['items'])
            with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp)) as store:
                self.assertEqual(store.query("SELECT shared_variables FROM patients WHERE patient_id = '11'"), [(share,)])
                got = next(r for r in es.match(store, query, {'11': window})['results'] if r['patient_id'] == '11')
            self.assertEqual(got, tg.match(query, {'patient_id': '11', 'window': window, 'events': engine_input['11']}))
            statuses[share] = got['status']
        self.assertEqual(statuses, {True: 'exact', False: 'possible'})

    def test_the_store_itself_refuses_a_patient_declared_not_box_representable(self):
        ingest = deepcopy(result()['ingest'])
        ingest['patients'][0]['box_representable'] = False
        with self.assertRaises(es.IngestError) as caught:
            es.validate_ingest(ingest)
        self.assertEqual(caught.exception.code, 'INGEST_PATIENT_NOT_BOX_REPRESENTABLE')

    def test_a_source_row_repeated_across_stores_is_refused(self):
        rows = deepcopy(result()['rows'])
        twin = deepcopy(next(r for r in rows if r['patient_id'] == '12'))
        twin['event_id'] += '_again'
        prepared = result()['prepared']
        with self.assertRaises(msi.MimicStoreError) as caught:
            msi.store_patients(rows + [twin], claim_provenance=prepared['claim_provenance'], stays=prepared['stays'],
                               items=prepared['items'])
        self.assertEqual(str(caught.exception), 'DUPLICATE_SOURCE_ROW_ACROSS_STORES:record_id')

    def test_the_fixture_has_no_refusals(self):
        self.assertEqual(result()['refused'][msi.REFUSAL]['patients'], 0)


class MatchingTests(unittest.TestCase):
    QUERY = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'fluid', 'concept': ITEM + '2001'},
                                                  {'id': 'drug', 'concept': GROUP + 'Medications'}],
             'relations': [{'id': 'R1', 'kind': 'after', 'from': 'fluid', 'to': 'drug', 'min': 0, 'max': 60 * MINUTE}],
             'relaxations': [{'id': 'wider', 'kind': 'extend_max', 'relation': 'R1', 'max': 600 * MINUTE,
                              'cost': '0.5'}], 'budget': '1'}

    def windows(self):
        return {p['patient_id']: {'start': min(c['lo'] for c in p['coverage']), 'end': max(c['hi'] for c in p['coverage'])}
                for p in result()['ingest']['patients']}

    def test_store_match_equals_exhaustive_and_the_adapters_own_matching(self):
        windows = self.windows()
        got = es.match(STATE['store'], self.QUERY, windows)
        want = ref.exhaustive_match(result()['ingest'], self.QUERY, windows)
        for g, w in zip(got['results'], want, strict=True):
            # A patient the candidate filter dropped is `none` with reason FILTERED; any other result is identical.
            self.assertEqual(g['status'], w['status'])
            if g.get('reason') != 'FILTERED':
                self.assertEqual(g, w)
        parallel = es.match(STATE['store'], self.QUERY, windows, workers=2, chunk=1)
        self.assertEqual(json.dumps(parallel, sort_keys=True), json.dumps(got, sort_keys=True))
        statuses = {r['patient_id']: r['status'] for r in got['results']}
        # Patient 13 has no admitted rows, but its source recorded both items in rows admission left out.
        self.assertEqual(statuses, {'11': 'exact', '12': 'exact', '13': 'not_comparable', '14': 'relaxed'})
        p13 = next(r for r in got['results'] if r['patient_id'] == '13')
        self.assertEqual((p13['reason'], p13['excluded_steps']), ('ADMISSION_EXCLUDED_RECORDS', ['fluid', 'drug']))
        # The adapter's own matching, on each patient whose claims fit in one store, over the same
        # absolute window expressed on the patient's clock.
        rows = result()['rows']
        for pid in ('12', '14'):
            (entry,) = store_for(pid)
            view = tgs.from_claims(entry['store'], msi.accept_all(entry['store'], entry['semantic_policy']),
                                   entry['semantic_policy'])
            origin = msi.micros(next(r['clock_origin'] for r in rows if r['patient_id'] == pid))
            query = deepcopy(self.QUERY)
            query['steps'][1]['concept'] = ITEM + '2003'  # the adapter knows items, not the store's groupings
            request = {'profile': tgs.REQUEST_PROFILE, 'query': query, 'unit': 'microsecond',
                       'windows': {pid: {'start': windows[pid]['start'] - origin, 'end': windows[pid]['end'] - origin}}}
            matched = tgs.match_snapshot(view, request)
            self.assertFalse(matched['clinical_mapping_verified'])
            adapter = matched['results'][0]
            mine = next(r for r in es.match(STATE['store'], query, windows)['results'] if r['patient_id'] == pid)
            self.assertEqual(adapter['status'], mine['status'], pid)
            self.assertEqual(adapter.get('binding'), mine.get('binding'), pid)
            self.assertEqual(adapter.get('relaxations'), mine.get('relaxations'), pid)

    def test_seeded_queries_agree_with_exhaustive_matching_and_the_adapter_rows(self):
        engine_input = verify.adapter_engine_input(result()['rows'], result()['prepared']['items'])
        check = verify.differential(STATE['store'], result()['ingest'], engine_input, seeds=60)
        self.assertEqual(check['mismatches'], 0, check['mismatch_examples'])
        self.assertEqual(check['adapter_row_mismatches'], 0, check['mismatch_examples'])
        self.assertEqual(check['dropped_matches'], 0)
        self.assertEqual(check['patient_results'], 240)
        self.assertGreater(check['status_mix'].get('exact', 0), 0)


class PerBucketDeriveTests(unittest.TestCase):
    def test_the_snapshot_holds_the_digests_recorded_before_the_per_bucket_derive_at_any_part_size(self):
        """D23: the per-bucket derive gives the fixture's tables, excluded records included, exactly
        as main's single-statement derive did (`patterns/fixtures/execution_store_content_digests.json`),
        at any part size. D24 changed the events (their variables and `proper`, and the adapter profile in
        their evidence), the patients (`shared_variables`) and so the input digest; every other table is
        the one recorded."""
        want = json.loads((ei.ROOT / 'patterns/fixtures/execution_store_content_digests.json').read_text())['cases']['mimic']
        tables = lambda m: {n: {k: t[k] for k in ('rows', 'content_digest', 'buckets')} for n, t in m['tables'].items()
                            if n not in ('patients', 'events')}
        self.assertGreater(want['tables']['excluded_records']['rows'], 0)
        manifests = [STATE['store'].manifest]
        with tempfile.TemporaryDirectory() as tmp:
            manifests.append(json.loads((es.build(result()['ingest'], tmp, part_rows=1) / 'manifest.json').read_text()))
        for manifest in manifests:
            self.assertEqual(tables(manifest), {n: t for n, t in want['tables'].items() if n not in ('patients', 'events')})
            self.assertEqual(manifest['tables']['events']['rows'], want['tables']['events']['rows'])
        self.assertEqual(manifests[0]['input_digest'], manifests[1]['input_digest'])
        self.assertEqual(manifests[0]['tables'], manifests[1]['tables'])


class CommandLineTests(unittest.TestCase):
    def test_the_fixture_builds_into_the_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(msi.main(['--output', tmp]), 0)
            summary = json.loads((Path(tmp) / 'summary.json').read_text())
            self.assertEqual(json.loads(printed.getvalue())['snapshot_id'], summary['snapshot_id'])
            self.assertEqual(summary['snapshot_id'], STATE['store'].snapshot_id)
            self.assertTrue((Path(tmp) / 'store' / summary['snapshot_id'] / 'manifest.json').is_file())
            self.assertIs(summary['labels']['clinical_mapping_verified'], False)

    def test_supplied_files_need_a_dataset_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('inputevents', 'icustays', 'd_items'):
                shutil.copy(FIXTURE / (name + '.csv'), Path(tmp) / (name + '.csv'))
            with redirect_stdout(io.StringIO()), mock.patch('sys.stderr', io.StringIO()) as err:
                self.assertEqual(msi.main(['--input-dir', tmp, '--output', str(Path(tmp) / 'out')]), 2)
            self.assertIn('DATASET_ID_REQUIRED', err.getvalue())


class CommittedReportTests(unittest.TestCase):
    def test_the_demo_report_is_aggregate_and_its_differential_is_clean(self):
        report = json.loads((ei.ROOT / 'verification/execution-store-mimic-demo-report.json').read_text())
        self.assertFalse(report['patient_rows_included'])
        self.assertFalse(report['clinical_mapping_verified'])
        self.assertEqual(report['concept_basis'], 'source_item_class')
        self.assertEqual(report['snapshot']['labels']['concept_basis'], 'source_item_class')
        self.assertEqual(report['differential']['mismatches'], 0)
        self.assertEqual(report['differential']['adapter_row_mismatches'], 0)
        self.assertEqual(report['differential']['dropped_matches'], 0)
        self.assertEqual(report['route']['choice'], 'a')
        self.assertEqual(report['raw_source_time_check'], {'events_checked': report['counts']['events'], 'mismatches': 0})
        self.assertEqual(report['counts']['excluded']['rows'], report['counts']['admission_outcomes']['UNSUPPORTED_ROW']
                         + report['counts']['admission_outcomes']['INVALID_ROW'])
        self.assertEqual(report['snapshot']['labels']['source'], 'inputevents:continuous_segments')
        self.assertIn('ADMISSION_EXCLUDED_RECORDS', report['differential']['not_comparable_by_reason'])
        # No event, claim or record identifiers, item IRIs or patient keys: counts only.
        text = json.dumps(report)
        self.assertIsNone(re.search(r'(input|row|claim_input)_[0-9a-f]{64}_[0-9]+', text))
        for marker in (ITEM, GROUP, '"patient_id"', '"event_id"'):
            self.assertNotIn(marker, text)
        self.assertEqual(report['counts']['events'], report['snapshot']['tables']['events'])


if __name__ == '__main__':
    unittest.main()
