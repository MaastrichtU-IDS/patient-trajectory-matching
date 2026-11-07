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


EVIDENCE_ROUTE_FIELDS = ('evidence_id', 'context_id', 'provenance')
ROW_ROUTE_FIELDS = ('snapshot_context_id', 'evidence_id', 'evidence_context_id')


def route_invariant(result):
    """The adapter output without its route identity: contexts, evidence identifiers and
    the RDF reader's provenance descriptors, which name the route by design."""
    out = {k: deepcopy(v) for k, v in result.items() if k not in ('context', 'context_id')}
    for row in out['results']:
        for key in ('bound_evidence', 'relaxed_bound_evidence'):
            for item in row.get(key, {}).values():
                for f in EVIDENCE_ROUTE_FIELDS:
                    item['evidence'].pop(f, None)
    return out


def rows_invariant(rows):
    return [{k: v for k, v in r.items() if k not in ROW_ROUTE_FIELDS} for r in rows]


def bound_evidence(result):
    for row in result['results']:
        for key in ('bound_evidence', 'relaxed_bound_evidence'):
            yield from row.get(key, {}).values()


def source_for(events, constraints=(), clocks=None):
    """A small bounded source: events as (id, patient, kind, start bounds, end bounds[, start var[, clock]])."""
    variables, rows = {}, []
    for eid, pid, kind, start, end, *extra in events:
        svar = extra[0] if extra else eid + '-start'
        clock = 'clinical' if len(extra) < 2 else extra[1]
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


def after(lo=None, hi=None, a='a', b='b'):
    r = {'id': a + '-' + b, 'kind': 'after', 'from': a, 'to': b}
    if lo is not None:
        r['min'] = lo
    if hi is not None:
        r['max'] = hi
    return r


def offset_clocks(days_apart):
    return [{'clock_id': 'c1', 'origin': ORIGIN, 'scope': 'global', 'policy': tgs.OFFSET_POLICY},
            {'clock_id': 'c2', 'origin': '2026-01-%02dT00:00:00Z' % (1 + days_apart) if days_apart < 31 else
             '2026-04-11T00:00:00Z', 'scope': 'global', 'policy': tgs.OFFSET_POLICY}]


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
        self.assertEqual(rows_invariant(tgs.ingest_rows(self.json_view)), rows_invariant(tgs.ingest_rows(self.rdf_view)))

    def test_canonical_graph_route_equals_the_rdf_route(self):
        canonical = tgs.match_snapshot(tgs.from_bounded_source(self.source, self.facts), self.request)
        self.assertEqual(canonical, self.rdf)

    def test_route_modules_are_bound_in_the_context(self):
        json_files, rdf_files = self.json['context']['artifacts'], self.rdf['context']['artifacts']
        self.assertNotIn('patterns/bounded_rdf.py', json_files)
        self.assertIn('patterns/bounded_rdf.py', rdf_files)
        self.assertIn('patterns/joint_evidence.py', json_files)  # the facts document's vocabulary
        self.assertIsNone(self.json['context']['semantic_run'])

    def test_possible_witness_is_checked_against_the_source_network(self):
        p2 = next(r for r in self.json['results'] if r['patient_id'] == 'P2')
        check = p2['source_check']
        self.assertTrue(check['feasible'])
        self.assertFalse(check['entailed'])
        self.assertTrue(check['engine_witness_confirmed'])
        self.assertEqual(p2['projection'], 'intervals')  # the query has a `during` relation
        self.assertTrue(140 * DAY <= p2['witness']['A'] <= 170 * DAY)
        self.assertTrue(300 * DAY <= p2['witness']['C'] <= 330 * DAY)

    def test_every_bound_step_carries_its_graph_evidence_and_concept_basis(self):
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
                self.assertTrue(item['concept']['basis'])
        p3 = next(r for r in self.json['results'] if r['patient_id'] == 'P3')
        self.assertEqual(p3['bound_evidence']['A']['concept']['concept'], NS + 'seizure')  # the widened concept

    def test_result_context_binds_the_snapshot_query_and_artifacts(self):
        context = self.json['context']
        self.assertEqual(context['snapshot_context_id'], self.json_view.snapshot.context_id)
        self.assertEqual(context['query_sha256'], ei.digest(ei.canonical(self.request['query'])))
        self.assertEqual((context['unit'], context['unit_us'], context['engine_grid']), ('day', DAY, 'microsecond'))
        self.assertIn('patterns/trajectory_graph_sulo.py', context['artifacts'])
        self.assertEqual(self.json['context_id'], ei.digest(ei.canonical(context)))
        self.assertIsNotNone(self.rdf['context']['graph_sha256'])
        self.assertNotIn('events', self.json)

    def test_summary_counts_statuses_and_adapter_codes(self):
        self.assertEqual(self.json['summary'], {'patients': 5, 'decided_by_adapter': 0, 'adapter_codes': {},
                                                'statuses': {'excluded': 1, 'exact': 1, 'none': 1, 'possible': 1, 'relaxed': 1}})

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
        basis = result['request_entailed_concepts']['P1-fs'][NS + 'seizure']
        self.assertEqual(basis['kind'], 'rustdl_entailed')
        self.assertTrue(basis['asserted_supports'])
        run = result['context']['semantic_run']
        self.assertEqual((run['status'], run['ontology_sha256']), ('READY', result['semantic']['ontology_sha256']))
        self.assertEqual(run['backend']['name'], 'rustdl')
        # Entailment is request-specific: the ingest rows hold asserted concepts only.
        row = next(r for r in tgs.ingest_rows(view) if r['event_id'] == 'P1-fs')
        self.assertNotIn(NS + 'seizure', {c['concept'] for c in row['concepts']})
        self.assertEqual(row['entailment'], 'not_materialized')

    def test_declared_disjointness_needs_the_semantic_run(self):
        facts = deepcopy(self.facts)
        facts['vocabulary']['disjoint'] = [{'id': 'drug-not-seizure', 'classes': [NS + 'sodium_channel_blocker', NS + 'seizure']}]
        view = tgs.from_bounded_json(self.source, facts)
        with self.assertRaisesRegex(tgs.AdapterError, 'SEMANTIC_RUN_REQUIRED_FOR_DISJOINTNESS'):
            tgs.match_snapshot(view, self.request)
        result = tgs.match_snapshot(view, dict(self.request, semantic_policy=facts['vocabulary']))
        self.assertEqual(result['statuses'], self.json['statuses'])
        self.assertEqual({r['vocabulary_disjointness'] for r in tgs.ingest_rows(view)}, {'not_checked'})


class IngestRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source, cls.facts, cls.request = convert(load(EXAMPLE / 'query.json'), load(EXAMPLE / 'patients.json'))
        cls.view = tgs.from_bounded_json(source, cls.facts)

    def test_rows_are_the_same_with_and_without_a_request(self):
        before = tgs.ingest_rows(self.view)
        tgs.match_snapshot(self.view, self.request)
        hours = deepcopy(self.request)
        hours['windows'] = {'P1': {'start': 0, 'end': 10}}
        hours['unit'] = 'hour'
        tgs.match_snapshot(self.view, hours)
        self.assertEqual(tgs.ingest_rows(self.view), before)
        self.assertEqual(tgs.ingest_rows(self.view.snapshot), tgs.ingest_rows(tgs.SourceView(self.view.snapshot, 'snapshot')))

    def test_rows_hold_own_clock_bounds_box_verdict_and_asserted_concepts(self):
        rows = {r['event_id']: r for r in tgs.ingest_rows(self.view)}
        self.assertEqual(set(rows), set(self.view.snapshot.events))
        fs = rows['P2-fs']
        self.assertEqual((fs['clock_id'], fs['clock_origin']), ('clinical', ORIGIN))
        self.assertEqual(fs['start_us'], [140 * DAY, 170 * DAY])
        self.assertEqual(fs['end_us'], [140 * DAY + 1, 170 * DAY + 1])
        self.assertEqual((fs['start_var'], fs['end_var']), ('P2-fs-start', 'P2-fs-end'))
        self.assertEqual((fs['box_representable'], fs['non_box_constraints'], fs['shared_variables']), (True, [], []))
        self.assertEqual(fs['selection_state'], 'declared_source')
        bases = {c['concept']: c['basis'] for c in fs['concepts']}
        self.assertEqual(bases[NS + 'febrile_seizure'][0]['kind'], 'semantic_fact')
        self.assertEqual(bases[SPECIMEN][0]['kind'], 'event_kind_closure')
        self.assertTrue(all(c['basis'] for r in rows.values() for c in r['concepts']))
        self.assertTrue(all('reference_clock' not in r and 'comparability' not in r for r in rows.values()))


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

    def test_selected_classes_is_never_called_per_event(self):
        # Ancestors come from one parse of the ontology, not one call per event.
        with patch.object(bt, 'selected_classes', side_effect=AssertionError('reparse')):
            result = tgs.match_snapshot(self.view, request_for([('a', PROCESS)]))
            tgs.ingest_rows(self.view)
        self.assertEqual(result['statuses'], {'P1': 'exact'})

    def test_named_ancestors_close_upward(self):
        r = tgs.match_snapshot(self.view, request_for([('a', str(bt.BT.IntervalProcess)), ('b', SPECIMEN)], [after(1)]))
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        self.assertEqual(r['results'][0]['binding'], {'a': 'X', 'b': 'Y'})
        x = next(row for row in tgs.ingest_rows(self.view) if row['event_id'] == 'X')
        self.assertEqual({c['concept'] for c in x['concepts']}, bt.selected_classes({'event_kind': 'infusion'}))

    def test_unknown_step_concept_is_refused(self):
        with self.assertRaisesRegex(tgs.AdapterError, 'UNKNOWN_CONCEPT:https://example.org/not-a-class'):
            tgs.match_snapshot(self.view, request_for([('a', 'https://example.org/not-a-class')]))

    def test_unknown_relaxation_concept_is_refused(self):
        request = request_for([('a', INFUSION)], relaxations=[
            {'id': 'w', 'kind': 'widen_concept', 'cost': 1, 'step': 'a', 'concept': 'https://example.org/nowhere'}], budget=1)
        with self.assertRaisesRegex(tgs.AdapterError, 'UNKNOWN_CONCEPT:https://example.org/nowhere'):
            tgs.match_snapshot(self.view, request)

    def facts(self, event, iri):
        return {'profile': tgs.FACTS_PROFILE, 'facts': [
            {'id': 'f', 'patient_id': 'P1', 'episode_id': 'E1', 'kind': 'class', 'subject': {'event_id': event},
             'class_iri': iri}],
            'vocabulary': {'profile': 'joint-semantic-policy-1.0', 'id': 'v', 'classes': [], 'rules': [], 'disjoint': []}}

    def test_fact_class_must_be_declared(self):
        view = tgs.from_bounded_json(self.source, self.facts('X', 'https://example.org/undeclared'))
        with self.assertRaisesRegex(tgs.AdapterError, 'UNDECLARED_FACT_CLASS'):
            tgs.match_snapshot(view, request_for([('a', INFUSION)]))
        with self.assertRaisesRegex(tgs.AdapterError, 'UNDECLARED_FACT_CLASS'):
            tgs.ingest_rows(view)

    def test_fact_on_an_ontology_class_brings_its_ancestors_with_basis(self):
        view = tgs.from_bounded_json(self.source, self.facts('Y', INFUSION))
        y = {c['concept']: [b['kind'] for b in c['basis']]
             for row in tgs.ingest_rows(view) if row['event_id'] == 'Y' for c in row['concepts']}
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
        view = tgs.from_bounded_json(source)
        r = tgs.match_snapshot(view, request_for([('a', INFUSION), ('b', SPECIMEN)], [after(1)]))
        p1, p2 = r['results']
        self.assertEqual((p1['status'], p1['decided_by'], p1['adapter']['code']),
                         ('refused', 'adapter', 'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE'))
        self.assertEqual(p1['adapter']['constraints'][0]['edge'], 'source:b-after-a')
        self.assertEqual(p2['status'], 'possible')
        self.assertEqual(r['summary']['adapter_codes'], {'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE': 1})
        verdicts = {(row['patient_id'], row['box_representable'], tuple(row['non_box_constraints'])) for row in tgs.ingest_rows(view)}
        self.assertEqual(verdicts, {('P1', False, ('source:b-after-a',)), ('P2', True, ())})

    def test_source_constraint_the_box_implies_is_accepted(self):
        source = source_for([('A', 'P1', 'infusion', (0, DAY), (2 * DAY, 2 * DAY)),
                             ('B', 'P1', 'specimen_collection', (5 * DAY, 6 * DAY), (7 * DAY, 7 * DAY))],
                            [{'id': 'redundant', 'left_var': 'A-start', 'right_var': 'B-start',
                              'upper_us': -DAY, 'source_key': 'declared'}])
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [after(4, 6)])
        self.assertEqual(tgs.match_snapshot(tgs.from_bounded_json(source), request)['statuses'], {'P1': 'exact'})

    def test_variables_are_tightened_to_their_minimal_bounds(self):
        # The proper-interval edge alone tightens: end >= start + 1 lifts the end's floor.
        source = source_for([('A', 'P1', 'infusion', (5 * DAY, 9 * DAY), (0, 20 * DAY))])
        row = tgs.ingest_rows(tgs.from_bounded_json(source))[0]
        self.assertEqual(row['declared_end_us'], [0, 20 * DAY])
        self.assertEqual(row['end_us'], [5 * DAY + 1, 20 * DAY])

    def test_offset_clocks_are_rebased_by_their_exact_difference(self):
        clocks = [{'clock_id': 'c1', 'origin': ORIGIN, 'scope': 'global', 'policy': tgs.OFFSET_POLICY},
                  {'clock_id': 'c2', 'origin': '2026-01-02T00:00:00+01:00', 'scope': 'global', 'policy': tgs.OFFSET_POLICY}]
        source = source_for([('X', 'P1', 'infusion', (0, 0), (1, 1), 'X-start', 'c1'),
                             ('Y', 'P1', 'specimen_collection', (0, 0), (1, 1), 'Y-start', 'c2')], clocks=clocks)
        view = tgs.from_bounded_json(source)
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [after(23, 23)], unit='hour', window=(0, 48))
        with self.assertRaisesRegex(tgs.AdapterError, 'SHARED_WINDOW_CLOCK_REQUIRED'):
            tgs.match_snapshot(view, request)
        request['window']['clock_id'] = 'c1'
        r = tgs.match_snapshot(view, request)
        hour = 3_600_000_000
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        self.assertEqual(r['results'][0]['clock']['rebase_offsets_us'], {'c1': 0, 'c2': 23 * hour})
        y = next(e for e in r['results'][0]['engine_events'] if e['id'] == 'Y')
        self.assertEqual(y['start'], [23 * hour] * 2)
        # The ingest row keeps Y on its own clock.
        self.assertEqual(next(row for row in tgs.ingest_rows(view) if row['event_id'] == 'Y')['start_us'], [0, 0])

    def test_a_shared_window_across_clocks_needs_a_clock(self):
        # The reviewer's probe: two offset clocks 100 days apart and a shared `within` window.
        source = source_for([('X', 'P1', 'infusion', (DAY, DAY), (DAY + 1, DAY + 1), 'X-start', 'c1'),
                             ('Y', 'P2', 'infusion', (DAY, DAY), (DAY + 1, DAY + 1), 'Y-start', 'c2')],
                            clocks=offset_clocks(100))
        view = tgs.from_bounded_json(source)
        request = request_for([('a', INFUSION)], window=(0, 10), scope='within')
        with self.assertRaisesRegex(tgs.AdapterError, 'SHARED_WINDOW_CLOCK_REQUIRED'):
            tgs.match_snapshot(view, request)
        request['window']['clock_id'] = 'c1'
        self.assertEqual(tgs.match_snapshot(view, request)['statuses'], {'P1': 'exact', 'P2': 'none'})
        # Per-patient windows on each patient's own clock are explicit, so they are allowed.
        request = request_for([('a', INFUSION)], scope='within')
        del request['window']
        request['windows'] = {'P1': {'start': 0, 'end': 10}, 'P2': {'start': 0, 'end': 10}}
        self.assertEqual(tgs.match_snapshot(view, request)['statuses'], {'P1': 'exact', 'P2': 'exact'})

    def test_a_patient_window_without_a_clock_over_two_clocks_is_refused(self):
        source = source_for([('X', 'P1', 'infusion', (0, 0), (1, 1), 'X-start', 'c1'),
                             ('Y', 'P1', 'specimen_collection', (0, 0), (1, 1), 'Y-start', 'c2')], clocks=offset_clocks(1))
        request = request_for([('a', INFUSION)])
        del request['window']
        request['windows'] = {'P1': {'start': 0, 'end': 10}}
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)['results'][0]
        self.assertEqual((r['status'], r['adapter']['code']), ('refused', 'WINDOW_CLOCK_REQUIRED'))

    def test_window_clock_chooses_the_reference_clock(self):
        source = source_for([('X', 'P1', 'infusion', (0, 0), (1, 1), 'X-start', 'c1')], clocks=offset_clocks(1))
        request = request_for([('a', INFUSION)], unit='day', window=(-1, -1), scope='within')
        request['window']['clock_id'] = 'c2'
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request)
        self.assertEqual(r['statuses'], {'P1': 'exact'})
        self.assertEqual(r['results'][0]['clock']['rebase_offsets_us'], {'c1': -DAY, 'c2': 0})

    def test_patient_local_clocks_that_cannot_be_rebased_make_the_patient_incomparable(self):
        view = tgs.from_patient_local(load(ei.ROOT / 'examples/patient-local/source.json'))
        request = request_for([('a', PROCESS)])
        del request['window']
        request['windows'] = {p: {'start': -3650, 'end': 3650} for p in ('P1', 'P2', 'P3')}
        request['windows']['P4'] = {'start': -3650, 'end': 3650, 'clock_id': 'P4_common'}
        r = tgs.match_snapshot(view, request)
        p4 = next(x for x in r['results'] if x['patient_id'] == 'P4')
        self.assertEqual((p4['status'], p4['adapter']['code']), ('incomparable', 'CLOCKS_NOT_REBASABLE'))
        self.assertEqual((p4['adapter']['clocks'], p4['adapter']['excluded_events']), (['P4_other'], ['P4b']))
        # P1 relates its endpoints to a shared uncertain anchor: no box holds that.
        self.assertEqual(r['statuses'], {'P1': 'refused', 'P2': 'exact', 'P3': 'exact', 'P4': 'incomparable'})
        self.assertEqual(r['context']['time_domain'], 'bounded-patient-local-calendar-microseconds')
        self.assertIn('patterns/patient_local.py', r['context']['artifacts'])
        # A shared window over the patients' own local clocks is ambiguous.
        with self.assertRaisesRegex(tgs.AdapterError, 'SHARED_WINDOW_CLOCK_REQUIRED'):
            tgs.match_snapshot(view, request_for([('a', PROCESS)], window=(-3650, 3650)))

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


def assert_full_network_agrees(case, view, result, window):
    """Every binding check equals the replay over the patient's whole source network."""
    query = result['engine_query']
    by_id = {x['id']: x for x in query.get('relaxations', [])}
    compared = 0
    for r in result['results']:
        if 'source_check' not in r:
            continue
        check = r['source_check']
        full = tgs.full_network_check(view.snapshot, r['patient_id'], {'clinical': 0}, query,
                                      [by_id[i] for i in check['relaxations']], check['binding'], window,
                                      check['certainty_claimed'], r.get('engine_result', r).get('witness'))
        for key in ('feasible', 'entailed', 'engine_witness_confirmed'):
            case.assertEqual(full[key], check[key], (r['patient_id'], key))
        case.assertEqual(full['refutation'] is None, check['refutation'] is None)
        compared += 1
    return compared


class SharedVariableTests(unittest.TestCase):
    """A source variable named by several events reaches the engine as one variable (D22)."""

    def probe(self):
        return source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY), 'shared-start'),
                           ('Y', 'P1', 'specimen_collection', (0, 10 * DAY), (12 * DAY, 12 * DAY), 'shared-start'),
                           ('Z', 'P2', 'infusion', (0, 0), (1, 1)), ('W', 'P2', 'specimen_collection', (0, 0), (1, 1))])

    def test_a_shared_start_makes_after_zero_exact(self):
        # The reviewer's probe: X and Y share a start, so `after` with min 0 and max 0 holds in
        # every source timeline. Box projection would understate that as possible, so it was
        # refused (SHARED_SOURCE_VARIABLE); the engine now decides it, and the source network
        # confirms it.
        view = tgs.from_bounded_json(self.probe())
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [after(0, 0)])
        for projection in (True, False):
            with self.subTest(point_projection=projection):
                r = tgs.match_snapshot(view, request, point_projection=projection)
                p1, p2 = r['results']
                self.assertEqual((p1['status'], p1['decided_by'], p1['binding']), ('exact', 'engine', {'a': 'X', 'b': 'Y'}))
                self.assertNotIn('adapter', p1)
                self.assertEqual((p1['source_check']['entailed'], p1['source_check']['feasible']), (True, True))
                self.assertEqual(p1['projection'], 'points' if projection else 'intervals')
                self.assertEqual({e['id']: e['start_var'] for e in p1['engine_events']},
                                 {'X': 'shared-start', 'Y': 'shared-start'})
                self.assertEqual(p2['status'], 'exact')
                self.assertEqual(r['summary']['adapter_codes'], {})
                self.assertEqual(assert_full_network_agrees(self, view, r, tgs._window(request['window'], DAY)), 2)
        # One start cannot also be a day later: none, decided by the engine.
        r = tgs.match_snapshot(view, request_for([('a', INFUSION), ('b', SPECIMEN)], [after(1)]))
        self.assertEqual((r['results'][0]['status'], r['results'][0]['decided_by']), ('none', 'engine'))
        # The store contract is unchanged: the rows still mark the patient as not box-representable.
        rows = {row['event_id']: row for row in tgs.ingest_rows(view)}
        self.assertEqual((rows['X']['box_representable'], rows['X']['shared_variables']), (False, ['shared-start']))
        self.assertTrue(rows['Z']['box_representable'])

    def test_engine_refusals_of_shared_variables_are_per_patient(self):
        view = tgs.from_bounded_json(self.probe())
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [after(0, 0)])
        # A linked group over the engine's limit: the shared start links X's and Y's variables.
        with patch.object(tgs.tg, 'MAX_LINKED_VARIABLES', 2):
            r = tgs.match_snapshot(view, request, point_projection=False)
        p1, p2 = r['results']
        self.assertEqual((p1['status'], p1['decided_by'], p1['adapter']['code']),
                         ('refused', 'adapter', 'SHARED_VARIABLE_COMPONENT_LIMIT'))
        self.assertEqual(p1['adapter']['shared_variables'], [{'variable': 'shared-start', 'events': ['X', 'Y']}])
        self.assertEqual(p2['status'], 'exact')
        # A shared variable whose events' ranges do not meet. A validated snapshot cannot
        # produce one: an inconsistent source is refused when prepared, and every event naming
        # a variable is sent that variable's one tightened range. So the conflict is injected
        # into the engine's input: Y's range for the shared start moves past X's.
        real = tg.match

        def conflicting(query, patient):
            for event in patient['events']:
                if event['id'] == 'Y':
                    event['start'] = [11 * DAY, 11 * DAY]
            return real(query, patient)
        with patch.object(tgs.tg, 'match', side_effect=conflicting):
            r = tgs.match_snapshot(view, request)
        p1, p2 = r['results']
        self.assertEqual((p1['status'], p1['adapter']['code']), ('refused', 'EMPTY_SHARED_VARIABLE'))
        self.assertEqual(p2['status'], 'exact')
        self.assertEqual(r['summary']['adapter_codes'], {'EMPTY_SHARED_VARIABLE': 1})

    def test_a_non_box_constraint_is_still_refused_with_shared_variables(self):
        # X ends at least two days before Y ends: an edge between events no box holds.
        source = source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 20 * DAY), 'shared-start'),
                             ('Y', 'P1', 'specimen_collection', (0, 10 * DAY), (11 * DAY, 20 * DAY), 'shared-start')],
                            [{'id': 'x-ends-first', 'left_var': 'X-end', 'right_var': 'Y-end',
                              'upper_us': -2 * DAY, 'source_key': 'declared'}])
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request_for([('a', INFUSION), ('b', SPECIMEN)], [after(0, 0)]))
        p1 = r['results'][0]
        self.assertEqual((p1['status'], p1['adapter']['code']), ('refused', 'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE'))
        self.assertEqual([c['edge'] for c in p1['adapter']['constraints']], ['source:x-ends-first'])
        self.assertEqual(p1['adapter']['shared_variables'], [{'variable': 'shared-start', 'events': ['X', 'Y']}])

    def test_a_strict_order_the_engine_misses_is_refuted_by_the_source_network(self):
        # Y starts when X ends. The engine's events only end at or after they start, so it
        # finds X empty and the two starts equal possible; the source's start + 1 <= end
        # refutes that, and the safety net reports unresolved rather than possible.
        source = source_for([('X', 'P1', 'infusion', (DAY, 5 * DAY), (DAY, 6 * DAY)),
                             ('Y', 'P1', 'specimen_collection', (0, 0), (9 * DAY, 9 * DAY), 'X-end')])
        r = tgs.match_snapshot(tgs.from_bounded_json(source), request_for([('a', INFUSION), ('b', SPECIMEN)], [after(0, 0)]))
        p1 = r['results'][0]
        self.assertEqual((p1['status'], p1['adapter']['code'], p1['engine_result']['status']),
                         ('unresolved', 'POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK', 'possible'))
        self.assertIn('proper:X', p1['source_check']['refutation'])
        self.assertEqual(p1['projection'], 'intervals')

    def strict_chain(self):
        # Y starts when X ends (P1); P2 has the same times without the shared variable.
        return source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (0, 20 * DAY)),
                           ('Y', 'P1', 'specimen_collection', (0, 0), (30 * DAY, 30 * DAY), 'X-end'),
                           ('X2', 'P2', 'infusion', (0, 10 * DAY), (0, 20 * DAY)),
                           ('Y2', 'P2', 'specimen_collection', (DAY, 20 * DAY), (30 * DAY, 30 * DAY))])

    def test_a_possible_binding_the_source_entails_is_exact(self):
        # The review's repro: Y's start is X's end, so Y starts at least a microsecond after X.
        # The engine lets X end as it starts and says possible; the source entails the binding.
        view = tgs.from_bounded_json(self.strict_chain())
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [after(1)], unit='microsecond', window=(0, 40 * DAY))
        p1 = tgs.match_snapshot(view, request)['results'][0]
        self.assertEqual((p1['status'], p1['complete'], p1['decided_by'], p1['binding']),
                         ('exact', True, 'adapter', {'a': 'X', 'b': 'Y'}))
        self.assertEqual(p1['adapter'], {'code': 'EXACT_BY_SOURCE_ENTAILMENT', 'engine_status': 'possible'})
        self.assertEqual((p1['engine_result']['status'], p1['engine_result']['complete']), ('possible', True))
        self.assertTrue(p1['source_check']['entailed'])
        self.assertNotIn('witness', p1)
        # The same for a relaxed result's certainty: unrelaxed, b can only be X itself.
        relaxed = request_for([('a', INFUSION), ('b', INFUSION)], [after(1)], unit='microsecond', window=(0, 40 * DAY),
                              relaxations=[{'id': 'b-any-process', 'kind': 'widen_concept', 'cost': '1', 'step': 'b',
                                            'concept': PROCESS}], budget='1')
        p1 = tgs.match_snapshot(view, relaxed)['results'][0]
        self.assertEqual((p1['status'], p1['certainty'], p1['complete'], p1['relaxations'], p1['binding']),
                         ('relaxed', 'exact', True, ['b-any-process'], {'a': 'X', 'b': 'Y'}))
        self.assertEqual((p1['engine_result']['certainty'], p1['adapter']['code']), ('possible', 'EXACT_BY_SOURCE_ENTAILMENT'))

    def test_a_binding_not_entailed_under_a_shared_end_leaves_exactness_undecided(self):
        # Another binding might be exact over the source and not over the engine's looser model.
        view = tgs.from_bounded_json(self.strict_chain())
        request = request_for([('a', INFUSION), ('b', SPECIMEN)], [after(1, 5 * DAY)], unit='microsecond',
                              window=(0, 40 * DAY))
        p1, p2 = tgs.match_snapshot(view, request)['results']
        self.assertEqual((p1['status'], p1['complete'], p1['note'], p1['decided_by']),
                         ('possible', False, 'EXACTNESS_NOT_DECIDED_STRICT_INTERVALS', 'adapter'))
        self.assertEqual(p1['adapter'], {'code': 'EXACTNESS_NOT_DECIDED_STRICT_INTERVALS', 'engine_status': 'possible',
                                         'engine_complete': True})
        self.assertTrue(p1['source_check']['feasible'])
        self.assertFalse(p1['source_check']['entailed'])
        # Without a shared end the engine's model is the source's box: possible stays complete.
        self.assertEqual((p2['status'], p2['complete'], p2['decided_by']), ('possible', True, 'engine'))
        self.assertNotIn('adapter', p2)

    def test_point_projection_with_shared_variables_equals_intervals(self):
        # P1-P3 share a start between x and y; P4 shares none; P5 starts y when x ends.
        events = []
        for p, z in (('P1', (6, 9)), ('P2', (3, 5)), ('P3', (20, 21))):
            events += [(p + 'x', p, 'infusion', (DAY, 4 * DAY), (30 * DAY, 30 * DAY), p + '-start'),
                       (p + 'y', p, 'specimen_collection', (DAY, 4 * DAY), (31 * DAY, 31 * DAY), p + '-start'),
                       (p + 'z', p, 'specimen_collection', (z[0] * DAY, z[1] * DAY), (32 * DAY, 32 * DAY))]
        events += [('P4x', 'P4', 'infusion', (DAY, 4 * DAY), (30 * DAY, 30 * DAY)),
                   ('P4y', 'P4', 'specimen_collection', (DAY, 4 * DAY), (31 * DAY, 31 * DAY)),
                   ('P4z', 'P4', 'specimen_collection', (6 * DAY, 9 * DAY), (32 * DAY, 32 * DAY)),
                   ('P5x', 'P5', 'infusion', (DAY, 5 * DAY), (3 * DAY, 6 * DAY)),
                   ('P5y', 'P5', 'specimen_collection', (0, 0), (31 * DAY, 31 * DAY), 'P5x-end'),
                   ('P5z', 'P5', 'specimen_collection', (8 * DAY, 9 * DAY), (32 * DAY, 32 * DAY))]
        view = tgs.from_bounded_json(source_for(events))
        request = request_for([('a', INFUSION), ('b', SPECIMEN), ('c', SPECIMEN)],
                              [after(0, None, 'a', 'b'), after(2, 8, 'b', 'c')], window=(0, 60))
        points = tgs.match_snapshot(view, request)
        intervals = tgs.match_snapshot(view, request, point_projection=False)
        self.assertEqual(points['statuses'], intervals['statuses'])
        self.assertEqual([decision_fields(r) for r in points['results']], [decision_fields(r) for r in intervals['results']])
        self.assertEqual(points['statuses'], {'P1': 'exact', 'P2': 'possible', 'P3': 'none', 'P4': 'possible', 'P5': 'exact'})
        projections = {r['patient_id']: r['projection'] for r in points['results']}
        # A shared end would order other events' starts, which points drop: P5 keeps intervals.
        self.assertEqual(projections, {'P1': 'points', 'P2': 'points', 'P3': 'points', 'P4': 'points', 'P5': 'intervals'})
        p1 = points['results'][0]
        self.assertEqual({e['id']: e['start_var'] for e in p1['engine_events'] if e['id'] in ('P1x', 'P1y')},
                         {'P1x': 'P1-start', 'P1y': 'P1-start'})
        self.assertTrue(all('end_var' not in e for e in p1['engine_events']))
        window = tgs._window(request['window'], DAY)
        self.assertEqual(assert_full_network_agrees(self, view, points, window), 4)
        self.assertEqual(assert_full_network_agrees(self, view, intervals, window), 4)


class ProjectionTests(unittest.TestCase):
    def test_without_a_during_relation_events_go_to_the_engine_as_points(self):
        # A three-step chain with uncertain starts: polynomial point-forest method with points,
        # budgeted backtracking with intervals, same decisions.
        events, steps = [], []
        for p, shift in (('P1', 0), ('P2', 3), ('P3', 40)):
            events += [(p + 'x', p, 'infusion', ((1 + shift) * DAY, (4 + shift) * DAY), (50 * DAY, 50 * DAY)),
                       (p + 'y', p, 'specimen_collection', (6 * DAY, 9 * DAY), (51 * DAY, 51 * DAY)),
                       (p + 'z', p, 'specimen_collection', (12 * DAY, 13 * DAY), (52 * DAY, 52 * DAY))]
        view = tgs.from_bounded_json(source_for(events))
        request = request_for([('a', INFUSION), ('b', SPECIMEN), ('c', SPECIMEN)],
                              [after(4, 5, 'a', 'b'), after(6, 6, 'b', 'c')], window=(0, 60))
        points = tgs.match_snapshot(view, request)
        intervals = tgs.match_snapshot(view, request, point_projection=False)
        self.assertEqual(points['statuses'], intervals['statuses'])
        self.assertEqual([decision_fields(r) for r in points['results']], [decision_fields(r) for r in intervals['results']])
        possible = [r for r in points['results'] if r['status'] == 'possible']
        self.assertTrue(possible)
        for r in possible:
            self.assertEqual((r['projection'], r['method']), ('points', 'point_forest_intervals'))
            self.assertTrue(r['source_check']['feasible'])
        for r in intervals['results']:
            if r['status'] == 'possible':
                self.assertEqual((r['projection'], r['method']), ('intervals', 'bounded_backtracking'))
        self.assertIn('none', points['statuses'].values())


class WitnessCheckTests(unittest.TestCase):
    def test_one_event_bound_to_two_steps_is_not_reported_possible(self):
        # One event has one start, so A and B cannot be five days apart on it. Before D13 the
        # engine gave each step its own time variables and said possible, and this adapter
        # refuted it against the source network; the engine now shares the event's variables
        # and decides none itself. The adapter's refutation path stays as a safety net.
        source = source_for([('X', 'P1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY))])
        request = request_for([('a', INFUSION), ('b', INFUSION)], [after(5)])
        for projection in (False, True):
            with self.subTest(point_projection=projection):
                r = tgs.match_snapshot(tgs.from_bounded_json(source), request, point_projection=projection)['results'][0]
                self.assertEqual((r['status'], r['decided_by']), ('none', 'engine'))
                self.assertNotIn('engine_result', r)

    def test_bound_event_check_equals_the_full_network_check(self):
        source, facts, request = convert(load(EXAMPLE / 'query.json'), load(EXAMPLE / 'patients.json'))
        one = source_for([('X', 'Q1', 'infusion', (0, 10 * DAY), (11 * DAY, 11 * DAY))])
        cases = [(tgs.from_bounded_json(source, facts), request),
                 (tgs.from_bounded_json(one), request_for([('a', INFUSION), ('b', INFUSION)], [after(5)]))]
        compared = 0
        for view, req in cases:
            result = tgs.match_snapshot(view, req)
            query = result['engine_query']
            by_id = {x['id']: x for x in query.get('relaxations', [])}
            windows = tgs._window(req.get('window'), DAY)
            for r in result['results']:
                if 'source_check' not in r:
                    continue
                pid = r['patient_id']
                window = tgs._window(req['windows'][pid], DAY) if 'windows' in req else windows
                check = r['source_check']
                relaxations = [by_id[i] for i in check['relaxations']]
                engine = r.get('engine_result', r)
                full = tgs.full_network_check(view.snapshot, pid, {'clinical': 0}, query, relaxations,
                                              check['binding'], window, check['certainty_claimed'], engine.get('witness'))
                for key in ('feasible', 'entailed', 'engine_witness_confirmed'):
                    self.assertEqual(full[key], check[key], (pid, key))
                self.assertEqual(full['refutation'] is None, check['refutation'] is None)
                compared += 1
        # P1, P2 and P3; the one-event case is now decided none by the engine (D13).
        self.assertEqual(compared, 3)

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
        self.request = request_for([('a', ANTIBIOTIC), ('b', SPECIMEN)], [after(1)], unit='microsecond', window=(0, 10))

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
        self.assertEqual((a['concept']['concept'], a['concept']['basis'][0]['kind']), (ANTIBIOTIC, 'rustdl_entailed'))
        row = next(x for x in tgs.ingest_rows(view) if x['event_id'] == 'A')
        self.assertEqual(row['selection_state'], 'accepted')
        self.assertEqual(r['context']['route_provenance']['acceptance_policy_id'], self.policy['id'])
        for name in ('patterns/claim_projection.py', 'patterns/claim_rdf.py', 'patterns/bounded_rdf.py',
                     'patterns/joint_evidence.py', 'patterns/semantic_support.py'):
            self.assertIn(name, r['context']['artifacts'])
        self.assertEqual(r['context']['semantic_run']['model_sha256'], r['semantic']['model_sha256'])
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
        request = request_for([('first', item), ('next', item)], [after(1, None, 'first', 'next')],
                              unit='minute', window=(0, 7 * 24 * 60))
        r = tgs.match_snapshot(view, request)
        self.assertFalse(r['context']['clinical_mapping_verified'])
        self.assertFalse(r['clinical_mapping_verified'])
        self.assertEqual(r['context']['route_provenance']['acceptance_policy_id'], 'caller_declared_accept_all')
        self.assertEqual(r['context']['time_domain'], 'bounded-patient-local-calendar-microseconds')
        self.assertIn('patterns/local_claim_projection.py', r['context']['artifacts'])
        self.assertEqual(r['statuses'], {episode['patient_id']: 'exact'})
        rows = tgs.ingest_rows(view)
        self.assertEqual(len(rows), len(store['claims']))
        for row in rows:
            bases = {c['concept']: c['basis'] for c in row['concepts']}
            self.assertEqual(bases[item][0]['kind'], 'semantic_fact')
            self.assertEqual(bases[item][0]['supports'][0]['support_ids'], ['accept_claim_' + row['event_id']])
            self.assertEqual(row['clock_policy'], 'patient-local-calendar-microseconds-v1')
            self.assertTrue(row['box_representable'])
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
