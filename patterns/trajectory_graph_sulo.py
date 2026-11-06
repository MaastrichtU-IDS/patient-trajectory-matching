"""SULO snapshots into the trajectory-graph engine, with each bound step's evidence carried back.

Bounded JSON, bounded RDF, patient-local graphs and claim selections all reach a
`bounded_intervals.Snapshot`. This adapter reads that snapshot, projects each patient's
events into the engine's input form (concept IRIs, integer start and end ranges on one
microsecond grid), runs `trajectory_graph.match`, and checks every binding the engine
reports against the source's temporal network plus the query's constraints.

- Ingest. `ingest_rows(view)` is the query-independent projection of a snapshot: one row
  per event on its own clock, with its tightened bounds, its patient's box verdict, and its
  asserted concepts with their bases. This is what an execution store ingests.
- Matching. `match_snapshot(view, request)` rebases clocks, runs the engine and checks.
- Concepts. The event kind's named ancestors from the pinned ontology, parsed once per run;
  semantic class facts carried with the snapshot; and, when the request supplies a semantic
  policy, rustDL-entailed memberships with their supports. A query or relaxation concept
  the vocabulary does not know is refused (UNKNOWN_CONCEPT).
- Time. Variables are tightened to their minimal bounds. A patient whose source network a
  box cannot represent (a source constraint the box does not imply, or a variable shared by
  two events) is refused. A `possible` binding the source network refutes is never
  reported as possible.
- Clocks and units. One clock per patient: offset-datetime clocks are rebased exactly; any
  other second clock makes the patient incomparable. Declared units convert to microseconds
  exactly; calendar months and years are refused.

See docs/trajectory-graph-sulo-adapter.md.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import sys

from rdflib import Graph, OWL, RDF, RDFS, URIRef

from . import bounded_intervals as bt
from . import exact_intervals as ei
from . import trajectory_graph as tg
from .temporal_stn import Edge, Network, ZERO

PROFILE = 'trajectory-graph-sulo-1.0'
INGEST_PROFILE = 'trajectory-graph-sulo-ingest-1.0'
REQUEST_PROFILE = 'trajectory-graph-sulo-request-1.0'
FACTS_PROFILE = 'trajectory-graph-sulo-facts-1.0'
OFFSET_POLICY = 'offset-datetime-microseconds-v1'
ENGINE_GRID = 'microsecond'
UNITS = {'microsecond': 1, 'millisecond': 1_000, 'second': 1_000_000, 'minute': 60_000_000,
         'hour': 3_600_000_000, 'day': 86_400_000_000, 'week': 604_800_000_000}
CALENDAR_UNITS = ('month', 'year', 'quarter', 'decade')
REQUEST_FIELDS = {'profile', 'query', 'unit', 'windows', 'window', 'semantic_policy'}
ONTOLOGY_FILES = ('ontology/vendor/sulo-0.2.14.ttl', 'ontology/pro-solid-profile.ttl',
                  'ontology/exact-interval-profile.ttl', 'ontology/bounded-interval-profile.ttl')
FILES = ('patterns/trajectory_graph_sulo.py', 'patterns/trajectory_graph.py', 'patterns/temporal_stn.py',
         'patterns/bounded_intervals.py', 'patterns/exact_intervals.py', 'patterns/pro_solid.py',
         'schemas/bounded-interval.schema.json', 'patterns/requirements.lock.txt') + ONTOLOGY_FILES
RDF_FILES = ('patterns/bounded_rdf.py', 'ontology/bounded-rdf-profile.ttl')
INF = math.inf
# Adapter statuses, beside the engine's exact/possible/relaxed/unresolved/none/excluded.
REFUSED, INCOMPARABLE = 'refused', 'incomparable'


class AdapterError(ValueError):
    """A request or source the adapter refuses as a whole; the message is a stable code."""


def require(condition, code):
    if not condition:
        raise AdapterError(code)


def digest(value):
    return ei.digest(ei.canonical(value))


@dataclass(frozen=True)
class SourceView:
    """A snapshot plus what travels with it: semantic facts and their supports.

    `event_supports` and `fact_supports` hold claim-selection supports (claim, decision,
    claim digest) keyed by event or fact identifier; a declared source has none.
    `vocabulary` is the joint semantic policy declaring the facts' classes. `files` are the
    route's own artifacts, bound into every result context.
    """
    snapshot: bt.Snapshot
    route: str
    semantic_facts: tuple = ()
    fact_supports: dict = field(default_factory=dict)
    event_supports: dict = field(default_factory=dict)
    vocabulary: dict | None = None
    provenance: dict = field(default_factory=dict)
    files: tuple = ()


# ---------------------------------------------------------------- snapshot routes

def _facts(document):
    """A caller's facts document: {profile, vocabulary (joint semantic policy), facts}."""
    if document is None:
        return (), {}, None, {}, ()
    from . import joint_evidence as joint
    require(isinstance(document, dict) and set(document) == {'profile', 'vocabulary', 'facts'}
            and document['profile'] == FACTS_PROFILE, 'UNSUPPORTED_FACTS_DOCUMENT')
    joint.validate_policy(document['vocabulary'])
    facts = document['facts']
    require(isinstance(facts, list) and all(isinstance(f, dict) for f in facts), 'INVALID_SEMANTIC_FACTS')
    bt.unique(facts, 'id')
    basis = {'basis': 'caller_declared_fact', 'facts_sha256': digest(document)}
    return (tuple(deepcopy(facts)), {f['id']: [dict(basis, fact_id=f['id'])] for f in facts},
            deepcopy(document['vocabulary']), {'facts_sha256': digest(document)}, joint.FILES)


def from_bounded_json(source, facts=None):
    """Bounded JSON through `bounded_intervals.prepare`."""
    rows, supports, vocabulary, provenance, files = _facts(facts)
    return SourceView(bt.prepare(source), 'bounded-json', rows, supports, {}, vocabulary, provenance, files)


def from_bounded_rdf(graph, facts=None):
    """A bounded RDF graph through `bounded_rdf.prepare_graph`."""
    from . import bounded_rdf as br
    rows, supports, vocabulary, provenance, files = _facts(facts)
    return SourceView(br.prepare_graph(graph), 'bounded-rdf', rows, supports, {}, vocabulary, provenance,
                      RDF_FILES + files)


def from_bounded_source(source, facts=None):
    """The canonical route for bounded JSON: export to RDF, then read the graph back."""
    from . import bounded_rdf as br
    return from_bounded_rdf(br.export_source(source), facts)


def from_patient_local(source_or_graph, facts=None):
    """A patient-local source or graph, always read through `patient_local.prepare_graph`."""
    from . import patient_local as local
    rows, supports, vocabulary, provenance, files = _facts(facts)
    graph = source_or_graph if isinstance(source_or_graph, Graph) else local.export_source(source_or_graph)
    return SourceView(local.prepare_graph(graph), 'patient-local-rdf', rows, supports, {}, vocabulary, provenance,
                      RDF_FILES + local.FILES + files)


def from_claims(store, policy, semantic_policy, *, local_graph=False):
    """Claims accepted by an explicit policy, selected as `claim_projection` selects them.

    A bounded store (`claim-store-1.0`) or a patient-local one, as JSON or as a closed
    claim-description graph (`local_graph=True` for the patient-local fields). The accepted
    rows become a source exactly as the projection builds it, and the snapshot is read
    through the graph route. Pending, rejected, superseded and withdrawn claims contribute
    nothing.
    """
    from . import claim_projection as cp
    from . import claim_rdf as cr
    from . import local_claim_projection as lcp
    from . import patient_local as local
    if isinstance(store, Graph):
        store = cr.decode(store, fields=cr.LOCAL_FIELDS if local_graph else cr.FIELDS)
    require(isinstance(store, dict), 'UNSUPPORTED_CLAIM_STORE')
    local_store = store.get('profile') in (lcp.STORE_PROFILE, lcp.RECORD_STORE_PROFILE)
    store, policy, semantic_policy = deepcopy((store, policy, semantic_policy))
    selection = (lcp.select if local_store else cp.select)(store, policy, semantic_policy)
    require(not selection['blockers'], 'BLOCKED_ACCEPTED_CONFLICT')
    require(any(selection['selected'].values()), 'EMPTY_ACCEPTED_VIEW')
    provenance = {'store_sha256': cr.digest(store), 'acceptance_policy_id': policy['id'],
                  'acceptance_policy_sha256': cr.digest(policy), 'semantic_policy_sha256': cr.digest(semantic_policy),
                  'accepted_claim_ids': selection['accepted_claim_ids'],
                  'interpretation': 'caller_accepted_assertions_for_analysis'}
    if local_store:
        profile = local.RECORD_PROFILE if store['profile'] == lcp.RECORD_STORE_PROFILE else local.PROFILE_ID
    else:
        profile = bt.PROFILE_ID
    source = {'profile': profile, 'dataset_id': store['dataset_id'],
              'snapshot_id': 'accepted_' + cr.digest(provenance), 'clocks': deepcopy(store['clocks']),
              **{k: selection['selected'][k] for k in ('events', 'variables', 'constraints')}}
    if local_store:
        snapshot, route = local.prepare_graph(local.export_source(source)), 'local-claim-selection-rdf'
        files = RDF_FILES + cp.FILES + lcp.FILES
    else:
        from . import bounded_rdf as br
        snapshot, route = br.prepare_graph(br.export_source(source)), 'claim-selection-rdf'
        files = RDF_FILES + cp.FILES
    supports = selection['row_supports']
    facts = tuple(selection['selected']['semantic_facts'])
    from . import joint_evidence as joint
    return SourceView(snapshot, route, facts,
                      {f['id']: supports['semantic_facts:' + f['id']] for f in facts},
                      {e: supports['events:' + e] for e in snapshot.events}, semantic_policy,
                      dict(provenance, selection_source=source, claim_states=selection['claims']),
                      files + joint.FILES)


# ---------------------------------------------------------------- vocabulary and concepts

class Vocabulary:
    """The pinned ontology `bounded_intervals.selected_classes` reads, parsed once per run."""

    def __init__(self):
        self.graph = (ei.base.ontology() + Graph().parse(ei.PROFILE)
                      + Graph().parse(ei.ROOT / 'ontology/bounded-interval-profile.ttl'))
        named = {s for s in self.graph.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
        for s, o in self.graph.subject_objects(RDFS.subClassOf):
            named.update(x for x in (s, o) if isinstance(x, URIRef))
        self.named = {str(c) for c in named}
        self._ancestors = {}

    def ancestors(self, iri):
        """Named subclass closure, the walk `selected_classes` performs."""
        if iri not in self._ancestors:
            pending, found = [URIRef(iri)], set()
            while pending:
                cls = pending.pop()
                if cls in found:
                    continue
                found.add(cls)
                pending.extend(p for p in self.graph.objects(cls, RDFS.subClassOf) if isinstance(p, URIRef))
            self._ancestors[iri] = {str(c) for c in found}
        return self._ancestors[iri]


def _query_concepts(query):
    return ({s['concept'] for s in query['steps']}
            | {x['concept'] for x in query.get('relaxations', []) if x['kind'] == 'widen_concept'})


def _asserted_concepts(view, vocabulary, known):
    """Each event's asserted concepts with their bases: kind closure, facts, fact closure."""
    from . import semantic_support as ss
    snapshot = view.snapshot
    concepts = {eid: {} for eid in snapshot.events}

    def add(eid, iri, basis):
        concepts[eid].setdefault(iri, []).append(basis)

    for eid, event in sorted(snapshot.events.items()):
        kind = str(bt.KINDS[event['event_kind']])
        for iri in sorted(vocabulary.ancestors(kind)):
            add(eid, iri, {'kind': 'event_kind_closure', 'event_kind': event['event_kind'], 'kind_class': kind})
    for fact in view.semantic_facts:
        if fact.get('kind') != 'class' or 'event_id' not in fact.get('subject', {}):
            continue
        eid, iri = fact['subject']['event_id'], ss.iri(fact['class_iri'])
        require(eid in snapshot.events and bt.scope(snapshot.events[eid]) == bt.scope(fact), 'FACT_EVENT_SCOPE')
        require(iri in known, 'UNDECLARED_FACT_CLASS:' + iri)
        add(eid, iri, {'kind': 'semantic_fact', 'fact_id': fact['id'], 'supports': view.fact_supports.get(fact['id'], [])})
        if iri in vocabulary.named:
            for parent in sorted(vocabulary.ancestors(iri) - {iri}):
                add(eid, parent, {'kind': 'semantic_fact_closure', 'fact_id': fact['id'], 'asserted_class': iri})
    return concepts


def _entail(view, policy, wanted, timeout_seconds):
    """Class memberships the checked rustDL route entails for the query's policy classes."""
    from . import joint_evidence as joint
    from . import semantic_support as ss
    joint.validate_policy(policy)
    selection = {'selected_semantic_facts': list(view.semantic_facts),
                 'row_supports': {'semantic_facts:' + f['id']: view.fact_supports.get(f['id'], []) for f in view.semantic_facts}}
    module, axiom_supports = joint.compile_semantics(view.snapshot, selection, policy)
    model = ss.build_model(view.snapshot, module)
    classes = sorted(set(wanted) & set(policy['classes']))
    out = {'semantic_policy_id': policy['id'], 'semantic_policy_sha256': digest(policy),
           'model_sha256': digest(model), 'axiom_supports': axiom_supports, 'checked_classes': classes}
    if not classes and not policy['disjoint']:
        return dict(out, status='NOT_NEEDED', ontology_sha256=None, backend=None, memberships={})
    require(len(classes) <= 8, 'SEMANTIC_SELECTOR_LIMIT')
    # With disjointness declared the run is needed for consistency even if no class is named.
    support = ss.check(model, classes, timeout_seconds=timeout_seconds)
    require(support['status'] == 'READY', 'SEMANTIC_SUPPORT_UNRESOLVED:' + support.get('reason_code', support['status']))
    return dict(out, status='READY', ontology_sha256=support['ontology_sha256'],
                backend=support['backend_result'].get('backend'), memberships=support['memberships'])


# ---------------------------------------------------------------- the box

def _tightened(network):
    """Each variable's minimal bounds in its source network."""
    out = {}
    for name in network.names:
        if name != ZERO:
            out[name] = (-network.bound(ZERO, name)[0], network.bound(name, ZERO)[0])
    return out


def _non_box(network, bounds, edge_evidence):
    """Source constraints the tightened box does not already imply.

    With only per-variable bounds and proper-interval edges left, the feasible region is the
    tightened box intersected with the proper-interval edges. A source constraint the box
    implies (max(left) - min(right) <= upper) changes nothing; any other one would be lost.
    """
    out = []
    for e in network.edges:
        if e.id.startswith('source:') and bounds[e.left][1] - bounds[e.right][0] > e.upper:
            out.append({'edge': e.id, 'left': e.left, 'right': e.right, 'upper': e.upper,
                        'source': edge_evidence[e.id]['source']})
    return out


def box_analysis(snapshot):
    """Tightened bounds, and for each patient whether a box per event represents the source.

    Two things a box cannot hold: a source constraint the tightened box does not imply, and
    a variable referenced by two events (an equality between them). Query-independent.
    """
    bounds = {}
    for network in snapshot.networks.values():
        bounds.update(_tightened(network))
    patients = {}
    for (pid, _), network in sorted(snapshot.networks.items()):
        info = patients.setdefault(pid, {'non_box_constraints': [], 'shared_variables': []})
        info['non_box_constraints'] += _non_box(network, bounds, snapshot.edge_evidence)
    uses = defaultdict(set)
    for eid, event in snapshot.events.items():
        uses[event['start_var']].add(eid)
        uses[event['end_var']].add(eid)
    for vid, eids in sorted(uses.items()):
        if len(eids) > 1:
            patients[snapshot.variables[vid]['patient_id']]['shared_variables'].append(
                {'variable': vid, 'events': sorted(eids)})
    for info in patients.values():
        info['box_representable'] = not info['non_box_constraints'] and not info['shared_variables']
    return bounds, patients


# ---------------------------------------------------------------- ingest

def ingest_rows(view):
    """One row per snapshot event, independent of any request: the store's ingest contract.

    Times are the event's own clock, declared and tightened. The patient's box verdict, the
    asserted concepts and their bases, and the evidence travel with each row. Entailed
    classes are not materialized here.
    """
    if isinstance(view, bt.Snapshot):
        view = SourceView(view, 'snapshot')
    snapshot = view.snapshot
    vocabulary = Vocabulary()
    known = set(vocabulary.named) | set((view.vocabulary or {}).get('classes', []))
    concepts = _asserted_concepts(view, vocabulary, known)
    bounds, boxes = box_analysis(snapshot)
    clocks = {c['clock_id']: c for c in snapshot.source['clocks']}
    disjointness = 'not_checked' if view.vocabulary and view.vocabulary.get('disjoint') else 'none_declared'
    rows = []
    for eid, e in sorted(snapshot.events.items()):
        item, box = snapshot.evidence[eid], boxes[e['patient_id']]
        clock = snapshot.variables[e['start_var']]['clock_id']
        rows.append({
            'profile': INGEST_PROFILE, 'snapshot_context_id': snapshot.context_id,
            'time_domain': snapshot.context.get('time_domain'),
            'patient_id': e['patient_id'], 'event_id': eid, 'episode_id': e['episode_id'],
            'record_id': e['record_id'], 'record_status': e['status'],
            'event_kind': e['event_kind'], 'kind_class': str(bt.KINDS[e['event_kind']]),
            'clock_id': clock, 'clock_policy': clocks[clock]['policy'], 'clock_origin': clocks[clock]['origin'],
            'declared_start_us': [snapshot.variables[e['start_var']][k] for k in ('lower_us', 'upper_us')],
            'declared_end_us': [snapshot.variables[e['end_var']][k] for k in ('lower_us', 'upper_us')],
            'start_us': list(bounds[e['start_var']]), 'end_us': list(bounds[e['end_var']]), 'ongoing': False,
            'start_var': e['start_var'], 'end_var': e['end_var'],
            'box_representable': box['box_representable'],
            'non_box_constraints': [c['edge'] for c in box['non_box_constraints']],
            'shared_variables': [s['variable'] for s in box['shared_variables']],
            'evidence_id': item['evidence_id'], 'evidence_context_id': item['context_id'], 'claim': item['claim'],
            'patient_bearer': item['patient_bearer'], 'source_record': item['source_record'],
            'source_hash': item['source_hash'], 'source_key': e['source_key'],
            'selection_state': 'accepted' if view.event_supports.get(eid) else 'declared_source',
            'claim_supports': deepcopy(view.event_supports.get(eid, [])),
            'concepts': [{'concept': iri, 'basis': basis} for iri, basis in sorted(concepts[eid].items())],
            'entailment': 'not_materialized', 'vocabulary_disjointness': disjointness})
    return rows


# ---------------------------------------------------------------- request

def _window(value, scale, code='INVALID_WINDOW'):
    if value is None:
        return None
    require(isinstance(value, dict) and {'start', 'end'} <= set(value) <= {'start', 'end', 'clock_id'}, code)
    require(type(value['start']) is int and type(value['end']) is int and value['start'] <= value['end'], code)
    require(value.get('clock_id') is None or isinstance(value['clock_id'], str), code)
    out = {'start': ei.checked_us(value['start'] * scale), 'end': ei.checked_us(value['end'] * scale)}
    if value.get('clock_id') is not None:
        out['clock_id'] = value['clock_id']
    return out


def validate_request(request):
    require(isinstance(request, dict) and request.get('profile') == REQUEST_PROFILE, 'UNSUPPORTED_REQUEST_PROFILE')
    require(set(request) <= REQUEST_FIELDS and {'query', 'unit'} <= set(request), 'REQUEST_FIELDS')
    unit = request['unit']
    require(isinstance(unit, str), 'UNSUPPORTED_UNIT')
    require(unit.rstrip('s') not in CALENDAR_UNITS, 'CALENDAR_UNIT_REFUSED')
    require(unit in UNITS, 'UNSUPPORTED_UNIT')
    # D9: the index window is explicit, per patient or shared; never inferred from the data.
    require('windows' in request or 'window' in request, 'INDEX_WINDOW_REQUIRED')
    require(isinstance(request.get('windows', {}), dict) and all(type(k) is str for k in request.get('windows', {})),
            'INVALID_WINDOWS')
    require('window' not in request or isinstance(request['window'], dict), 'INVALID_WINDOW')
    tg.validate_query(request['query'])
    return request


def engine_query(query, scale):
    """The query on the microsecond grid: every bound, grace and relaxed bound scaled exactly."""
    out = deepcopy(query)
    for r in out.get('relations', []):
        for key in ('min', 'max', 'grace'):
            if r.get(key) is not None:
                r[key] = ei.checked_us(r[key] * scale)
    for x in out.get('relaxations', []):
        for key in ('max', 'grace'):
            if key in x:
                x[key] = ei.checked_us(x[key] * scale)
    return tg.validate_query(out)


# ---------------------------------------------------------------- binding checks

def _replay(names, edges, query, relaxations, binding, var, certainty, witness):
    """Replay one binding: entailment over `edges`, feasibility with the query's constraints."""
    _, relations = tg.applied(query, relaxations)
    constraints = tg.compile_constraints(query, relations)
    base = Network(names, edges)
    assert base.feasible, 'a compiled snapshot network is feasible'
    query_edges, entailed = [], True
    for i, c in enumerate(constraints):
        x, y = var(c.x), var(c.y)
        if c.hi != INF:
            query_edges.append(Edge(f'query:{i}:{c.source}:max', y, x, int(c.hi)))
            d = base.bound(y, x)[0]
            entailed = entailed and d is not None and d <= c.hi
        if c.lo != -INF:
            query_edges.append(Edge(f'query:{i}:{c.source}:min', x, y, int(-c.lo)))
            d = base.bound(x, y)[0]
            entailed = entailed and d is not None and d <= -c.lo
    joint = Network(names, edges + query_edges)
    out = {'certainty_claimed': certainty, 'relaxations': [x['id'] for x in relaxations],
           'binding': dict(sorted(binding.items())), 'feasible': joint.feasible, 'entailed': entailed,
           'engine_witness_confirmed': None, 'witness': None,
           'refutation': None if joint.feasible else joint.negative_cycle}
    if joint.feasible:
        values = joint.witness()
        out['witness'] = {s: values[var((s, 's'))] for s in sorted(binding)}
        if witness:
            pins = []
            for s, v in sorted(witness.items()):
                pins += [Edge(f'witness:{s}:upper', var((s, 's')), ZERO, v),
                         Edge(f'witness:{s}:lower', ZERO, var((s, 's')), -v)]
            out['engine_witness_confirmed'] = Network(names, edges + query_edges + pins).feasible
    return out


def _anchors(window):
    return [Edge('anchor:START:upper', '$START', ZERO, window['start']),
            Edge('anchor:START:lower', ZERO, '$START', -window['start']),
            Edge('anchor:END:upper', '$END', ZERO, window['end']),
            Edge('anchor:END:lower', ZERO, '$END', -window['end'])]


def binding_check(query, relaxations, binding, spans, window, certainty, witness=None):
    """Check a binding over the bound events alone.

    Valid once the patient is box-representable (no unimplied source constraint, no shared
    variable): the feasible region is then each event's tightened box with its proper-interval
    edge, events independent, so the bound events' variables carry every relevant constraint.
    `spans` maps an event to its rebased tightened (start, end) bounds. Steps bound to the
    same event share its variables.
    """
    names, edges = {'$START', '$END'}, _anchors(window)
    for eid in sorted(set(binding.values())):
        (sl, sh), (fl, fh) = spans[eid]
        s, f = eid + '.s', eid + '.f'
        names |= {s, f}
        edges += [Edge(f'bound:{s}:upper', s, ZERO, sh), Edge(f'bound:{s}:lower', ZERO, s, -sl),
                  Edge(f'bound:{f}:upper', f, ZERO, fh), Edge(f'bound:{f}:lower', ZERO, f, -fl),
                  Edge('proper:' + eid, s, f, -1)]

    def var(term):
        name, part = term
        return '$' + name if name in tg.ANCHORS else binding[name] + '.' + part
    return _replay(names, edges, query, relaxations, binding, var, certainty, witness)


def full_network_check(snapshot, pid, offsets, query, relaxations, binding, window, certainty, witness=None):
    """The same check over every variable and edge of the patient's source networks.

    O(V^3) in the patient's variables; kept as the reference `binding_check` is tested against.
    """
    names, edges = {'$START', '$END'}, _anchors(window)
    name = lambda n: n if n == ZERO else 'v:' + n
    for (p, _), network in sorted(snapshot.networks.items()):
        if p != pid:
            continue
        for e in network.edges:
            upper = e.upper
            if e.left == ZERO:
                upper -= offsets[snapshot.variables[e.right]['clock_id']]
            elif e.right == ZERO:
                upper += offsets[snapshot.variables[e.left]['clock_id']]
            edges.append(Edge(e.id, name(e.left), name(e.right), upper))
            names.update(name(n) for n in (e.left, e.right) if n != ZERO)

    def var(term):
        step, part = term
        if step in tg.ANCHORS:
            return '$' + step
        event = snapshot.events[binding[step]]
        return name(event['start_var'] if part == 's' else event['end_var'])
    return _replay(names, edges, query, relaxations, binding, var, certainty, witness)


# ---------------------------------------------------------------- matching

def _evidence(view, eid, concept, concepts):
    return {'event_id': eid, 'evidence': deepcopy(view.snapshot.evidence[eid]),
            'claim_supports': deepcopy(view.event_supports.get(eid, [])),
            'concept': {'concept': concept, 'basis': deepcopy(concepts[eid].get(concept, []))}}


def _bound(view, query, relaxations, binding, concepts):
    step_concepts, _ = tg.applied(query, relaxations)
    return {s: _evidence(view, e, step_concepts[s], concepts) for s, e in sorted(binding.items())}


def _verified(result, check, query, view, concepts):
    """Apply the binding check to the engine's result for one patient."""
    by_id = {x['id']: x for x in query.get('relaxations', [])}
    status = result['status']
    out = dict(result)
    out['decided_by'] = 'engine'
    if status in ('exact', 'possible', 'relaxed'):
        certainty = result.get('certainty', status)
        relaxations = [by_id[i] for i in result.get('relaxations', [])]
        verdict = check(relaxations, result['binding'], certainty, result.get('witness'))
        out['source_check'] = verdict
        out['bound_evidence'] = _bound(view, query, relaxations, result['binding'], concepts)
        downgrade = None
        if certainty == 'exact' and not verdict['entailed']:
            downgrade = 'EXACT_NOT_ENTAILED_BY_SOURCE_NETWORK' if verdict['feasible'] else 'EXACT_BINDING_REFUTED_BY_SOURCE_NETWORK'
        elif certainty == 'possible' and not verdict['feasible']:
            downgrade = 'POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK'
        if downgrade:
            out = {'patient_id': result['patient_id'], 'decided_by': 'adapter', 'engine_result': result,
                   'source_check': verdict, 'bound_evidence': out['bound_evidence'], 'complete': False}
            if verdict['feasible']:  # exact over the box but not over the network: still possible
                if status == 'relaxed':
                    out.update({k: result[k] for k in ('relaxations', 'cost', 'binding', 'method')},
                               status='relaxed', certainty='possible')
                else:
                    out.update(status='possible', binding=result['binding'], method=result['method'])
                out.update(witness=verdict['witness'], adapter={'code': downgrade, 'engine_status': status})
            else:
                out.update(status='unresolved', reason=downgrade,
                           adapter={'code': downgrade, 'engine_status': status, 'engine_certainty': certainty})
            return out
        if certainty == 'possible' and status == 'possible' and verdict['engine_witness_confirmed'] is False:
            out.update(engine_witness=result['witness'], witness=verdict['witness'],
                       adapter={'code': 'ENGINE_WITNESS_REPLACED_BY_SOURCE_WITNESS'})
        return out
    if status == 'unresolved' and 'relaxed_match' in result:
        match = result['relaxed_match']
        relaxations = [by_id[i] for i in match['relaxations']]
        verdict = check(relaxations, match['binding'], match['certainty'], None)
        holds = verdict['entailed'] if match['certainty'] == 'exact' else verdict['feasible']
        if not holds and verdict['feasible']:
            out['relaxed_match'] = dict(match, certainty='possible')
            out['adapter'] = {'code': 'RELAXED_MATCH_EXACTNESS_NOT_ENTAILED'}
        elif not holds:
            out.pop('relaxed_match')
            out['engine_result'] = result
            out['adapter'] = {'code': 'RELAXED_MATCH_REFUTED_BY_SOURCE_NETWORK'}
        if 'relaxed_match' in out:
            out['relaxed_bound_evidence'] = _bound(view, query, relaxations, match['binding'], concepts)
        out['relaxed_source_check'] = verdict
    return out


def match_snapshot(view, request, *, timeout_seconds=20, point_projection=True):
    """Match one snapshot against a trajectory-graph query whose concepts are class IRIs.

    `view` is a `SourceView` (or a bare `Snapshot`, read as having no semantic facts).
    `request` = {profile, query, unit, windows | window, semantic_policy?}; times in the
    query and windows are integers in `unit`, windows relative to their clock's origin.
    `point_projection=False` sends every event to the engine as an interval, even when the
    query never reads an end; the decisions are the same.
    """
    if isinstance(view, bt.Snapshot):
        view = SourceView(view, 'snapshot')
    validate_request(request)
    request = deepcopy(request)
    snapshot = view.snapshot
    scale = UNITS[request['unit']]
    query = engine_query(request['query'], scale)
    semantic_policy = request.get('semantic_policy')

    vocabulary = Vocabulary()
    known = set(vocabulary.named)
    for policy in (view.vocabulary, semantic_policy):
        if policy is not None:
            known.update(policy['classes'])
    for concept in sorted(_query_concepts(query)):
        require(concept in known, 'UNKNOWN_CONCEPT:' + concept)
    if semantic_policy is None and view.vocabulary is not None:
        derived = {r['then'] for r in view.vocabulary.get('rules', [])}
        for concept in sorted(_query_concepts(query) & derived):
            # The facts' own policy derives this class; matching on asserted facts alone
            # would silently miss it.
            raise AdapterError('ENTAILMENT_REQUIRED:' + concept)
        # Asserted facts under declared disjointness may be inconsistent; only the checked
        # semantic run can tell.
        require(not (view.vocabulary.get('disjoint') and view.semantic_facts), 'SEMANTIC_RUN_REQUIRED_FOR_DISJOINTNESS')
    concepts = _asserted_concepts(view, vocabulary, known)
    semantic, entailed = None, {}
    if semantic_policy is not None:
        semantic = _entail(view, semantic_policy, _query_concepts(query), timeout_seconds)
        claims = {snapshot.evidence[eid]['claim']: eid for eid in snapshot.events}
        for cls, members in sorted(semantic.pop('memberships').items()):
            for individual in sorted(members):
                if individual in claims:
                    asserted = [s for s in semantic['axiom_supports']
                                if individual in (s['axiom'].get('individual'), s['axiom'].get('subject'))]
                    basis = {'kind': 'rustdl_entailed', 'semantic_policy_id': semantic_policy['id'],
                             'ontology_sha256': semantic['ontology_sha256'], 'model_sha256': semantic['model_sha256'],
                             'asserted_supports': asserted}
                    concepts[claims[individual]].setdefault(cls, []).append(basis)
                    entailed.setdefault(claims[individual], {})[cls] = basis

    clocks = {c['clock_id']: c for c in snapshot.source['clocks']}
    by_patient = defaultdict(list)
    for eid, event in sorted(snapshot.events.items()):
        by_patient[event['patient_id']].append(eid)
    clock_of = lambda eid: snapshot.variables[snapshot.events[eid]['start_var']]['clock_id']
    per_patient = request.get('windows', {})
    shared = request.get('window')
    if shared is not None and 'clock_id' not in shared:
        # A shared window on each patient's own clock would cover different periods.
        covered = {clock_of(e) for p, eids in by_patient.items() if p not in per_patient for e in eids}
        require(len(covered) <= 1, 'SHARED_WINDOW_CLOCK_REQUIRED')
    patients = sorted(set(by_patient) | set(per_patient))
    windows = {p: _window(per_patient[p], scale) if p in per_patient else _window(shared, scale) for p in patients}
    bounds, boxes = box_analysis(snapshot)

    results = []
    for pid in patients:
        results.append(_patient(pid, windows[pid], by_patient.get(pid, []), snapshot, clocks, clock_of, bounds,
                                boxes.get(pid), concepts, query, view, point_projection))

    files = tuple(sorted(set(FILES + view.files)))
    semantic_run = None if semantic is None else {k: semantic[k] for k in (
        'status', 'semantic_policy_id', 'semantic_policy_sha256', 'model_sha256', 'ontology_sha256', 'backend',
        'checked_classes')}
    context = {'profile': PROFILE, 'engine_profile': tg.PROFILE_ID, 'route': view.route,
               'snapshot_profile': snapshot.context['profile'], 'snapshot_context_id': snapshot.context_id,
               'dataset_id': snapshot.source['dataset_id'], 'snapshot_id': snapshot.source['snapshot_id'],
               'graph_sha256': snapshot.context.get('graph_sha256'),
               'source_sha256': snapshot.context.get('source_sha256', snapshot.context.get('projection_sha256')),
               'time_domain': snapshot.context.get('time_domain'),
               'request_sha256': digest(request), 'query_sha256': digest(request['query']),
               'engine_query_sha256': digest(query), 'unit': request['unit'], 'unit_us': scale,
               'engine_grid': ENGINE_GRID, 'point_projection': point_projection,
               'vocabulary_sha256': digest(view.vocabulary) if view.vocabulary is not None else None,
               'semantic_policy_sha256': digest(semantic_policy) if semantic_policy is not None else None,
               'semantic_run': semantic_run,
               'route_provenance': {k: v for k, v in view.provenance.items() if k not in ('selection_source', 'claim_states')},
               'concept_sources': 'event_kind_closure+semantic_facts' + ('+rustdl_entailed' if semantic_policy else ''),
               'clinical_mapping_verified': False,
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in files}}
    summary = {'patients': len(results),
               'statuses': dict(sorted(Counter(r['status'] for r in results).items())),
               'decided_by_adapter': sum(r['decided_by'] == 'adapter' for r in results),
               'adapter_codes': dict(sorted(Counter(r['adapter']['code'] for r in results if 'adapter' in r).items()))}
    return {'profile': PROFILE, 'context_id': digest(context), 'context': context,
            'engine_query': query, 'results': results, 'summary': summary,
            'statuses': {r['patient_id']: r['status'] for r in results},
            # Request-specific: entailment runs only for the query's policy classes.
            'request_entailed_concepts': entailed, 'semantic': semantic,
            'clinical_mapping_verified': False}


def _patient(pid, window, eids, snapshot, clocks, clock_of, bounds, box, concepts, query, view, point_projection):
    def adapter(status, code, **details):
        return {'patient_id': pid, 'status': status, 'decided_by': 'adapter', 'complete': True,
                'adapter': {'code': code, **details}}

    excluded = {**tg.match(query, {'patient_id': pid, 'window': None, 'events': []}), 'decided_by': 'engine'}
    if window is None:
        return excluded
    if not eids:
        return adapter(INCOMPARABLE, 'PATIENT_NOT_IN_SNAPSHOT')
    used = sorted({clock_of(e) for e in eids})
    if 'clock_id' not in window and len(used) > 1:
        return adapter(REFUSED, 'WINDOW_CLOCK_REQUIRED', clocks=used)
    # One clock per patient: the window's clock if it names one, else the patient's only clock.
    reference = window.get('clock_id', used[0])
    if reference not in clocks or clocks[reference]['scope'] not in ('global', 'patient:' + pid):
        return adapter(REFUSED, 'WINDOW_CLOCK_NOT_PATIENT_CLOCK', clock_id=reference)
    offsets, unrebased = {reference: 0}, []
    for clock in used:
        if clock == reference:
            continue
        if clocks[clock]['policy'] == OFFSET_POLICY and clocks[reference]['policy'] == OFFSET_POLICY:
            # Both origins are instants: the exact integer difference rebases the clock.
            offsets[clock] = ei.normalize(clocks[clock]['origin'], clocks[reference]['origin'])
        else:
            unrebased.append(clock)
    if unrebased:
        return adapter(INCOMPARABLE, 'CLOCKS_NOT_REBASABLE', reference_clock=reference, clocks=unrebased,
                       excluded_events=sorted(e for e in eids if clock_of(e) in unrebased))
    if box['non_box_constraints']:
        return adapter(REFUSED, 'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE', constraints=box['non_box_constraints'],
                       shared_variables=box['shared_variables'])
    if box['shared_variables']:
        # Until engine events can share variables (D13), a shared variable is an equality
        # between events the engine would treat as independent.
        return adapter(REFUSED, 'SHARED_SOURCE_VARIABLE', shared_variables=box['shared_variables'])

    def shifted(vid, eid):
        lo, hi = bounds[vid]
        off = offsets[clock_of(eid)]
        return [lo + off, hi + off]

    spans = {e: (shifted(snapshot.events[e]['start_var'], e), shifted(snapshot.events[e]['end_var'], e)) for e in eids}
    # No `during` relation means no constraint reads an end, and after tightening every
    # start has a feasible end: the engine may take each event as a point. Ties on start
    # under first/last occurrence would be broken by end, so then intervals are kept.
    ordered = any(s.get('occurrence', 'any') != 'any' for s in query['steps'])
    points = (point_projection and not any(r['kind'] == 'during' for r in query.get('relations', []))
              and (not ordered or len({tuple(spans[e][0]) for e in eids}) == len(eids)))
    events = []
    for eid in eids:
        event = {'id': eid, 'concepts': sorted(concepts[eid]), 'start': spans[eid][0]}
        if not points:
            event['end'] = spans[eid][1]
        events.append(event)
    engine_window = {'start': window['start'], 'end': window['end']}
    result = tg.match(query, {'patient_id': pid, 'window': engine_window, 'events': events})

    def check(relaxations, binding, certainty, witness):
        return binding_check(query, relaxations, binding, spans, engine_window, certainty, witness)
    out = _verified(result, check, query, view, concepts)
    out['projection'] = 'points' if points else 'intervals'
    out['clock'] = {'reference_clock': reference, 'rebase_offsets_us': dict(sorted(offsets.items())),
                    'policy': clocks[reference]['policy'], 'origin': clocks[reference]['origin']}
    # Request-specific: the events exactly as the engine received them, on the reference clock.
    out['engine_events'] = [dict(e, tightened_end=spans[e['id']][1]) if points else e for e in events]
    return out


# ---------------------------------------------------------------- command line

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    example = ei.ROOT / 'examples/trajectory-graph-sulo'
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--source', type=Path, help='bounded JSON source (default: the example)')
    inputs.add_argument('--graph', type=Path, help='bounded RDF graph (Turtle)')
    parser.add_argument('--facts', type=Path, help='semantic facts document')
    parser.add_argument('--request', type=Path, default=example / 'request.json')
    parser.add_argument('--route', choices=('graph', 'json'), default='graph',
                        help='read a JSON source through RDF (canonical) or directly')
    parser.add_argument('--output', type=Path, help='write the full result here')
    args = parser.parse_args(argv)
    try:
        facts_path = args.facts or (example / 'facts.json' if not (args.source or args.graph) else None)
        facts = json.loads(facts_path.read_text()) if facts_path else None
        request = json.loads(args.request.read_text())
        if args.graph:
            view = from_bounded_rdf(Graph().parse(args.graph, format='turtle'), facts)
        else:
            source = json.loads((args.source or example / 'source.json').read_text())
            view = (from_bounded_source if args.route == 'graph' else from_bounded_json)(source, facts)
        result = match_snapshot(view, request)
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}), file=sys.stderr)
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'profile': PROFILE, 'route': result['context']['route'], 'context_id': result['context_id'],
                      'statuses': result['statuses'], 'summary': result['summary']}, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
