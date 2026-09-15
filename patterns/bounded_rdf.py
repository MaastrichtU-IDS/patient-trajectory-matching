"""Closed bounded RDF ingestion, retaining source terms and PRO witnesses.

Arbitrary named instance IRIs are supported; vocabulary and structural shape are
bounded. Every input triple must be consumed. No general OWL or identity inference.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD
from rdflib.exceptions import ParserError
from rdflib.plugins.parsers.notation3 import BadSyntax

from . import bounded_intervals as bt
from . import bounded_cohort as cohort
from . import exact_intervals as ei

BR = Namespace('https://example.org/trajectory/bounded-rdf/')
PROFILE_ID = 'bounded-rdf-1.0'
PROFILE = ei.ROOT / 'ontology/bounded-rdf-profile.ttl'


def export_source(source):
    """Export valid bounded JSON with explicit identifiers needed for RDF loading."""
    snapshot = bt.prepare(source)
    return _export_snapshot(snapshot, PROFILE_ID)


def _export_snapshot(snapshot, profile_id):
    graph = snapshot.graph

    def identifier(parent, cls, value):
        node = URIRef(str(parent) + '/rdf-identifier')
        graph.add((parent, ei.S.hasDirectPart, node))
        graph.add((node, RDF.type, cls))
        graph.add((node, ei.S.hasValue, Literal(value, datatype=XSD.string, normalize=False)))

    identifier(bt.D.snapshot, BR.ProfileIdentifier, profile_id)
    for vid in snapshot.variables:
        identifier(bt.D['variables/' + vid], BR.VariableIdentifier, vid)
    for row in snapshot.source['constraints']:
        identifier(bt.D['constraints/' + row['id']], BR.ConstraintIdentifier, row['id'])
    return graph


class Reader:
    def __init__(self, graph, extra_ontologies=()):
        self.graph, self.consumed, self.claimed = graph, set(), set()
        self.ontology = (ei.base.ontology() + Graph().parse(ei.PROFILE)
                         + Graph().parse(ei.ROOT / 'ontology/bounded-interval-profile.ttl') + Graph().parse(PROFILE))
        for path in extra_ontologies:
            self.ontology += Graph().parse(path)
        self.ancestors = {}
        for s, p, o in graph:
            ei.require(isinstance(s, URIRef) and isinstance(p, URIRef) and isinstance(o, (URIRef, Literal)), 'NAMED_RDF_REQUIRED')
            ei.require(not isinstance(o, Literal) or p == ei.S.hasValue, 'SOLID_LITERAL_PROPERTY')

    def parents(self, cls):
        if cls not in self.ancestors:
            found, pending = set(), [cls]
            while pending:
                current = pending.pop()
                if current in found: continue
                found.add(current)
                pending.extend(p for p in self.ontology.objects(current, RDFS.subClassOf) if isinstance(p, URIRef))
            self.ancestors[cls] = found
        return self.ancestors[cls]

    def is_type(self, node, cls):
        return any(cls in self.parents(t) for t in self.graph.objects(node, RDF.type))

    def nodes(self, cls):
        return sorted({s for s in self.graph.subjects(RDF.type, None) if self.is_type(s, cls)})

    def claim(self, node, cls, *, shared=False):
        ei.require(shared or node not in self.claimed, 'REUSED_DESCRIPTOR_OR_WITNESS')
        types = set(self.graph.objects(node, RDF.type))
        ei.require(self.is_type(node, cls) and types <= self.parents(cls), 'UNSUPPORTED_OR_CONFLICTING_TYPES:' + str(cls))
        self.claimed.add(node)
        self.consumed.update((node, RDF.type, t) for t in types)
        return node

    def objects(self, subject, predicate):
        values = set(self.graph.objects(subject, predicate))
        self.consumed.update((subject, predicate, value) for value in values)
        return values

    def one(self, subject, predicate):
        return ei.one(self.objects(subject, predicate), 'RDF_CARDINALITY:' + str(predicate))

    def part(self, parent, cls, relation=ei.S.hasDirectPart):
        node = ei.one((n for n in self.graph.objects(parent, relation) if self.is_type(n, cls)), 'RDF_PART_CARDINALITY:' + str(cls))
        self.consumed.add((parent, relation, node))
        return self.claim(node, cls)

    def value(self, parent, cls, *, datatype=XSD.string, unit=None, relation=ei.S.hasDirectPart):
        node = self.part(parent, cls, relation)
        value = self.one(node, ei.S.hasValue)
        ei.require(isinstance(value, Literal) and value.datatype == datatype and value.language is None, 'RDF_DATATYPE_MISMATCH')
        if unit is not None:
            ei.require(self.one(node, ei.S.hasDirectPart) == unit, 'UNSUPPORTED_UNIT')
            self.claim(unit, ei.S.Unit, shared=True)
        if datatype == XSD.integer:
            ei.require(bool(re.fullmatch(r'[+-]?[0-9]+', str(value))), 'INVALID_INTEGER_LITERAL')
            result = ei.checked_us(int(str(value)))
        else:
            result = ei.text(str(value))
        return result, {'datum': str(node), 'lexical': str(value), 'datatype': str(datatype), 'unit': str(unit) if unit else None}

    def string(self, parent, cls, relation=ei.S.hasDirectPart):
        return self.value(parent, cls, relation=relation)[0]

    def provenance(self, node):
        key = self.value(node, ei.EX.SourceKey)
        digest = self.value(node, ei.EX.SourceHash)
        location = self.value(node, ei.EX.SourceLocation)
        ei.require(bool(re.fullmatch(r'[0-9a-f]{64}', digest[0])), 'INVALID_SOURCE_HASH')
        return {'source_key': key[0], 'source_hash': digest[0], 'source_location': location[0],
                'descriptors': {'source_key': key[1], 'source_hash': digest[1], 'source_location': location[1]},
                'hash_verification': 'preserved_declaration_original_rows_not_available'}


def prepare_graph(input_graph):
    return _prepare_graph(input_graph)


def _prepare_graph(input_graph, *, profile_id=PROFILE_ID, source_profile=bt.PROFILE_ID,
                   origin_datatype=XSD.dateTimeStamp, compiler=bt.compile_source,
                   read_clock=None, read_variable=None, extra_ontologies=(), extra_files=(),
                   time_domain='bounded-integer-microseconds'):
    graph = Graph()
    for triple in input_graph: graph.add(triple)
    r = Reader(graph, extra_ontologies)
    snapshot_node = r.claim(ei.one(r.nodes(bt.BT.Snapshot), 'SNAPSHOT_CARDINALITY'), bt.BT.Snapshot)
    ei.require(r.string(snapshot_node, BR.ProfileIdentifier) == profile_id, 'UNSUPPORTED_RDF_PROFILE')
    source = {'profile': source_profile, 'dataset_id': r.string(snapshot_node, bt.BT.DatasetIdentifier),
              'snapshot_id': r.string(snapshot_node, bt.BT.SnapshotIdentifier),
              'clocks': [], 'variables': [], 'events': [], 'constraints': []}
    clock_ids, variable_ids, variable_evidence, constraint_evidence = {}, {}, {}, {}
    for node in r.nodes(ei.EI.TemporalReferenceSystem):
        r.claim(node, ei.EI.TemporalReferenceSystem)
        clock_id = r.string(node, ei.EI.ClockIdentifier)
        ei.require(clock_id not in clock_ids.values(), 'DUPLICATE_CLOCK_IDENTIFIER')
        clock_ids[node] = clock_id
        source['clocks'].append({'clock_id': clock_id, 'origin': r.value(node, ei.EI.ClockOrigin, datatype=origin_datatype, unit=ei.EI.Second)[0],
                                 'scope': r.string(node, ei.EI.ClockScope), 'policy': r.string(node, ei.EI.ClockPolicy),
                                 **(read_clock(r, node) if read_clock else {})})
    for node in r.nodes(bt.BT.TemporalVariable):
        r.claim(node, bt.BT.TemporalVariable)
        vid = r.string(node, BR.VariableIdentifier)
        ei.require(vid not in variable_ids.values(), 'DUPLICATE_VARIABLE_IDENTIFIER')
        variable_ids[node] = vid
        lower = r.value(node, bt.BT.LowerBound, datatype=XSD.integer, unit=bt.BT.Microsecond)
        upper = r.value(node, bt.BT.UpperBound, datatype=XSD.integer, unit=bt.BT.Microsecond)
        binding = r.part(node, ei.EI.ClockBinding)
        clock = r.one(binding, ei.S.refersTo)
        ei.require(clock in clock_ids, 'UNKNOWN_CLOCK_REFERENCE')
        provenance = r.provenance(node)
        source['variables'].append({'id': vid, 'lower_us': lower[0], 'upper_us': upper[0],
                                    'patient_id': r.string(node, ei.EX.PatientIdentifier), 'episode_id': r.string(node, ei.EX.EpisodeIdentifier),
                                    'clock_id': clock_ids[clock], 'source_key': provenance['source_key'],
                                    **(read_variable(r, node) if read_variable else {})})
        variable_evidence[vid] = {'variable': str(node), 'lower': lower[1], 'upper': upper[1],
                                  'clock_binding': str(binding), 'clock': str(clock), 'provenance': provenance}
    for node in r.nodes(bt.BT.DifferenceConstraint):
        r.claim(node, bt.BT.DifferenceConstraint)
        cid = r.string(node, BR.ConstraintIdentifier)
        ei.require(cid not in constraint_evidence, 'DUPLICATE_CONSTRAINT_IDENTIFIER')
        ei.require(r.string(node, bt.BT.OperatorDatum) == 'left_minus_right_le', 'UNSUPPORTED_SOURCE_OPERATOR')
        operands, bindings = {}, {}
        for name, cls in [('left_var', bt.BT.LeftOperandBinding), ('right_var', bt.BT.RightOperandBinding)]:
            binding = r.part(node, cls)
            target = r.one(binding, ei.S.refersTo)
            ei.require(target in variable_ids, 'UNKNOWN_VARIABLE_REFERENCE')
            operands[name], bindings[name] = variable_ids[target], str(binding)
        upper = r.value(node, bt.BT.UpperBound, datatype=XSD.integer, unit=bt.BT.Microsecond)
        provenance = r.provenance(node)
        source['constraints'].append({'id': cid, **operands, 'upper_us': upper[0], 'source_key': provenance['source_key']})
        constraint_evidence[cid] = {'constraint': str(node), 'operand_bindings': bindings, 'upper': upper[1], 'provenance': provenance}
    ei.require(r.objects(snapshot_node, ei.S.hasDirectPart) ==
               set(r.nodes(bt.BT.TemporalVariable)) | set(r.nodes(bt.BT.DifferenceConstraint)) |
               {n for n in graph.objects(snapshot_node, ei.S.hasDirectPart)
                if any(r.is_type(n, c) for c in (BR.ProfileIdentifier, bt.BT.DatasetIdentifier, bt.BT.SnapshotIdentifier))},
               'SNAPSHOT_MEMBERSHIP')
    # Requiring equality also catches variables/constraints omitted from snapshot membership.
    expected_members = set(r.nodes(bt.BT.TemporalVariable)) | set(r.nodes(bt.BT.DifferenceConstraint))
    ei.require(expected_members <= set(graph.objects(snapshot_node, ei.S.hasDirectPart)), 'SNAPSHOT_MEMBERSHIP')
    evidence, people, patient_ids = {}, {}, {}
    for process in r.nodes(ei.S.Process):
        kind = ei.one((k for k, cls in bt.KINDS.items() if r.is_type(process, cls)), 'SUPPORTED_PROCESS_KIND_REQUIRED')
        r.claim(process, bt.KINDS[kind])
        role = r.one(process, ei.S.hasParticipant)
        r.claim(role, ei.EX.PatientRole)
        bearers = r.objects(role, ei.S.isFeatureOf) | set(graph.subjects(ei.S.hasFeature, role))
        person = ei.one(bearers, 'PATIENT_BEARER_CARDINALITY')
        if (person, ei.S.hasFeature, role) in graph: r.consumed.add((person, ei.S.hasFeature, role))
        if person not in people:
            r.claim(person, ei.EX.Person)
            pid = r.string(person, ei.EX.PatientIdentifier, ei.S.hasFeature)
            ei.require(pid not in patient_ids, 'PATIENT_IDENTIFIER_COLLISION')
            people[person], patient_ids[pid] = pid, person
        record = ei.one(graph.subjects(ei.S.refersTo, process), 'SOURCE_RECORD_CARDINALITY')
        r.claim(record, ei.EX.SourceRecord)
        ei.require(r.one(record, ei.S.refersTo) == process, 'SOURCE_RECORD_REFERENCE')
        eid = r.string(record, ei.EX.EventIdentifier)
        ei.require(eid not in evidence, 'DUPLICATE_EVENT_IDENTIFIER')
        interval = r.one(process, ei.S.atTime)
        r.claim(interval, bt.BT.OccurrenceInterval)
        endpoints, descriptors = {}, {}
        for name, cls in [('start', bt.BT.StartDescriptor), ('end', bt.BT.EndDescriptor)]:
            descriptor = r.part(interval, cls)
            variable = r.one(descriptor, ei.S.refersTo)
            ei.require(variable in variable_ids, 'UNKNOWN_VARIABLE_REFERENCE')
            endpoints[name + '_var'], descriptors[name + '_descriptor'] = variable_ids[variable], str(descriptor)
        provenance = r.provenance(record)
        row = {'id': eid, 'record_id': r.string(record, ei.EX.RecordIdentifier), 'patient_id': people[person],
               'episode_id': r.string(record, ei.EX.EpisodeIdentifier), 'status': r.string(record, ei.EX.RecordStatus),
               'event_kind': kind, **endpoints, 'source_key': provenance['source_key']}
        source['events'].append(row)
        evidence[eid] = {'process': str(process), 'patient_role': str(role), 'patient_bearer': str(person),
                         'interval': str(interval), **descriptors, 'source_record': str(record),
                         'source': row, 'source_hash': provenance['source_hash'], 'provenance': provenance,
                         'start_variable': variable_evidence[endpoints['start_var']]['variable'],
                         'end_variable': variable_evidence[endpoints['end_var']]['variable']}
    unused = set(graph) - r.consumed
    ei.require(not unused, 'UNCONSUMED_RDF_TRIPLES: ' + str(len(unused)))
    for name in ('clocks', 'variables', 'events', 'constraints'):
        source[name].sort(key=lambda row: row.get('id', row.get('clock_id')))
    def attach_rdf(items):
        for item in items.values():
            if item['kind'] == 'variable_bound': item['rdf'] = variable_evidence[item['variable']]
            elif item['kind'] == 'source_constraint': item['rdf'] = constraint_evidence[item['source']['id']]
            else: item['rdf'] = evidence[item['event_id']]

    try:
        source, events, variables, networks, edge_evidence = compiler(source)
    except bt.InconsistentSource as error:
        attach_rdf(error.details['edge_evidence'])
        error.details['rdf_context'] = {'profile': profile_id, 'graph_sha256': ei.digest(ei.turtle_text(graph)),
                                        'dataset_id': source['dataset_id'], 'snapshot_id': source['snapshot_id']}
        error.args = ('INCONSISTENT_SOURCE: ' + ei.canonical(error.details),)
        raise
    attach_rdf(edge_evidence)
    files = ('patterns/bounded_rdf.py', 'ontology/bounded-rdf-profile.ttl', 'patterns/bounded_intervals.py',
             'patterns/temporal_stn.py', 'schemas/bounded-interval.schema.json', 'ontology/bounded-interval-profile.ttl',
             'ontology/exact-interval-profile.ttl', 'ontology/pro-solid-profile.ttl', 'ontology/vendor/sulo-0.2.14.ttl',
             'patterns/exact_intervals.py', 'patterns/pro_solid.py', 'patterns/requirements.lock.txt')
    context = {'profile': profile_id, 'dataset_id': source['dataset_id'], 'snapshot_id': source['snapshot_id'],
               'graph_sha256': ei.digest(ei.turtle_text(graph)), 'projection_sha256': ei.digest(ei.canonical(source)),
               'time_domain': time_domain, 'source_hash_policy': 'preserve_declared_hashes',
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in files + extra_files}}
    context_id = ei.digest(ei.canonical(context))
    for item in evidence.values():
        item['context_id'] = context_id
        item['evidence_id'] = profile_id + ':' + ei.digest(ei.canonical(item))
    return bt.Snapshot(source, graph, events, variables, networks, edge_evidence, evidence, context, context_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--source', type=Path)
    inputs.add_argument('--graph', type=Path)
    parser.add_argument('--query', type=Path, default=ei.ROOT / 'examples/bounded-interval/query.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/bounded-rdf-run')
    args = parser.parse_args()
    try:
        query = bt.validate(json.loads(args.query.read_text()), query=True)
        graph = (Graph().parse(args.graph, format='turtle') if args.graph else
                 export_source(json.loads((args.source or ei.ROOT / 'examples/bounded-interval/source.json').read_text())))
        snapshot = prepare_graph(graph)
        result = cohort.execute(snapshot, query)
    except bt.InconsistentSource as error:
        parser.exit(2, json.dumps(error.details) + '\n')
    except (ei.ContractError, OSError, UnicodeError, json.JSONDecodeError, ParserError, BadSyntax) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'graph.ttl').write_text(ei.turtle_text(snapshot.graph))
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'profile': PROFILE_ID, 'certain_patient_ids': result['certain_patient_ids'],
                      'possible_patient_ids': result['possible_patient_ids'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
