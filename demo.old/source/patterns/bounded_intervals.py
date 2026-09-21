"""Validated synthetic bounded-time source -> PRO/SOLID graph + temporal networks.

Source JSON is authoritative in this profile. RDF is a lossless evidence projection,
not a second ingestion API. No uncertain boundary is asserted to have an exact value.
"""
from dataclasses import asdict, dataclass
from collections import defaultdict
import hashlib
import json
import re

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD

from . import exact_intervals as ei
from .temporal_stn import Edge, Network, ZERO

PROFILE_ID = 'bounded-interval-1.0'
QUERY_PROFILE = 'bounded-interval-query-1.0'
BT = Namespace('https://example.org/trajectory/bounded/')
D = Namespace('https://example.org/trajectory/bounded-data/')
SCHEMA = ei.ROOT / 'schemas/bounded-interval.schema.json'
KINDS = {'infusion': BT.Infusion, 'specimen_collection': BT.SpecimenCollection,
         'recorded_input_segment': BT.RecordedInputSegment}


class InconsistentSource(ei.ContractError):
    def __init__(self, scope, network, evidence):
        self.details = {'status': 'INCONSISTENT_SOURCE', 'scope': list(scope),
                        'negative_cycle': network.negative_cycle,
                        'edges': [asdict(e) for e in network.edges], 'edge_evidence': evidence}
        super().__init__('INCONSISTENT_SOURCE: ' + ei.canonical(self.details))


def validate(value, *, query=False):
    schema = json.loads(SCHEMA.read_text())
    if query:
        schema = schema['$defs']['query']
    return _validate_contract(value, schema, query=query)


def _validate_contract(value, schema, *, query=False):
    errors = list(Draft202012Validator(schema).iter_errors(value))
    ei.require(not errors, 'INVALID_QUERY_SCHEMA' if query else 'INVALID_SOURCE_SCHEMA')
    # JSON Schema considers 1.0 an integer; the execution contract requires JSON ints.
    def integers(item):
        if isinstance(item, dict):
            for key, val in item.items():
                if key.endswith('_us'):
                    ei.checked_us(val)
                if key in ('id', 'record_id', 'patient_id', 'episode_id', 'clock_id',
                           'start_var', 'end_var', 'left_var', 'right_var', 'left', 'right'):
                    ei.identifier(val)
                integers(val)
        elif isinstance(item, list):
            for val in item:
                integers(val)
    integers(value)
    if query:
        names = [s['id'] for s in value['slots']]
        ei.require(len(names) == len(set(names)), 'DUPLICATE_SLOT')
        unique(value['constraints'], 'id')
        for c in value['constraints']:
            ei.require(c['left'] in names and c['right'] in names and c['left'] != c['right'], 'INVALID_SLOT_REFERENCE')
            if c['operator'] == 'gap':
                ei.require(c['min_gap_us'] <= c['max_gap_us'], 'INVALID_GAP_BOUNDS')
    return value


def unique(rows, key):
    result = {r[key]: r for r in rows}
    ei.require(len(result) == len(rows), 'DUPLICATE_IDENTIFIER:' + key)
    return result


def scope(row):
    return row['patient_id'], row['episode_id']


@dataclass
class Snapshot:
    source: dict
    graph: Graph
    events: dict
    variables: dict
    networks: dict
    edge_evidence: dict
    evidence: dict
    context: dict
    context_id: str


def compile_source(source):
    """Validate and compile normalized records without inventing graph evidence."""
    validate(source)
    for clock in source['clocks']:
        ei.normalize(clock['origin'], clock['origin'])
    return _compile_validated(source)


def _compile_validated(source):
    """Shared integer network kernel; entry points validate their clock contracts."""
    # Own the source snapshot rather than retaining mutable caller dictionaries.
    source = json.loads(ei.canonical(source))
    variables = unique(source['variables'], 'id')
    events = unique(source['events'], 'id')
    unique(source['events'], 'record_id')
    constraints = unique(source['constraints'], 'id')
    clocks = unique(source['clocks'], 'clock_id')
    for clock in clocks.values():
        ei.require(clock['scope'] == 'global' or re.fullmatch(r'patient:[A-Za-z0-9_-]+', clock['scope']), 'UNSUPPORTED_CLOCK_SCOPE')
    groups, edges, edge_evidence = defaultdict(list), defaultdict(list), {}
    for vid, v in sorted(variables.items()):
        ei.require(v['lower_us'] <= v['upper_us'], 'REVERSED_VARIABLE_BOUNDS')
        ei.require(v['clock_id'] in clocks, 'UNKNOWN_CLOCK')
        ei.require(clocks[v['clock_id']]['scope'] in ('global', 'patient:' + v['patient_id']), 'CLOCK_PATIENT_SCOPE_MISMATCH')
        groups[scope(v)].append(vid)
        for side, left, right, upper in [('lower', ZERO, vid, -v['lower_us']), ('upper', vid, ZERO, v['upper_us'])]:
            eid = 'bound:' + vid + ':' + side
            edges[scope(v)].append(Edge(eid, left, right, upper))
            edge_evidence[eid] = {'kind': 'variable_bound', 'variable': vid, 'side': side, 'source': v}
    for cid, c in sorted(constraints.items()):
        ei.require(c['left_var'] in variables and c['right_var'] in variables, 'UNKNOWN_VARIABLE')
        left, right = variables[c['left_var']], variables[c['right_var']]
        ei.require(scope(left) == scope(right) and left['clock_id'] == right['clock_id'], 'CROSS_SCOPE_SOURCE_CONSTRAINT')
        eid = 'source:' + cid
        edges[scope(left)].append(Edge(eid, c['left_var'], c['right_var'], c['upper_us']))
        edge_evidence[eid] = {'kind': 'source_constraint', 'source': c}
    for event in events.values():
        ei.require(event['start_var'] in variables and event['end_var'] in variables, 'UNKNOWN_VARIABLE')
        start, end = variables[event['start_var']], variables[event['end_var']]
        ei.require(scope(start) == scope(end) == scope(event) and start['clock_id'] == end['clock_id'], 'EVENT_VARIABLE_SCOPE_MISMATCH')
        eid = 'proper:' + event['id']
        edges[scope(event)].append(Edge(eid, event['start_var'], event['end_var'], -1))
        edge_evidence[eid] = {'kind': 'proper_interval', 'event_id': event['id'], 'rule': 'start + 1 <= end', 'source': event}
    networks = {}
    for group, names in sorted(groups.items()):
        network = Network(names, sorted(edges[group], key=lambda e: e.id))
        if not network.feasible:
            raise InconsistentSource(group, network, {e.id: edge_evidence[e.id] for e in network.edges})
        networks[group] = network
    return source, events, variables, networks, edge_evidence


def prepare(source):
    return _prepare_compiled(compile_source(source))


def _prepare_compiled(compiled, *, profile_id=PROFILE_ID, graph_builder=None, extra_files=(),
                      time_domain='bounded-integer-microseconds'):
    source, events, variables, networks, edge_evidence = compiled
    graph, evidence = (graph_builder or build_graph)(source)
    files = ('patterns/bounded_intervals.py', 'patterns/temporal_stn.py', 'schemas/bounded-interval.schema.json',
             'ontology/bounded-interval-profile.ttl', 'ontology/exact-interval-profile.ttl',
             'ontology/pro-solid-profile.ttl', 'ontology/vendor/sulo-0.2.14.ttl',
             'patterns/exact_intervals.py', 'patterns/pro_solid.py', 'patterns/requirements.lock.txt')
    context = {'profile': profile_id, 'dataset_id': source['dataset_id'], 'snapshot_id': source['snapshot_id'],
               'source_sha256': ei.digest(ei.canonical(source)), 'graph_sha256': ei.digest(ei.turtle_text(graph)),
               'time_domain': time_domain,
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in files + extra_files}}
    context_id = ei.digest(ei.canonical(context))
    for item in evidence.values():
        item['context_id'] = context_id
        item['evidence_id'] = profile_id + ':' + ei.digest(ei.canonical(item))
    return Snapshot(source, graph, events, variables, networks, edge_evidence, evidence, context, context_id)


def build_graph(source, *, origin_datatype=XSD.dateTimeStamp):
    """Internal writer for a validated source; emit no sampled point timestamps."""
    g, evidence = Graph(), {}
    for prefix, ns in [('sulo', ei.S), ('bt', BT), ('ei', ei.EI), ('ex', ei.EX), ('data', D)]:
        g.bind(prefix, ns)

    def node(path, cls):
        result = D[path]
        g.add((result, RDF.type, cls))
        return result

    def scalar(parent, name, cls, value, datatype=XSD.string, unit=None):
        child = node(str(parent).removeprefix(str(D)) + '/' + name, cls)
        g.add((parent, ei.S.hasDirectPart, child))
        g.add((child, ei.S.hasValue, Literal(value, datatype=datatype, normalize=False)))
        if unit:
            g.add((child, ei.S.hasDirectPart, unit))
            g.add((unit, RDF.type, ei.S.Unit))
        return child

    def provenance(parent, row):
        scalar(parent, 'source-key', ei.EX.SourceKey, row['source_key'])
        scalar(parent, 'source-hash', ei.EX.SourceHash, ei.digest(ei.canonical(row)))
        scalar(parent, 'source-location', ei.EX.SourceLocation, 'constructed://bounded-interval/source.json')

    snapshot = node('snapshot', BT.Snapshot)
    scalar(snapshot, 'dataset', BT.DatasetIdentifier, source['dataset_id'])
    scalar(snapshot, 'snapshot', BT.SnapshotIdentifier, source['snapshot_id'])
    for c in source['clocks']:
        clock = node('clocks/' + c['clock_id'], ei.EI.TemporalReferenceSystem)
        for field, cls in [('clock_id', ei.EI.ClockIdentifier), ('scope', ei.EI.ClockScope), ('policy', ei.EI.ClockPolicy)]:
            scalar(clock, field, cls, c[field])
        scalar(clock, 'origin', ei.EI.ClockOrigin, c['origin'], origin_datatype, ei.EI.Second)
    for v in source['variables']:
        variable = node('variables/' + v['id'], BT.TemporalVariable)
        g.add((snapshot, ei.S.hasDirectPart, variable))
        scalar(variable, 'lower', BT.LowerBound, v['lower_us'], XSD.integer, BT.Microsecond)
        scalar(variable, 'upper', BT.UpperBound, v['upper_us'], XSD.integer, BT.Microsecond)
        scalar(variable, 'patient', ei.EX.PatientIdentifier, v['patient_id'])
        scalar(variable, 'episode', ei.EX.EpisodeIdentifier, v['episode_id'])
        binding = node('variables/' + v['id'] + '/clock', ei.EI.ClockBinding)
        g.add((variable, ei.S.hasDirectPart, binding))
        g.add((binding, ei.S.refersTo, D['clocks/' + v['clock_id']]))
        provenance(variable, v)
    for c in source['constraints']:
        constraint = node('constraints/' + c['id'], BT.DifferenceConstraint)
        g.add((snapshot, ei.S.hasDirectPart, constraint))
        for field, cls in [('left_var', BT.LeftOperandBinding), ('right_var', BT.RightOperandBinding)]:
            binding = node('constraints/' + c['id'] + '/' + field, cls)
            g.add((constraint, ei.S.hasDirectPart, binding))
            g.add((binding, ei.S.refersTo, D['variables/' + c[field]]))
        scalar(constraint, 'operator', BT.OperatorDatum, 'left_minus_right_le')
        scalar(constraint, 'upper', BT.UpperBound, c['upper_us'], XSD.integer, BT.Microsecond)
        provenance(constraint, c)
    for e in source['events']:
        path = 'events/' + e['id']
        process = node(path, KINDS[e['event_kind']])
        person = node('persons/' + e['patient_id'], ei.EX.Person)
        identifier = node('persons/' + e['patient_id'] + '/id', ei.EX.PatientIdentifier)
        g.add((person, ei.S.hasFeature, identifier))
        g.add((identifier, ei.S.hasValue, Literal(e['patient_id'], datatype=XSD.string)))
        role = node(path + '/patient-role', ei.EX.PatientRole)
        g.add((process, ei.S.hasParticipant, role))
        g.add((role, ei.S.isFeatureOf, person))
        interval = node(path + '/interval', BT.OccurrenceInterval)
        g.add((process, ei.S.atTime, interval))
        for side, cls in [('start', BT.StartDescriptor), ('end', BT.EndDescriptor)]:
            descriptor = node(path + '/' + side, cls)
            g.add((interval, ei.S.hasDirectPart, descriptor))
            g.add((descriptor, ei.S.refersTo, D['variables/' + e[side + '_var']]))
        record = node('records/' + e['record_id'], ei.EX.SourceRecord)
        g.add((record, ei.S.refersTo, process))
        for field, cls in [('id', ei.EX.EventIdentifier), ('record_id', ei.EX.RecordIdentifier),
                           ('episode_id', ei.EX.EpisodeIdentifier), ('status', ei.EX.RecordStatus)]:
            scalar(record, field, cls, e[field])
        provenance(record, e)
        evidence[e['id']] = {'process': str(process), 'patient_role': str(role), 'patient_bearer': str(person),
                             'interval': str(interval), 'start_descriptor': str(D[path + '/start']),
                             'end_descriptor': str(D[path + '/end']), 'source_record': str(record),
                             'source': e, 'source_hash': ei.digest(ei.canonical(e)),
                             'start_variable': str(D['variables/' + e['start_var']]),
                             'end_variable': str(D['variables/' + e['end_var']])}
    return g, evidence


def selected_classes(event):
    """Named subclass closure from pinned modules, limited to process selectors."""
    ontology = ei.base.ontology() + Graph().parse(ei.PROFILE) + Graph().parse(ei.ROOT / 'ontology/bounded-interval-profile.ttl')
    pending, found = [KINDS[event['event_kind']]], set()
    while pending:
        cls = pending.pop()
        if cls in found:
            continue
        found.add(cls)
        pending.extend(p for p in ontology.objects(cls, RDFS.subClassOf) if isinstance(p, URIRef))
    return {str(c) for c in found}
