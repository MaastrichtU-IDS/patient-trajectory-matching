"""The SULO adapter for the trajectory-graph engine: routes, evidence, concepts, time and clocks."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rdflib import RDF, URIRef

from . import bounded_intervals as bt
from . import bounded_rdf as br
from . import claim_projection as cp
from . import claim_rdf as cr
from . import exact_intervals as ei
from . import trajectory_graph as tg
from . import trajectory_graph_sulo as tgs

EXAMPLE = ei.ROOT / 'examples/trajectory-graph'
SULO_EXAMPLE = ei.ROOT / 'examples/trajectory-graph-sulo'
NS = 'https://example.org/trajectory/trajectory-graph-example/'
ORIGIN = '2026-01-01T00:00:00Z'
DAY = tgs.UNITS['day']
INFUSION, SPECIMEN, PROCESS = str(bt.BT.Infusion), str(bt.BT.SpecimenCollection), str(ei.S.Process)


def load(path):
    return json.loads(Path(path).read_text())


def convert(query, patients, unit='day'):
    """Re-express trajectory-graph engine input as a bounded-interval source.

    Times become microseconds on one offset-datetime clock. An exposure keeps its end
    range; a point event becomes the shortest proper interval the bounded profile admits,
    ending one microsecond after it starts. Each listed concept becomes a declared class
    fact on the event; the event kind is only a carrier (exposures `infusion`, points
    `specimen_collection`).
    """
    scale = tgs.UNITS[unit]
    variables, events, facts, concepts = [], [], [], set(tgs._query_concepts(query))
    for p in patients:
        pid = p['patient_id']
        for e in p['events']:
            eid, start = e['id'], e['start']
            if 'end' in e:
                assert e['end'][1] is not None, 'the bounded profile has no ongoing exposure'
                end, kind = [e['end'][0] * scale, e['end'][1] * scale], 'infusion'
            else:
                end, kind = [start[0] * scale + 1, start[1] * scale + 1], 'specimen_collection'
            key = 'examples/trajectory-graph/patients.json#' + eid
            for side, (lo, hi) in (('start', (start[0] * scale, start[1] * scale)), ('end', end)):
                variables.append({'id': eid + '-' + side, 'patient_id': pid, 'episode_id': 'E1', 'clock_id': 'clinical',
                                  'lower_us': lo, 'upper_us': hi, 'source_key': key + '/' + side})
            events.append({'id': eid, 'record_id': 'record-' + eid, 'patient_id': pid, 'episode_id': 'E1',
                           'event_kind': kind, 'status': 'performed', 'start_var': eid + '-start',
                           'end_var': eid + '-end', 'source_key': key})
            for c in e['concepts']:
                concepts.add(c)
                facts.append({'id': eid + '-' + c, 'patient_id': pid, 'episode_id': 'E1', 'kind': 'class',
                              'subject': {'event_id': eid}, 'class_iri': NS + c})
    source = {'profile': bt.PROFILE_ID, 'dataset_id': 'trajectory-graph-example', 'snapshot_id': 'trajectory-graph-example-1',
              'clocks': [{'clock_id': 'clinical', 'origin': ORIGIN, 'scope': 'global', 'policy': tgs.OFFSET_POLICY}],
              'variables': variables, 'events': events, 'constraints': []}
    vocabulary = {'profile': 'joint-semantic-policy-1.0', 'id': 'trajectory-graph-example-concepts',
                  'classes': sorted(NS + c for c in concepts), 'rules': [], 'disjoint': []}
    q = deepcopy(query)
    for s in q['steps']:
        s['concept'] = NS + s['concept']
    for x in q.get('relaxations', []):
        if x['kind'] == 'widen_concept':
            x['concept'] = NS + x['concept']
    request = {'profile': tgs.REQUEST_PROFILE, 'query': q, 'unit': unit,
               'windows': {p['patient_id']: p['window'] for p in patients}}
    return source, {'profile': tgs.FACTS_PROFILE, 'vocabulary': vocabulary, 'facts': facts}, request


def decision_fields(result):
    """What the engine decides, without the witness and method, which depend on event shape."""
    out = {k: v for k, v in result.items() if k in ('patient_id', 'status', 'binding', 'relaxations', 'cost',
                                                    'certainty', 'complete', 'note', 'reason')}
    if 'relaxed_match' in result:
        out['relaxed_match'] = {k: v for k, v in result['relaxed_match'].items() if k != 'method'}
    return out


ROUTE_FIELDS = ('context', 'context_id')
EVIDENCE_ROUTE_FIELDS = ('evidence_id', 'context_id', 'provenance')


def route_invariant(result):
    """The adapter output without its route identity: contexts, evidence identifiers and
    the RDF reader's provenance descriptors, which name the route by design."""
    out = {k: deepcopy(v) for k, v in result.items() if k not in ROUTE_FIELDS}
    for row in out['results']:
        for key in ('bound_evidence', 'relaxed_bound_evidence'):
            for item in row.get(key, {}).values():
                for f in EVIDENCE_ROUTE_FIELDS:
                    item['evidence'].pop(f, None)
    for row in out['events']:
        for f in ('snapshot_context_id', 'evidence_id', 'evidence_context_id'):
            row.pop(f)
    return out


def bound_evidence(result):
    for row in result['results']:
        for key in ('bound_evidence', 'relaxed_bound_evidence'):
            yield from row.get(key, {}).values()


def source_for(events, constraints=(), clocks=None):
    """A small bounded source: events as (id, patient, kind, start bounds, end bounds[, shared start var])."""
    variables, rows = {}, []
    for eid, pid, kind, start, end, *shared in events:
        svar = shared[0] if shared else eid + '-start'
        clock = 'clinical' if len(shared) < 2 else shared[1]
        for vid, (lo, hi) in ((svar, start), (eid + '-end', end)):
            variables.setdefault(vid, {'id': vid, 'patient_id': pid, 'episode_id': 'E1', 'clock_id': clock,
                                       'lower_us': lo, 'upper_us': hi, 'source_key': vid})
        rows.append({'id': eid, 'record_id': 'record-' + eid, 'patient_id': pid, 'episode_id': 'E1', 'event_kind': kind,
                     'status': 'performed', 'start_var': svar, 'end_var': eid + '-end', 'source_key': eid})
    return {'profile': bt.PROFILE_ID, 'dataset_id': 'constructed', 'snapshot_id': 'constructed-1',
            'clocks': clocks or [{'clock_id': 'clinical', 'origin': ORIGIN, 'scope': 'global', 'policy': tgs.OFFSET_POLICY}],
            'variables': list(variables.values()), 'events': rows, 'constraints': list(constraints)}


def request_for(steps, relations=(), unit='day', window=(0, 30), **extra):
    query = {'profile': tg.PROFILE_ID, 'steps': [{'id': i, 'concept': c} for i, c in steps], 'relations': list(relations)}
    query.update(extra)
    return {'profile': tgs.REQUEST_PROFILE, 'query': query, 'unit': unit,
            'window': {'start': window[0], 'end': window[1]}}


class ExampleParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.query, cls.patients = load(EXAMPLE / 'query.json'), load(EXAMPLE / 'patients.json')
        cls.source, cls.facts, cls.request = convert(cls.query, cls.patients)
        cls.engine = tg.run(cls.query, cls.patients)['results']
        cls.json_view = tgs.from_bounded_json(cls.source, cls.facts)
        cls.rdf_view = tgs.from_bounded_rdf(br.export_source(cls.source), cls.facts)
        cls.json = tgs.match_snapshot(cls.json_view, cls.request)
        cls.rdf = tgs.match_snapshot(cls.rdf_view, cls.request)

    def test_committed_example_is_the_reexpressed_engine_example(self):
        self.assertEqual(load(SULO_EXAMPLE / 'source.json'), self.source)
        self.assertEqual(load(SULO_EXAMPLE / 'facts.json'), self.facts)
        self.assertEqual(load(SULO_EXAMPLE / 'request.json'), self.request)

    def test_json_route_matches_the_engine_on_the_original_example(self):
        self.assertEqual([decision_fields(r) for r in self.json['results']], [decision_fields(r) for r in self.engine])
        self.assertEqual(self.json['statuses'], {'P1': 'exact', 'P2': 'possible', 'P3': 'relaxed', 'P4': 'none', 'P5': 'excluded'})
        self.assertTrue(all(r['decided_by'] == 'engine' for r in self.json['results']))

    def test_rdf_route_matches_the_engine_on_the_original_example(self):
        self.assertEqual([decision_fields(r) for r in self.rdf['results']], [decision_fields(r) for r in self.engine])

    def test_json_and_rdf_routes_give_identical_adapter_output(self):
        self.assertNotEqual(self.json['context']['route'], self.rdf['context']['route'])
        self.assertEqual(route_invariant(self.json), route_invariant(self.rdf))

    def test_canonical_graph_route_equals_the_rdf_route(self):
        canonical = tgs.match_snapshot(tgs.from_bounded_source(self.source, self.facts), self.request)
        self.assertEqual(canonical, self.rdf)

    def test_possible_witness_is_checked_against_the_source_network(self):
        p2 = next(r for r in self.json['results'] if r['patient_id'] == 'P2')
        check = p2['source_check']
        self.assertTrue(check['feasible'])
        self.assertFalse(check['entailed'])
        self.assertTrue(check['engine_witness_confirmed'])
        # The witness lies inside the original example's ranges (in days, on the clock).
        self.assertTrue(140 * DAY <= p2['witness']['A'] <= 170 * DAY)
        self.assertTrue(300 * DAY <= p2['witness']['C'] <= 330 * DAY)

    def test_every_bound_step_carries_its_graph_evidence(self):
        for view, result in ((self.json_view, self.json), (self.rdf_view, self.rdf)):
            snapshot = view.snapshot
            items = list(bound_evidence(result))
            self.assertEqual(len(items), 9)  # P1 3, P2 3, P3 3
            for item in items:
                evidence = dict(item['evidence'])
                claimed = evidence.pop('evidence_id')
                self.assertEqual(claimed, snapshot.context['profile'] + ':' + ei.digest(ei.canonical(evidence)))
                self.assertEqual(claimed, snapshot.evidence[item['event_id']]['evidence_id'])
                self.assertEqual(evidence['context_id'], snapshot.context_id)
                self.assertIn((URIRef(evidence['claim']), RDF.type, bt.BT.IntervalClaim), snapshot.graph)
                self.assertIn((URIRef(evidence['patient_role']), ei.S.isFeatureOf, URIRef(evidence['patient_bearer'])), snapshot.graph)
                self.assertIn((URIRef(evidence['source_record']), ei.S.refersTo, URIRef(evidence['claim'])), snapshot.graph)

    def test_event_rows_are_the_store_ingest_rows(self):
        rows = {r['event_id']: r for r in self.json['events']}
        self.assertEqual(set(rows), set(self.json_view.snapshot.events))
        fs = rows['P2-fs']
        self.assertEqual(fs['start_us'], [140 * DAY, 170 * DAY])
        self.assertEqual(fs['end_us'], [140 * DAY + 1, 170 * DAY + 1])
        self.assertEqual(fs['comparability'], 'comparable')
        self.assertEqual(fs['selection_state'], 'declared_source')
        bases = {c['concept']: c['basis'] for c in fs['concepts']}
        self.assertEqual(bases[NS + 'febrile_seizure'][0]['kind'], 'semantic_fact')
        self.assertEqual(bases[SPECIMEN][0]['kind'], 'event_kind_closure')
        self.assertTrue(all(c['basis'] for r in rows.values() for c in r['concepts']))

    def test_result_context_binds_the_snapshot_query_and_artifacts(self):
        context = self.json['context']
        self.assertEqual(context['snapshot_context_id'], self.json_view.snapshot.context_id)
        self.assertEqual(context['query_sha256'], ei.digest(ei.canonical(self.request['query'])))
        self.assertEqual((context['unit'], context['unit_us'], context['engine_grid']), ('day', DAY, 'microsecond'))
        self.assertIn('patterns/trajectory_graph_sulo.py', context['artifacts'])
        self.assertEqual(self.json['context_id'], ei.digest(ei.canonical(context)))
        self.assertIsNotNone(self.rdf['context']['graph_sha256'])

    def test_rustdl_entailed_concepts_reproduce_the_explicit_ancestors(self):
        facts = deepcopy(self.facts)
        febrile = {f['subject']['event_id'] for f in facts['facts'] if f['class_iri'] == NS + 'febrile_seizure'}
        facts['facts'] = [f for f in facts['facts']
                          if not (f['class_iri'] == NS + 'seizure' and f['subject']['event_id'] in febrile)]
        facts['vocabulary']['rules'] = [{'id': 'febrile-seizure-is-seizure',
                                         'if': {'class': NS + 'febrile_seizure'}, 'then': NS + 'seizure'}]
        view = tgs.from_bounded_json(self.source, facts)
        with self.assertRaisesRegex(tgs.AdapterError, 'ENTAILMENT_REQUIRED'):
            tgs.match_snapshot(view, self.request)
        result = tgs.match_snapshot(view, dict(self.request, semantic_policy=facts['vocabulary']))
        self.assertEqual([decision_fields(r) for r in result['results']], [decision_fields(r) for r in self.engine])
        self.assertEqual(result['semantic']['status'], 'READY')
        bases = {c['concept']: c['basis'] for r in result['events'] if r['event_id'] == 'P1-fs' for c in r['concepts']}
        self.assertEqual([b['kind'] for b in bases[NS + 'seizure']], ['rustdl_entailed'])
        self.assertTrue(bases[NS + 'seizure'][0]['asserted_supports'])


class ConceptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = source_for([('X', 'P1', 'infusion', (0, 0), (DAY, DAY)),
                                 ('Y', 'P1', 'specimen_collection', (2 * DAY, 2 * DAY), (3 * DAY, 3 * DAY))])
        cls.view = tgs.from_bounded_json(cls.source)

    def test_kind_ancestors_equal_selected_classes(self):
        vocabulary = tgs.Vocabulary()
        for kind, cls in bt.KINDS.items():
            self.assertEqual(vocabulary.ancestors(str(cls)), bt.selected_classes({'event_kind': kind}))

    def test_selected_classes_is_called_at_most_once_per_kind(self):
        # Ancestors come from one parse of the ontology, not one call per event.
        with patch.object(bt, 'selected_classes', side_effect=AssertionError('reparse')):
            result = tgs.match_snapshot(self.view, request_for([('a', PROCESS)]))
        self.assertEqual(result['statuses'], {'P1': 'exact'})

    def test_named_ancestors_close_upward(self):
        r = tgs.match_snapshot(self.view, request_for([('a', str(bt.BT.IntervalProcess)), ('b', SPECIMEN)],
                                                      [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 1}]))
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        self.assertEqual(r['results'][0]['binding'], {'a': 'X', 'b': 'Y'})
        x = next(row for row in r['events'] if row['event_id'] == 'X')
        self.assertEqual({c['concept'] for c in x['concepts']}, bt.selected_classes({'event_kind': 'infusion'}))

    def test_unknown_step_concept_is_refused(self):
        with self.assertRaisesRegex(tgs.AdapterError, 'UNKNOWN_CONCEPT:https://example.org/not-a-class'):
            tgs.match_snapshot(self.view, request_for([('a', 'https://example.org/not-a-class')]))

    def test_unknown_relaxation_concept_is_refused(self):
        request = request_for([('a', INFUSION)], relaxations=[
            {'id': 'w', 'kind': 'widen_concept', 'cost': 1, 'step': 'a', 'concept': 'https://example.org/nowhere'}], budget=1)
        with self.assertRaisesRegex(tgs.AdapterError, 'UNKNOWN_CONCEPT:https://example.org/nowhere'):
            tgs.match_snapshot(self.view, request)

    def test_fact_class_must_be_declared(self):
        facts = {'profile': tgs.FACTS_PROFILE, 'facts': [
            {'id': 'f', 'patient_id': 'P1', 'episode_id': 'E1', 'kind': 'class', 'subject': {'event_id': 'X'},
             'class_iri': 'https://example.org/undeclared'}],
            'vocabulary': {'profile': 'joint-semantic-policy-1.0', 'id': 'v', 'classes': [], 'rules': [], 'disjoint': []}}
        with self.assertRaisesRegex(tgs.AdapterError, 'UNDECLARED_FACT_CLASS'):
            tgs.match_snapshot(tgs.from_bounded_json(self.source, facts), request_for([('a', INFUSION)]))

    def test_fact_on_an_ontology_class_brings_its_ancestors_with_basis(self):
        facts = {'profile': tgs.FACTS_PROFILE, 'facts': [
            {'id': 'f', 'patient_id': 'P1', 'episode_id': 'E1', 'kind': 'class', 'subject': {'event_id': 'Y'},
             'class_iri': INFUSION}],
            'vocabulary': {'profile': 'joint-semantic-policy-1.0', 'id': 'v', 'classes': [], 'rules': [], 'disjoint': []}}
        r = tgs.match_snapshot(tgs.from_bounded_json(self.source, facts), request_for([('a', INFUSION)]))
        y = {c['concept']: [b['kind'] for b in c['basis']] for row in r['events'] if row['event_id'] == 'Y' for c in row['concepts']}
        self.assertEqual(y[INFUSION], ['semantic_fact'])
        self.assertEqual(y[PROCESS], ['event_kind_closure', 'semantic_fact_closure'])


class TimeAndClockTests(unittest.TestCase):
    def test_source_constraint_a_box_cannot_hold_is_refused_per_patient(self):
        source = source_for([('A1', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY)),
                             ('B1', 'P1', 'specimen_collection', (0, 10 * DAY), (11 * DAY, 11 * DAY)),
                             ('A2', 'P2', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY)),
                             ('B2', 'P2', 'specimen_collection', (0, 10 * DAY), (11 * DAY, 11 * DAY))],
                            [{'id': 'b-after-a', 'left_var': 'A1-start', 'right_var': 'B1-start',
                              'upper_us': -5 * DAY, 'source_key': 'declared'}])
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 1}])
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)
        p1, p2 = r['results']
        self.assertEqual((p1['status'], p1['decided_by'], p1['adapter']['code']),
                         ('refused', 'adapter', 'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE'))
        self.assertEqual(p1['adapter']['constraints'][0]['edge'], 'source:b-after-a')
        self.assertEqual(p2['status'], 'possible')
        self.assertEqual({row['comparability'] for row in r['events'] if row['patient_id'] == 'P1'}, {'refused'})

    def test_source_constraint_the_box_implies_is_accepted(self):
        source = source_for([('A', 'P1', 'infusion', (0, DAY), (2 * DAY, 2 * DAY)),
                             ('B', 'P1', 'specimen_collection', (5 * DAY, 6 * DAY), (7 * DAY, 7 * DAY))],
                            [{'id': 'redundant', 'left_var': 'A-start', 'right_var': 'B-start',
                              'upper_us': -DAY, 'source_key': 'declared'}])
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 4, 'max': 6}])
        self.assertEqual(tgs.match_snapshot(tgs.from_bounded_json(source), request)['statuses'], {'P1': 'exact'})

    def test_variables_are_tightened_to_their_minimal_bounds(self):
        # The proper-interval edge alone tightens: end >= start + 1 lifts the end's floor.
        source = source_for([('A', 'P1', 'infusion', (5 * DAY, 9 * DAY), (0, 20 * DAY))])
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request_for([('a', INFUSION)]))
        row = r['events'][0]
        self.assertEqual(row['declared_end_us'], [0, 20 * DAY])
        self.assertEqual(row['end_us'], [5 * DAY + 1, 20 * DAY])

    def test_offset_clocks_are_rebased_by_their_exact_difference(self):
        clocks = [{'clock_id': 'c1', 'origin': ORIGIN, 'scope': 'global', 'policy': tgs.OFFSET_POLICY},
                  {'clock_id': 'c2', 'origin': '2026-01-02T00:00:00+01:00', 'scope': 'global', 'policy': tgs.OFFSET_POLICY}]
        source = source_for([('X', 'P1', 'infusion', (0, 0), (1, 1), 'X-start', 'c1'),
                             ('Y', 'P1', 'specimen_collection', (0, 0), (1, 1), 'Y-start', 'c2')], clocks=clocks)
        request = request_for([('a', INFUSION), ('b', SPECIMEN)],
                              [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 23, 'max': 23}],
                              unit='hour', window=(0, 48))
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        self.assertEqual(r['results'][0]['clock']['rebase_offsets_us'], {'c1': 0, 'c2': 23 * 3_600_000_000})
        y = next(row for row in r['events'] if row['event_id'] == 'Y')
        self.assertEqual((y['start_us'], y['rebase_offset_us']), ([23 * 3_600_000_000] * 2, 23 * 3_600_000_000))

    def test_window_clock_chooses_the_reference_clock(self):
        clocks = [{'clock_id': 'c1', 'origin': ORIGIN, 'scope': 'global', 'policy': tgs.OFFSET_POLICY},
                  {'clock_id': 'c2', 'origin': '2026-01-02T00:00:00Z', 'scope': 'global', 'policy': tgs.OFFSET_POLICY}]
        source = source_for([('X', 'P1', 'infusion', (0, 0), (1, 1), 'X-start', 'c1')], clocks=clocks)
        request = request_for([('a', INFUSION)], unit='day', window=(-1, -1), scope='within')
        request['window']['clock_id'] = 'c2'
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        self.assertEqual(r['results'][0]['clock']['rebase_offsets_us'], {'c1': -DAY, 'c2': 0})

    def test_patient_local_clocks_that_cannot_be_rebased_make_the_patient_incomparable(self):
        view = tgs.from_patient_local(load(ei.ROOT / 'examples/patient-local/source.json'))
        r = tgs.match_snapshot(view, request_for([('a', PROCESS)], window=(-3650, 3650)))
        p4 = next(x for x in r['results'] if x['patient_id'] == 'P4')
        self.assertEqual((p4['status'], p4['adapter']['code']), ('incomparable', 'CLOCKS_NOT_REBASABLE'))
        self.assertEqual(p4['adapter']['clocks'], ['P4_other'])
        excluded = {row['event_id'] for row in r['events'] if row['comparability'] == 'incomparable_clock'}
        self.assertEqual(excluded, set(p4['adapter']['excluded_events']))
        self.assertTrue(excluded)
        # P1 relates its endpoints to a shared uncertain anchor: no box holds that.
        statuses = {x['patient_id']: x['status'] for x in r['results']}
        self.assertEqual(statuses, {'P1': 'refused', 'P2': 'exact', 'P3': 'exact', 'P4': 'incomparable'})
        self.assertEqual(r['context']['time_domain'], 'bounded-patient-local-calendar-microseconds')

    def test_a_windowed_patient_absent_from_the_snapshot_is_incomparable_not_none(self):
        source = source_for([('X', 'P1', 'infusion', (0, 0), (1, 1))])
        request = request_for([('a', INFUSION)])
        request['windows'] = {'P9': {'start': 0, 'end': 1}}
        statuses = tgs.match_snapshot(tgs.from_bounded_json(source), request)['statuses']
        self.assertEqual(statuses, {'P1': 'exact', 'P9': 'incomparable'})

    def test_units_convert_exactly_and_calendar_units_are_refused(self):
        source, facts, request = convert(load(EXAMPLE / 'query.json'), load(EXAMPLE / 'patients.json'))
        query = tgs.engine_query(request['query'], DAY)
        self.assertEqual(query['relations'][0]['max'], 365 * DAY)
        self.assertEqual(query['relaxations'][1]['grace'], 60 * DAY)
        view = tgs.from_bounded_json(source, facts)
        for unit, code in (('month', 'CALENDAR_UNIT_REFUSED'), ('years', 'CALENDAR_UNIT_REFUSED'),
                           ('fortnight', 'UNSUPPORTED_UNIT')):
            with self.assertRaisesRegex(tgs.AdapterError, code):
                tgs.match_snapshot(view, dict(request, unit=unit))
        with self.assertRaisesRegex(tgs.AdapterError, 'INDEX_WINDOW_REQUIRED'):
            tgs.match_snapshot(view, {k: v for k, v in request.items() if k != 'windows'})
        # The same example in hours gives the same decisions.
        hours = deepcopy(request)
        hours['unit'] = 'hour'
        for r in hours['query']['relations']:
            for k in ('min', 'max', 'grace'):
                if k in r:
                    r[k] *= 24
        hours['query']['relaxations'][1]['grace'] *= 24
        hours['windows'] = {p: w and {'start': w['start'] * 24, 'end': w['end'] * 24} for p, w in request['windows'].items()}
        self.assertEqual(tgs.match_snapshot(view, hours)['statuses'], tgs.match_snapshot(view, request)['statuses'])


class WitnessCheckTests(unittest.TestCase):
    def test_one_event_bound_to_two_steps_is_not_reported_possible(self):
        # The engine gives each step its own time variables, so the box admits A and B at
        # different times of the same event. The event has one start.
        source = source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY))])
        request = request_for([('a', INFUSION), ('b', INFUSION)], [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 5}])
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)['results'][0]
        self.assertEqual(r['engine_result']['status'], 'possible')
        self.assertEqual((r['status'], r['decided_by'], r['reason']),
                         ('unresolved', 'adapter', 'POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK'))
        self.assertFalse(r['source_check']['feasible'])
        self.assertTrue(any(e.startswith('query:') for e in r['source_check']['refutation']))

    def test_events_sharing_a_variable_are_not_reported_possible(self):
        source = source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY), 'shared-start'),
                             ('Y', 'P1', 'specimen_collection', (0, 10 * DAY), (12 * DAY, 12 * DAY), 'shared-start')])
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 5}])
        engine_input_alone = tg.match(tgs.engine_query(request['query'], DAY), {
            'patient_id': 'P1', 'window': {'start': 0, 'end': 30 * DAY}, 'events': [
                {'id': 'X', 'concepts': [INFUSION], 'start': [0, 10 * DAY], 'end': [11 * DAY, 11 * DAY]},
                {'id': 'Y', 'concepts': [SPECIMEN], 'start': [0, 10 * DAY], 'end': [12 * DAY, 12 * DAY]}]})
        self.assertEqual(engine_input_alone['status'], 'possible')  # box projection alone
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)['results'][0]
        self.assertEqual((r['status'], r['adapter']['code']), ('unresolved', 'POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK'))

    def test_shared_variable_that_still_admits_the_binding_stays_possible(self):
        source = source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY), 'shared-start'),
                             ('Y', 'P1', 'specimen_collection', (0, 10 * DAY), (12 * DAY, 12 * DAY), 'shared-start')])
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 0, 'max': 0}])
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)['results'][0]
        self.assertEqual(r['status'], 'possible')
        self.assertTrue(r['source_check']['feasible'])

    def _patched(self, fake):
        source, facts, request = convert(load(EXAMPLE / 'query.json'), load(EXAMPLE / 'patients.json'))
        real = tg.match

        def match(query, patient):
            return fake(real(query, patient)) if patient['patient_id'] == 'P2' and patient['window'] else real(query, patient)
        with patch.object(tgs.tg, 'match', side_effect=match):
            result = tgs.match_snapshot(tgs.from_bounded_json(source, facts), request)
        return next(r for r in result['results'] if r['patient_id'] == 'P2')

    def test_an_exact_claim_the_network_does_not_entail_is_downgraded(self):
        r = self._patched(lambda res: {'patient_id': 'P2', 'status': 'exact', 'binding': res['binding'],
                                       'method': 'forest_arc_consistency', 'complete': True})
        self.assertEqual((r['status'], r['adapter']['code'], r['complete']),
                         ('possible', 'EXACT_NOT_ENTAILED_BY_SOURCE_NETWORK', False))

    def test_an_engine_witness_outside_the_network_is_replaced(self):
        r = self._patched(lambda res: dict(res, witness={s: -DAY for s in res['witness']}))
        self.assertEqual((r['status'], r['adapter']['code']), ('possible', 'ENGINE_WITNESS_REPLACED_BY_SOURCE_WITNESS'))
        self.assertEqual(r['witness'], r['source_check']['witness'])
        self.assertEqual(r['engine_witness'], {'A': -DAY, 'B': -DAY, 'C': -DAY})


def claim_fixture():
    base = ei.ROOT / 'examples/claim-projection'
    return [load(base / (name + '.json')) for name in ('store', 'policy', 'semantic-policy', 'query')]


def rebind(store, policy, semantic):
    policy['store_sha256'] = cr.digest(store)
    policy['semantic_policy_sha256'] = cr.digest(semantic)
    claims = {c['id']: c for c in store['claims']}
    for d in policy['decisions']:
        d['claim_sha256'] = cr.digest(claims[d['claim_id']])


ANTIBIOTIC = 'https://example.org/trajectory/semantic/AntibioticAdministration'


class ClaimRouteTests(unittest.TestCase):
    def setUp(self):
        self.store, self.policy, self.semantic, self.query = claim_fixture()
        self.request = request_for([('a', ANTIBIOTIC), ('b', SPECIMEN)],
                                   [{'id': 'order', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 1}],
                                   unit='microsecond', window=(0, 10))

    def test_source_equals_what_the_projection_produces(self):
        view = tgs.from_claims(self.store, self.policy, self.semantic)
        projected = cp.execute(self.store, self.policy, self.semantic, self.query)
        self.assertEqual(projected['status'], 'READY')
        strip = lambda s: {k: v for k, v in s.items() if k != 'snapshot_id'}
        self.assertEqual(strip(view.provenance['selection_source']), strip(projected['source']))
        ordered = lambda s: {k: sorted(v, key=ei.canonical) if isinstance(v, list) else v for k, v in strip(s).items()}
        self.assertEqual(ordered(view.snapshot.source), ordered(projected['source']))
        self.assertEqual(view.route, 'claim-selection-rdf')

    def test_claim_graph_and_claim_json_give_the_same_snapshot(self):
        graph = cr.encode(self.store, fields=cr.FIELDS)
        a = tgs.from_claims(graph, self.policy, self.semantic)
        b = tgs.from_claims(self.store, self.policy, self.semantic)
        self.assertEqual(a.snapshot.context_id, b.snapshot.context_id)
        self.assertEqual(a.event_supports, b.event_supports)

    def test_accepted_claims_match_through_rustdl_entailment_with_supports(self):
        view = tgs.from_claims(self.store, self.policy, self.semantic)
        with self.assertRaisesRegex(tgs.AdapterError, 'ENTAILMENT_REQUIRED'):
            tgs.match_snapshot(view, self.request)
        r = tgs.match_snapshot(view, dict(self.request, semantic_policy=self.semantic))
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        a = r['results'][0]['bound_evidence']['a']
        self.assertEqual(a['claim_supports'][0]['assertion_id'], 'admin_classified')
        self.assertEqual(a['claim_supports'][0]['support_ids'], ['accept_admin_classified'])
        row = next(x for x in r['events'] if x['event_id'] == 'A')
        bases = {c['concept']: c['basis'] for c in row['concepts']}
        self.assertEqual(bases[ANTIBIOTIC][0]['kind'], 'rustdl_entailed')
        self.assertEqual(row['selection_state'], 'accepted')
        self.assertEqual(r['context']['route_provenance']['acceptance_policy_id'], self.policy['id'])
        for item in bound_evidence(r):
            evidence = dict(item['evidence'])
            claimed = evidence.pop('evidence_id')
            self.assertEqual(claimed, view.snapshot.context['profile'] + ':' + ei.digest(ei.canonical(evidence)))
            self.assertIn((URIRef(evidence['claim']), RDF.type, bt.BT.IntervalClaim), view.snapshot.graph)

    def test_pending_claims_disappear(self):
        self.policy['decisions'] = [d for d in self.policy['decisions'] if d['claim_id'] != 'admin_classified']
        rebind(self.store, self.policy, self.semantic)
        view = tgs.from_claims(self.store, self.policy, self.semantic)
        self.assertEqual(set(view.snapshot.events), {'B'})
        states = {c['claim_id']: c['state'] for c in view.provenance['claim_states']}
        self.assertEqual(states['admin_classified'], 'PENDING')
        r = tgs.match_snapshot(view, dict(self.request, semantic_policy=self.semantic))
        self.assertEqual(r['statuses'], {'P1': 'none'})

    def test_withdrawn_claims_disappear(self):
        self.policy['decisions'].append({'id': 'withdraw_admin', 'claim_id': 'admin_classified', 'claim_sha256': '',
                                         'action': 'withdraw', 'supersedes': 'accept_admin_classified',
                                         'reason': 'Synthetic withdrawal.'})
        rebind(self.store, self.policy, self.semantic)
        view = tgs.from_claims(self.store, self.policy, self.semantic)
        self.assertEqual(set(view.snapshot.events), {'B'})
        self.assertEqual(view.provenance['accepted_claim_ids'], ['collection_original'])
        self.assertFalse(view.semantic_facts)

    def test_no_accepted_claims_is_refused_not_matched(self):
        self.policy['decisions'] = []
        rebind(self.store, self.policy, self.semantic)
        with self.assertRaisesRegex(tgs.AdapterError, 'EMPTY_ACCEPTED_VIEW'):
            tgs.from_claims(self.store, self.policy, self.semantic)


class MimicFixtureTests(unittest.TestCase):
    def test_mimic_inputevents_through_a_caller_declared_accept_all_policy(self):
        from . import mimic_claim_import as mimic
        run = mimic.run(ei.ROOT / 'examples/mimic-inputevents', load(ei.ROOT / 'examples/mimic-claim-import/synthetic-request.json'))
        episode = next(e for e in run['episodes'] if e['status'] == 'PENDING_CLAIMS')
        store, semantic = episode['store'], run['semantic_policy']
        policy = {'profile': cp.POLICY_PROFILE, 'id': 'caller_declared_accept_all', 'store_sha256': cr.digest(store),
                  'semantic_policy_sha256': cr.digest(semantic),
                  'decisions': [{'id': 'accept_' + c['id'], 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
                                 'action': 'accept', 'supersedes': None,
                                 'reason': 'Caller-declared accept-all analysis policy; no clinical review.'}
                                for c in store['claims']]}
        view = tgs.from_claims(store, policy, semantic)
        self.assertEqual(view.route, 'local-claim-selection-rdf')
        item = mimic.ITEM_NS + '1000'
        request = request_for([('first', item), ('next', item)],
                              [{'id': 'later', 'kind': 'after', 'from': 'first', 'to': 'next', 'min': 1}],
                              unit='minute', window=(0, 7 * 24 * 60))
        r = tgs.match_snapshot(view, request)
        self.assertFalse(r['context']['clinical_mapping_verified'])
        self.assertFalse(r['clinical_mapping_verified'])
        self.assertEqual(r['context']['route_provenance']['acceptance_policy_id'], 'caller_declared_accept_all')
        self.assertEqual(r['context']['time_domain'], 'bounded-patient-local-calendar-microseconds')
        self.assertEqual(r['statuses'], {episode['patient_id']: 'exact'})
        rows = r['events']
        self.assertEqual(len(rows), len(store['claims']))
        for row in rows:
            bases = {c['concept']: c['basis'] for c in row['concepts']}
            self.assertEqual(bases[item][0]['kind'], 'semantic_fact')
            self.assertEqual(bases[item][0]['supports'][0]['support_ids'], ['accept_claim_' + row['event_id']])
            self.assertEqual(row['clock_policy'], 'patient-local-calendar-microseconds-v1')
        for evidence in bound_evidence(r):
            self.assertIn((URIRef(evidence['evidence']['claim']), RDF.type, bt.BT.IntervalClaim), view.snapshot.graph)


class CommandLineTests(unittest.TestCase):
    def test_example_runs_through_the_graph_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'result.json'
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(tgs.main(['--output', str(out)]), 0)
            result = load(out)
        self.assertEqual(result['context']['route'], 'bounded-rdf')
        self.assertEqual(json.loads(printed.getvalue())['statuses'], result['statuses'])
        self.assertEqual(result['statuses'], {'P1': 'exact', 'P2': 'possible', 'P3': 'relaxed', 'P4': 'none', 'P5': 'excluded'})


if __name__ == '__main__':
    unittest.main()
