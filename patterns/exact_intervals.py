"""Exact occurrence-interval adapter and evaluator; no general OWL or uncertain-time inference.

Run: python -m patterns.exact_intervals
The v2.4 point-anchor adapter, oracle, and schemas are independent of this profile.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

import rdflib
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
from pyshacl import validate as shacl_validate

from . import pro_solid as base
from .pro_solid import ContractError, EX, ROOT, S, datum, digest, one, require, role_binding, typed_objects

EI = Namespace('https://example.org/trajectory/interval/')
DATA = Namespace('https://example.org/trajectory/interval-data/')
PROFILE_ID = 'exact-interval-1.0'
POLICY = 'offset-datetime-microseconds-v1'
PROFILE = ROOT / 'ontology/exact-interval-profile.ttl'
SHAPES = ROOT / 'ontology/exact-interval-shapes.ttl'
KINDS = {'infusion': EI.Infusion, 'specimen_collection': EI.SpecimenCollection}
UNITS = {'second': (EI.Second, 1000000), 'minute': (EI.Minute, 60000000),
         'hour': (EI.Hour, 3600000000)}
META = {key: base.METADATA[key] for key in
        ('event_id', 'record_id', 'episode_id', 'source_key', 'source_hash', 'source_file', 'status')}
OBJECT_PROPERTIES = {S.hasParticipant, S.hasFeature, S.isFeatureOf, S.atTime,
                     S.hasDirectPart, S.refersTo}
MAX_US = 2**63 - 1


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def turtle_text(graph):
    """Emit explicit Turtle statements, retaining each literal's lexical form.

    Turtle's numeric shorthand may rewrite decimal '30' as '30.0'. Explicit
    quoted/datatype terms preserve source spellings and evidence identities.
    """
    return '\n'.join(sorted(' '.join(term.n3() for term in triple) + ' .' for triple in graph)) + '\n'


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= value.keys()
            and value.keys() <= set(required) | set(optional), 'SOURCE_FIELDS')


def text(value):
    require(isinstance(value, str) and bool(value.strip()), 'NONEMPTY_STRING_REQUIRED')
    return value


def identifier(value):
    require(isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]+', value)), 'INVALID_IDENTIFIER')
    return value


def checked_us(value):
    require(type(value) is int and -MAX_US - 1 <= value <= MAX_US, 'INTEGER_MICROSECONDS_REQUIRED')
    return value


def normalize(value, origin):
    try:
        return checked_us(base.offset_us(text(value), text(origin)))
    except OverflowError as error:
        raise ContractError('DATETIME_RANGE') from error


def duration_us(value, unit):
    require(unit in UNITS, 'UNSUPPORTED_DURATION_UNIT')
    require(isinstance(value, str) and bool(re.fullmatch(r'\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', value)),
            'DURATION_DECIMAL_REQUIRED')
    try:
        scaled = Fraction(Decimal(value)) * UNITS[unit][1]
    except (InvalidOperation, ValueError) as error:
        raise ContractError('DURATION_DECIMAL_REQUIRED') from error
    require(scaled > 0 and scaled.denominator == 1, 'DURATION_NOT_EXACT_MICROSECONDS')
    return checked_us(scaled.numerator)


def build_graph(source):
    """Construct named PRO/SOLID data from the closed synthetic source format."""
    fields(source, ('clocks', 'events'))
    require(isinstance(source['clocks'], list) and source['clocks'], 'CLOCKS_REQUIRED')
    require(isinstance(source['events'], list) and source['events'], 'EVENTS_REQUIRED')
    g = Graph()
    for prefix, ns in [('sulo', S), ('ex', EX), ('ei', EI), ('data', DATA)]:
        g.bind(prefix, ns)

    def info(parent, cls, value, name, datatype=XSD.string, relation=S.hasDirectPart):
        node = DATA[name]
        g.add((parent, relation, node))
        g.add((node, RDF.type, cls))
        g.add((node, S.hasValue, Literal(value, datatype=datatype, normalize=False)))
        return node

    def instant(parent, cls, value, name):
        node = info(parent, cls, value, name, XSD.dateTimeStamp)
        g.add((node, S.hasDirectPart, EI.Second))
        g.add((EI.Second, RDF.type, S.Unit))

    clocks = {}
    for clock in source['clocks']:
        fields(clock, ('clock_id', 'origin', 'scope', 'policy'))
        cid = identifier(clock['clock_id'])
        require(cid not in clocks, 'DUPLICATE_CLOCK_IDENTIFIER')
        clocks[cid] = clock
        frame = DATA['clocks/' + cid]
        g.add((frame, RDF.type, EI.TemporalReferenceSystem))
        for name, cls in [('clock_id', EI.ClockIdentifier), ('scope', EI.ClockScope), ('policy', EI.ClockPolicy)]:
            info(frame, cls, text(clock[name]), 'clocks/' + cid + '/' + name)
        instant(frame, EI.ClockOrigin, text(clock['origin']), 'clocks/' + cid + '/origin')

    seen, seen_records = set(), set()
    for row in source['events']:
        fields(row, ('event_id', 'patient_id', 'episode_id', 'record_id', 'event_kind', 'status',
                     'start', 'end', 'clock_id', 'source_key'), ('duration',))
        eid, pid, rid = (identifier(row[k]) for k in ('event_id', 'patient_id', 'record_id'))
        identifier(row['episode_id'])
        cid = identifier(row['clock_id'])
        require(eid not in seen and rid not in seen_records, 'DUPLICATE_SOURCE_IDENTIFIER')
        seen.add(eid); seen_records.add(rid)
        require(text(row['event_kind']) in KINDS, 'UNSUPPORTED_EVENT_KIND')
        require(row['status'] == 'performed', 'UNSUPPORTED_RECORD_STATUS')
        require(cid in clocks, 'UNKNOWN_CLOCK')
        path = 'events/' + eid
        process, patient = DATA[path], DATA['persons/' + pid]
        role, record, interval = DATA[path + '/patient-role'], DATA['records/' + rid], DATA[path + '/interval']
        g.add((patient, RDF.type, EX.Person))
        info(patient, EX.PatientIdentifier, pid, 'persons/' + pid + '/identifier', relation=S.hasFeature)
        g.add((process, RDF.type, KINDS[row['event_kind']]))
        g.add((process, S.hasParticipant, role))
        g.add((role, RDF.type, EX.PatientRole))
        g.add((role, S.isFeatureOf, patient))
        g.add((record, RDF.type, EX.SourceRecord))
        g.add((record, S.refersTo, process))
        meta = {**row, 'source_hash': digest(canonical(row)),
                'source_file': 'constructed://exact-interval/source-rows.json'}
        for key, cls in META.items():
            info(record, cls, text(meta[key]), 'records/' + rid + '/' + key)
        g.add((process, S.atTime, interval))
        g.add((interval, RDF.type, EI.ExactOccurrenceInterval))
        instant(interval, EI.ExactStartTime, text(row['start']), path + '/start')
        instant(interval, EI.ExactEndTime, text(row['end']), path + '/end')
        binding = DATA[path + '/clock-binding']
        g.add((interval, S.hasDirectPart, binding))
        g.add((binding, RDF.type, EI.ClockBinding))
        g.add((binding, S.refersTo, DATA['clocks/' + cid]))
        if 'duration' in row:
            duration = row['duration']
            fields(duration, ('value', 'unit'))
            duration_us(duration['value'], text(duration['unit']))
            node = info(interval, EI.ElapsedDuration, duration['value'], path + '/duration', XSD.decimal)
            unit = UNITS[duration['unit']][0]
            g.add((node, S.hasDirectPart, unit))
            g.add((unit, RDF.type, S.Unit))
    return g


def validate_graph(source):
    """Named subclass closure plus existing PRO derivation; explicit-data validation."""
    ont = base.ontology() + Graph().parse(PROFILE)
    classes = set(ont.subjects(RDF.type, OWL.Class))
    for subject, predicate, obj in source:
        require(isinstance(subject, URIRef), 'NAMED_INSTANCE_REQUIRED')
        if isinstance(obj, Literal):
            require(predicate == S.hasValue, 'SOLID_LITERAL_PROPERTY')
        else:
            require(isinstance(obj, URIRef), 'NAMED_INSTANCE_REQUIRED')
            require(predicate == RDF.type or predicate in OBJECT_PROPERTIES, 'UNSUPPORTED_OBJECT_PROPERTY')
            if predicate == RDF.type:
                require(obj in classes, 'UNSUPPORTED_INSTANCE_CLASS')
    g = Graph()
    for triple in source:
        g.add(triple)
    changed = True
    while changed:
        size = len(g)
        for node, _, cls in list(g.triples((None, RDF.type, None))):
            for parent in ont.objects(cls, RDFS.subClassOf):
                if isinstance(parent, URIRef):
                    g.add((node, RDF.type, parent))
        changed = size != len(g)
    g = base.materialize(g)
    groups = [(S.Object, S.Process), (S.SpatialObject, S.Feature),
              (S.Role, S.Quality, S.InformationObject, S.Capability),
              (S.Duration, S.TimeInstant, S.TimeInterval), (S.Time, S.Unit),
              (S.Collection, S.Quantity), (S.StartTime, S.EndTime),
              (EX.PatientRole, EX.CareProviderRole, EX.MeasurementResultRole, EX.AdministeredDrugRole)]
    for node in set(g.subjects(RDF.type, None)):
        types = set(g.objects(node, RDF.type))
        for group in groups:
            require(len(types.intersection(group)) <= 1, 'DISJOINT_PROFILE_CLASSES')
    before = rdflib.NORMALIZE_LITERALS
    try:
        conforms, _, report = shacl_validate(g, shacl_graph=Graph().parse(SHAPES),
                                             inference='none', do_owl_imports=False)
    finally:
        rdflib.NORMALIZE_LITERALS = before
    require(conforms, 'SHACL_INTERVAL_PROFILE_FAILURE:\n' + report)
    return g


def part(g, parent, cls):
    return one(typed_objects(g, parent, S.hasDirectPart, cls), 'PART_CARDINALITY:' + cls.split('/')[-1])


def scalar(g, node):
    return one(g.objects(node, S.hasValue), 'SCALAR_CARDINALITY')


@dataclass(frozen=True)
class Clock:
    resource: str
    clock_id: str
    origin: str
    scope: str
    policy: str


@dataclass(frozen=True)
class ExactInterval:
    event_id: str
    process: str
    patient_role: str
    patient_bearer: str
    patient_id: str
    episode_id: str
    start_us: int
    end_us: int
    clock: Clock
    context_id: str
    evidence_id: str

    def __post_init__(self):
        checked_us(self.start_us); checked_us(self.end_us)
        require(self.start_us < self.end_us, 'PROPER_INTERVAL_REQUIRED')


def project(source, manifest):
    """Return immutable interval records and evidence; never coerce into point DTOs."""
    fields(manifest, ('profile', 'dataset_id', 'snapshot_id'))
    require(manifest['profile'] == PROFILE_ID, 'UNSUPPORTED_PROFILE')
    text(manifest['dataset_id']); text(manifest['snapshot_id'])
    g = validate_graph(source)
    processes = set(g.subjects(RDF.type, EI.IntervalProcess))
    require(processes and processes == set(g.subjects(RDF.type, S.Process)), 'EMPTY_OR_MIXED_PROFILE')
    files = ('patterns/pro_solid.py', 'patterns/exact_intervals.py', 'patterns/requirements.lock.txt', 'ontology/pro-solid-profile.ttl',
             'ontology/exact-interval-profile.ttl', 'ontology/exact-interval-shapes.ttl',
             'ontology/vendor/sulo-0.2.14.ttl')
    graph_hash = digest(turtle_text(source))
    context = {**manifest, 'source_graph_sha256': graph_hash, 'normalization_policy': POLICY,
               'artifacts': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}}
    context_id = digest(canonical(context))
    clocks, clock_ids = {}, set()
    for frame in sorted(g.subjects(RDF.type, EI.TemporalReferenceSystem)):
        cid = identifier(datum(g, frame, EI.ClockIdentifier, S.hasDirectPart))
        require(cid not in clock_ids, 'DUPLICATE_CLOCK_IDENTIFIER')
        clock_ids.add(cid)
        policy = datum(g, frame, EI.ClockPolicy, S.hasDirectPart)
        scope = datum(g, frame, EI.ClockScope, S.hasDirectPart)
        require(policy == POLICY, 'UNSUPPORTED_CLOCK_POLICY')
        require(scope == 'global' or bool(re.fullmatch(r'patient:[A-Za-z0-9_-]+', scope)), 'UNSUPPORTED_CLOCK_SCOPE')
        origin = str(scalar(g, part(g, frame, EI.ClockOrigin)))
        normalize(origin, origin)
        clocks[frame] = Clock(str(frame), cid, origin, scope, policy)

    output, bindings = [], {}
    used_roles, used_records, used_descriptors, event_ids, record_ids = (set() for _ in range(5))
    patients = {}
    for process in sorted(processes):
        role, patient = role_binding(g, process, EX.PatientRole)
        require(role not in used_roles, 'ROLE_REUSED_ACROSS_PROCESSES')
        used_roles.add(role)
        pid = identifier(datum(g, patient, EX.PatientIdentifier, S.hasFeature))
        require(pid not in patients or patients[pid] == patient, 'PATIENT_IDENTIFIER_COLLISION')
        patients[pid] = patient
        record = one((r for r in g.subjects(S.refersTo, process) if (r, RDF.type, EX.SourceRecord) in g),
                     'SOURCE_RECORD_CARDINALITY')
        require(record not in used_records, 'REUSED_SOURCE_RECORD')
        used_records.add(record)
        meta = {key: datum(g, record, cls, S.hasDirectPart) for key, cls in META.items()}
        for key in ('event_id', 'record_id', 'episode_id'):
            identifier(meta[key])
        require(meta['event_id'] not in event_ids and meta['record_id'] not in record_ids, 'DUPLICATE_SOURCE_IDENTIFIER')
        event_ids.add(meta['event_id']); record_ids.add(meta['record_id'])
        require(meta['status'] == 'performed', 'UNSUPPORTED_RECORD_STATUS')
        require(bool(re.fullmatch('[0-9a-f]{64}', meta['source_hash'])), 'INVALID_SOURCE_HASH')
        interval = one(g.objects(process, S.atTime), 'INTERVAL_CARDINALITY')
        start, end = part(g, interval, S.StartTime), part(g, interval, S.EndTime)
        descriptors = {interval, start, end}
        require(len(descriptors) == 3 and not descriptors & used_descriptors, 'REUSED_TEMPORAL_DESCRIPTOR')
        used_descriptors.update(descriptors)
        clock_binding = part(g, interval, EI.ClockBinding)
        frame = one(g.objects(clock_binding, S.refersTo), 'CLOCK_REFERENCE_CARDINALITY')
        clock = clocks[frame]
        require(clock.scope in ('global', 'patient:' + pid), 'CLOCK_PATIENT_SCOPE_MISMATCH')
        start_text, end_text = str(scalar(g, start)), str(scalar(g, end))
        s, e = normalize(start_text, clock.origin), normalize(end_text, clock.origin)
        require(s < e, 'PROPER_INTERVAL_REQUIRED')
        elapsed = checked_us(e - s)
        duration = None
        durations = typed_objects(g, interval, S.hasDirectPart, S.Duration)
        if durations:
            node = one(durations, 'DURATION_CARDINALITY')
            unit = one(g.objects(node, S.hasDirectPart), 'DURATION_UNIT_CARDINALITY')
            unit_name = one((name for name, (resource, _) in UNITS.items() if resource == unit), 'UNSUPPORTED_DURATION_UNIT')
            value = str(scalar(g, node))
            require(duration_us(value, unit_name) == elapsed, 'DURATION_DISAGREEMENT')
            duration = {'datum': str(node), 'value': value, 'unit': str(unit), 'normalized_us': elapsed}
        evidence = {'context_id': context_id, 'process': str(process), 'patient_role': str(role),
                    'patient_bearer': str(patient), 'patient_id': pid, 'source_record': str(record),
                    'source_metadata': meta, 'interval': str(interval), 'clock_binding': str(clock_binding),
                    'clock': asdict(clock), 'duration_us': elapsed, 'recorded_duration': duration,
                    'start': {'datum': str(start), 'value': start_text, 'unit': str(EI.Second), 'normalized_us': s},
                    'end': {'datum': str(end), 'value': end_text, 'unit': str(EI.Second), 'normalized_us': e}}
        evidence_id = PROFILE_ID + ':' + digest(canonical(evidence))
        bindings[evidence_id] = evidence
        output.append(ExactInterval(meta['event_id'], str(process), str(role), str(patient), pid,
                                    meta['episode_id'], s, e, clock, context_id, evidence_id))
    require(used_records == set(g.subjects(RDF.type, EX.SourceRecord)), 'UNUSED_SOURCE_RECORD')
    return output, {'context_id': context_id, 'context': context, 'bindings': bindings}


def allen_relation(a, b):
    """One of the 13 basic Allen relations, for exact proper integer intervals."""
    s, e = a
    u, v = b
    for value in (s, e, u, v):
        checked_us(value)
    require(s < e and u < v, 'PROPER_INTERVAL_REQUIRED')
    if e < u: return 'before'
    if e == u: return 'meets'
    if v < s: return 'after'
    if v == s: return 'met_by'
    if s == u:
        return 'equals' if e == v else ('starts' if e < v else 'started_by')
    if e == v: return 'finishes' if s > u else 'finished_by'
    if s < u: return 'overlaps' if e < v else 'contains'
    return 'overlapped_by' if e > v else 'during'


def evaluate(left, right, operator, *, min_gap_us=None, max_gap_us=None):
    """Evaluate one same-patient/episode exact constraint, with explicit comparability."""
    require(operator in ('before', 'meets', 'overlaps', 'gap'), 'UNSUPPORTED_TEMPORAL_OPERATOR')
    if operator == 'gap':
        checked_us(min_gap_us); checked_us(max_gap_us)
        require(0 <= min_gap_us <= max_gap_us, 'INVALID_GAP_BOUNDS')
    else:
        require(min_gap_us is None and max_gap_us is None, 'UNEXPECTED_GAP_BOUNDS')
    result = {'operator': operator, 'left_event': left.event_id, 'right_event': right.event_id,
              'evidence_ids': [left.evidence_id, right.evidence_id],
              'context_ids': [left.context_id, right.context_id],
              'endpoints_us': {'left_start': left.start_us, 'left_end': left.end_us,
                               'right_start': right.start_us, 'right_end': right.end_us},
              'gap_bounds_us': [min_gap_us, max_gap_us] if operator == 'gap' else None}
    reasons = []
    if left.context_id != right.context_id: reasons.append('CONTEXT_MISMATCH')
    if (left.patient_bearer, left.patient_id, left.episode_id) != (right.patient_bearer, right.patient_id, right.episode_id):
        reasons.append('TRAJECTORY_SCOPE_MISMATCH')
    if left.clock != right.clock: reasons.append('CLOCK_MISMATCH')
    if reasons:
        return {**result, 'status': 'INCOMPARABLE', 'reason_codes': reasons, 'relation': None, 'signed_gap_us': None}
    relation = allen_relation((left.start_us, left.end_us), (right.start_us, right.end_us))
    gap = checked_us(right.start_us - left.end_us)
    satisfied = min_gap_us <= gap <= max_gap_us if operator == 'gap' else relation == operator
    rule = 'min_gap_us <= right.start_us - left.end_us <= max_gap_us' if operator == 'gap' else {
        'before': 'left.end_us < right.start_us', 'meets': 'left.end_us == right.start_us',
        'overlaps': 'left.start_us < right.start_us < left.end_us < right.end_us'}[operator]
    return {**result, 'status': 'SATISFIED' if satisfied else 'NOT_SATISFIED', 'reason_codes': [],
            'relation': relation, 'signed_gap_us': gap, 'rule': rule}


def run_queries(intervals, queries):
    require(isinstance(queries, list), 'QUERIES_ARRAY_REQUIRED')
    by_id = {item.event_id: item for item in intervals}
    results, ids = [], set()
    for query in queries:
        fields(query, ('id', 'left_event', 'right_event', 'operator'), ('min_gap_us', 'max_gap_us'))
        qid = identifier(query['id'])
        require(qid not in ids, 'DUPLICATE_QUERY_IDENTIFIER')
        ids.add(qid)
        left, right = identifier(query['left_event']), identifier(query['right_event'])
        require(left in by_id and right in by_id, 'UNKNOWN_EVENT_IDENTIFIER')
        result = evaluate(by_id[left], by_id[right], text(query['operator']),
                          min_gap_us=query.get('min_gap_us'), max_gap_us=query.get('max_gap_us'))
        results.append({'id': qid, **result})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    example = ROOT / 'examples/exact-interval'
    parser.add_argument('--source', type=Path, default=example / 'source-rows.json')
    parser.add_argument('--manifest', type=Path, default=example / 'manifest.json')
    parser.add_argument('--queries', type=Path, default=example / 'queries.json')
    parser.add_argument('--graph', type=Path, help='Read existing Turtle instead of constructing synthetic rows')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/exact-interval-run')
    args = parser.parse_args()
    try:
        graph = Graph().parse(args.graph, format='turtle') if args.graph else build_graph(json.loads(args.source.read_text()))
        intervals, evidence = project(graph, json.loads(args.manifest.read_text()))
        results = run_queries(intervals, json.loads(args.queries.read_text()))
    except ContractError as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'graph.ttl').write_text(turtle_text(graph))
    for name, value in [('intervals.json', [asdict(x) for x in intervals]), ('evidence.json', evidence),
                        ('comparisons.json', results)]:
        (args.output / name).write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps({'profile': PROFILE_ID, 'intervals': len(intervals), 'comparisons': len(results),
                      'statuses': {r['id']: r['status'] for r in results}, 'output': str(args.output)}))


if __name__ == '__main__':
    main()
