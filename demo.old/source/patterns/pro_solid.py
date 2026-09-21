"""PRO/SOLID source-row -> RDF -> matcher projection, profile 2.4.

This is a closed-world adapter contract, not a complete SULO/OWL reasoner.
Only named subclass closure, hasFeature inverse, and the PRO chain are executed.
Run from the pack root: python -m patterns.pro_solid
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import rdflib
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD
from pyshacl import validate as shacl_validate

# Dedicated runner configuration: retain source literal spelling when parsing RDF.
# In a server, isolate this loader or configure the RDF library equivalently at startup.
rdflib.NORMALIZE_LITERALS = False

ROOT = Path(__file__).resolve().parents[1]
S = Namespace('https://w3id.org/sulo/')
EX = Namespace('https://example.org/trajectory/toy/')
DATA = Namespace('https://example.org/trajectory/data/')
PROFILE = ROOT / 'ontology/pro-solid-profile.ttl'
PIN = ROOT / 'ontology/vendor/sulo-0.2.14.ttl'
UNIT_MAP = {EX.MilligramPerDecilitre: ('mg/dL', Decimal('1')),
            EX.MilligramPerLitre: ('mg/L', Decimal('0.1'))}
UNIT_NAMES = {'mg/dL': EX.MilligramPerDecilitre, 'mg/L': EX.MilligramPerLitre}
DRUGS = {'ex:DrugA': EX.DrugA, 'ex:DrugAChild': EX.DrugAChild, 'ex:DrugB': EX.DrugB}
OBJECT_PROPERTIES = {S.hasParticipant, S.isFeatureOf, S.hasFeature, S.hasPart,
                     S.refersTo, S.atTime}
METADATA = {'event_id': EX.EventIdentifier, 'record_id': EX.RecordIdentifier,
            'episode_id': EX.EpisodeIdentifier, 'source_key': EX.SourceKey,
            'source_file': EX.SourceLocation, 'source_hash': EX.SourceHash,
            'status': EX.RecordStatus, 'source_code': EX.SourceCode}


class ContractError(ValueError):
    """A failure of the declared adapter profile; not clinical non-membership."""


def require(condition, code):
    if not condition:
        raise ContractError(code)


def one(values, code):
    values = set(values)
    require(len(values) == 1, code)
    return next(iter(values))


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def decimal_text(value):
    return format(value, 'f')


def instant(text):
    # Deliberately reject unresolved offsets, local dates and sub-microsecond times.
    require(bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
                              r'(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', text)),
            'UNSUPPORTED_OR_UNZONED_TIME')
    require(not text.endswith('-00:00'), 'UNKNOWN_TIMEZONE_OFFSET')
    if not text.endswith('Z'):
        hours, minutes = map(int, text[-5:].split(':'))
        require(minutes < 60 and (hours < 14 or (hours == 14 and minutes == 0)),
                'INVALID_TIMEZONE_OFFSET')
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError as error:
        raise ContractError('INVALID_DATETIME') from error


def offset_us(value, origin):
    delta = instant(value) - instant(origin)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def ontology():
    pin = json.loads((ROOT / 'ontology/sulo-pin.json').read_text())
    require(hashlib.sha256(PIN.read_bytes()).hexdigest() == pin['sha256'], 'SULO_PIN_MISMATCH')
    return Graph().parse(PIN).parse(PROFILE)


def materialize(source):
    """Return separate limited closure. No existential witnesses or OWL consistency check."""
    ont = ontology()
    g = Graph()
    for triple in source:
        g.add(triple)
    changed = True
    while changed:
        before = len(g)
        for entity, _, cls in list(g.triples((None, RDF.type, None))):
            for parent in ont.objects(cls, RDFS.subClassOf):
                if isinstance(parent, URIRef):
                    g.add((entity, RDF.type, parent))
        for bearer, _, feature in list(g.triples((None, S.hasFeature, None))):
            g.add((feature, S.isFeatureOf, bearer))
        for feature, _, bearer in list(g.triples((None, S.isFeatureOf, None))):
            g.add((bearer, S.hasFeature, feature))
        for process, _, participant in list(g.triples((None, S.hasParticipant, None))):
            for bearer in g.objects(participant, S.isFeatureOf):
                g.add((process, S.hasParticipant, bearer))
        changed = len(g) != before
    return g


def typed_objects(g, subject, predicate, cls):
    return {o for o in g.objects(subject, predicate) if (o, RDF.type, cls) in g}


def role_binding(g, process, role_class):
    role = one(typed_objects(g, process, S.hasParticipant, role_class),
               'ROLE_CARDINALITY:' + str(role_class).split('/')[-1])
    bearer = one(g.objects(role, S.isFeatureOf), 'ROLE_BEARER_CARDINALITY')
    return role, bearer


def datum(g, parent, cls, relation=S.hasPart):
    node = one(typed_objects(g, parent, relation, cls), 'DATUM_CARDINALITY:' + cls.split('/')[-1])
    value = one(g.objects(node, S.hasValue), 'DATUM_VALUE_CARDINALITY')
    require(isinstance(value, Literal), 'NON_LITERAL_VALUE')
    require(value.datatype in (None, XSD.string) and value.language is None and str(value) != '',
            'METADATA_STRING_REQUIRED')
    return str(value)


def build_graph(rows):
    """Adapter for the supplied normalized synthetic JSON rows, not MIMIC raw tables."""
    g = Graph()
    for prefix, ns in [('sulo', S), ('ex', EX), ('data', DATA)]:
        g.bind(prefix, ns)
    seen = set()

    def info(parent, cls, value, name, relation=S.hasPart):
        node = DATA[name]
        g.add((parent, relation, node))
        g.add((node, RDF.type, cls))
        g.add((node, S.hasValue, Literal(value, datatype=XSD.string, normalize=False)))
        return node

    for row in rows:
        eid, pid = row['event_id'], row['patient_id']
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]+', eid)), 'INVALID_EVENT_IDENTIFIER')
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]+', pid)), 'INVALID_PATIENT_IDENTIFIER')
        require(eid not in seen, 'DUPLICATE_EVENT_IDENTIFIER')
        seen.add(eid)
        require(row['event_kind'] in ('measurement', 'administration'), 'UNSUPPORTED_EVENT_KIND')
        require(row['status'] == 'performed', 'UNSUPPORTED_RECORD_STATUS')
        process, patient = DATA['event-' + eid], DATA['person-' + pid]
        record, pr = DATA['record-' + eid], DATA[eid + '-patient-role']
        g.add((patient, RDF.type, EX.Person))
        info(patient, EX.PatientIdentifier, pid, pid + '-identifier', S.hasFeature)
        process_class = EX.Measurement if row['event_kind'] == 'measurement' else EX.DrugAdministration
        g.add((process, RDF.type, process_class))
        g.add((process, S.hasParticipant, pr))
        g.add((pr, RDF.type, EX.PatientRole))
        g.add((pr, S.isFeatureOf, patient))
        g.add((record, RDF.type, EX.SourceRecord))
        g.add((record, S.refersTo, process))
        meta = {**row, 'source_file': 'constructed://pro-solid/source-rows.json',
                'source_key': eid, 'source_hash': digest(json.dumps(row, sort_keys=True, separators=(',', ':')))}
        for name, cls in METADATA.items():
            info(record, cls, meta[name], eid + '-' + name)
        t = DATA[eid + '-time']
        instant(row['datetime'])
        g.add((process, S.atTime, t))
        g.add((t, RDF.type, EX.RecordedAnchorTime))
        g.add((t, S.hasValue, Literal(row['datetime'], datatype=XSD.dateTimeStamp, normalize=False)))
        g.add((t, S.hasPart, EX.Second))
        g.add((EX.Second, RDF.type, S.Unit))
        if row['event_kind'] == 'measurement':
            require(row['source_code'] == 'ex:Creatinine', 'UNSUPPORTED_MEASUREMENT_CODE')
            require(row['unit'] in UNIT_NAMES, 'UNSUPPORTED_UNIT')
            q, quality, rr = DATA[eid + '-result'], DATA[eid + '-quality'], DATA[eid + '-result-role']
            g.add((process, S.hasParticipant, rr))
            g.add((rr, RDF.type, EX.MeasurementResultRole))
            g.add((rr, S.isFeatureOf, q))
            g.add((q, RDF.type, EX.CreatinineResult))
            g.add((q, S.hasValue, Literal(row['value'], datatype=XSD.decimal, normalize=False)))
            g.add((q, S.refersTo, quality))
            g.add((quality, RDF.type, EX.CreatinineConcentration))
            g.add((quality, S.isFeatureOf, patient))
            unit = UNIT_NAMES[row['unit']]
            g.add((q, S.hasPart, unit))
            g.add((unit, RDF.type, S.Unit))
        else:
            require(row['source_code'] in DRUGS, 'UNSUPPORTED_DRUG_CODE')
            drug, dr = DATA[eid + '-drug'], DATA[eid + '-drug-role']
            g.add((process, S.hasParticipant, dr))
            g.add((dr, RDF.type, EX.AdministeredDrugRole))
            g.add((dr, S.isFeatureOf, drug))
            g.add((drug, RDF.type, DRUGS[row['source_code']]))
    return g


def validate_graph(source):
    g = materialize(source)
    # Check the named disjoint upper categories needed by this profile only.
    # This does not replace OWL consistency checking for arbitrary class expressions.
    disjoint_groups = [(S.Object, S.Process), (S.SpatialObject, S.Feature),
                       (S.Role, S.Quality, S.InformationObject, S.Capability),
                       (S.Duration, S.TimeInstant, S.TimeInterval), (S.Time, S.Unit),
                       (S.Collection, S.Quantity)]
    for subject in set(g.subjects(RDF.type, None)):
        types = set(g.objects(subject, RDF.type))
        for group in disjoint_groups:
            require(len(types.intersection(group)) <= 1, 'DISJOINT_UPPER_CLASSES')
    for subject, predicate, obj in source:
        require(isinstance(subject, URIRef), 'NAMED_INSTANCE_REQUIRED')
        if isinstance(obj, Literal):
            require(predicate == S.hasValue, 'SOLID_LITERAL_PROPERTY')
            require((subject, RDF.type, S.InformationObject) in g, 'SOLID_INFORMATION_OBJECT')
        else:
            require(predicate == RDF.type or predicate in OBJECT_PROPERTIES, 'UNSUPPORTED_OBJECT_PROPERTY')
            require(predicate != S.hasValue, 'NON_LITERAL_VALUE')
    for subject in set(g.subjects(S.hasValue, None)):
        one(g.objects(subject, S.hasValue), 'SOLID_VALUE_CARDINALITY')
    normalize_before = rdflib.NORMALIZE_LITERALS
    try:
        conforms, _, report = shacl_validate(g, shacl_graph=Graph().parse(ROOT / 'ontology/pro-solid-shapes.ttl'),
                                             inference='none', do_owl_imports=False)
    finally:
        # pySHACL changes this process-global flag; restore the loader's configuration.
        rdflib.NORMALIZE_LITERALS = normalize_before
    require(conforms, 'SHACL_PROFILE_FAILURE:\n' + report)
    return g


def project(source, manifest):
    """Return existing v2.0 matcher rows plus separate graph-binding evidence."""
    g = validate_graph(source)
    require(manifest['profile'] == 'pro-solid-2.4', 'UNSUPPORTED_PROFILE')
    instant(manifest['origin'])
    processes = set(g.subjects(RDF.type, EX.RecordedClinicalProcess))
    require(bool(processes), 'EMPTY_PROJECTION')
    records = set(g.subjects(RDF.type, EX.SourceRecord))
    require(len(records) == len(processes), 'RECORD_PROCESS_CARDINALITY')
    output, evidence, identifiers, record_identifiers = [], {}, set(), set()
    used_roles, used_records = set(), set()
    for process in sorted(processes):
        pr, patient = role_binding(g, process, EX.PatientRole)
        patient_id = datum(g, patient, EX.PatientIdentifier, S.hasFeature)
        record = one((r for r in g.subjects(S.refersTo, process) if (r, RDF.type, EX.SourceRecord) in g),
                     'SOURCE_RECORD_CARDINALITY')
        require(record not in used_records, 'REUSED_SOURCE_RECORD')
        used_records.add(record)
        meta = {name: datum(g, record, cls) for name, cls in METADATA.items()}
        require(meta['event_id'] not in identifiers, 'DUPLICATE_EVENT_IDENTIFIER')
        require(meta['record_id'] not in record_identifiers, 'DUPLICATE_RECORD_IDENTIFIER')
        identifiers.add(meta['event_id']); record_identifiers.add(meta['record_id'])
        require(bool(re.fullmatch('[0-9a-f]{64}', meta['source_hash'])), 'INVALID_SOURCE_HASH')
        require(meta['status'] == 'performed', 'PROCESS_STATUS_MISMATCH')
        t = one(g.objects(process, S.atTime), 'ANCHOR_TIME_CARDINALITY')
        tv = one(g.objects(t, S.hasValue), 'TIME_VALUE_CARDINALITY')
        require(tv.datatype in (XSD.dateTime, XSD.dateTimeStamp), 'TIME_DATATYPE')
        require(typed_objects(g, t, S.hasPart, S.Unit) == {EX.Second}, 'TIME_UNIT_PROFILE')
        us = offset_us(str(tv), manifest['origin'])
        binding = {'process': str(process), 'patient_role': str(pr), 'patient_bearer': str(patient),
                   'source_record': str(record), 'time_datum': str(t), 'original_datetime': str(tv),
                   'source_record_sha256': meta['source_hash']}
        original_value = original_unit = value = unit_text = None
        if (process, RDF.type, EX.Measurement) in g:
            rr, q = role_binding(g, process, EX.MeasurementResultRole)
            quality = one(g.objects(q, S.refersTo), 'MEASURED_QUALITY_CARDINALITY')
            require((quality, RDF.type, EX.CreatinineConcentration) in g, 'UNSUPPORTED_MEASURED_QUALITY')
            require(set(g.objects(quality, S.isFeatureOf)) == {patient}, 'MEASUREMENT_SUBJECT_MISMATCH')
            v = one(g.objects(q, S.hasValue), 'MEASUREMENT_VALUE_CARDINALITY')
            require(v.datatype == XSD.decimal, 'MEASUREMENT_DECIMAL_REQUIRED')
            try:
                number = Decimal(str(v))
            except InvalidOperation as error:
                raise ContractError('INVALID_DECIMAL') from error
            require(number.is_finite() and number >= 0, 'INVALID_CONCENTRATION')
            unit = one(typed_objects(g, q, S.hasPart, S.Unit), 'MEASUREMENT_UNIT_CARDINALITY')
            require(unit in UNIT_MAP, 'UNSUPPORTED_UNIT_OR_DIMENSION')
            original_unit, factor = UNIT_MAP[unit]
            with localcontext() as context:
                context.prec = max(28, len(number.as_tuple().digits) + len(factor.as_tuple().digits) + 2)
                original_value, value, unit_text = str(v), decimal_text(number * factor), 'mg/dL'
            concept, kind = 'ex:Creatinine', 'measurement'
            binding.update(result_role=str(rr), result_datum=str(q), measured_quality=str(quality),
                           unit_resource=str(unit), unit_factor=decimal_text(factor))
        else:
            require((process, RDF.type, EX.AdministrationRecordProcess) in g, 'UNSUPPORTED_PROCESS')
            dr, drug = role_binding(g, process, EX.AdministeredDrugRole)
            direct = {k for k, cls in DRUGS.items() if (drug, RDF.type, cls) in source}
            concept = one(direct, 'DRUG_ASSERTED_CONCEPT_CARDINALITY')
            kind = 'administration'
            binding.update(drug_role=str(dr), drug_bearer=str(drug))
        require(meta['source_code'] == concept, 'SOURCE_CONCEPT_MISMATCH')
        explicit_roles = typed_objects(g, process, S.hasParticipant, S.Role)
        require(not (explicit_roles & used_roles), 'ROLE_REUSED_ACROSS_PROCESSES')
        used_roles.update(explicit_roles)
        # Evidence is linked by a deterministic assertion ID; source graph hash is recorded separately.
        binding.update(original_value=original_value, original_unit=original_unit,
                       normalized_value=value, normalized_unit=unit_text, concept=concept)
        assertion_id = 'pro-solid:' + digest(json.dumps(binding, sort_keys=True))
        evidence[meta['event_id']] = {**binding, 'assertion_id': assertion_id}
        output.append({
            'dataset_id': manifest['dataset_id'], 'snapshot_id': manifest['snapshot_id'],
            'patient_id': patient_id, 'episode_id': meta['episode_id'], 'event_id': meta['event_id'],
            'record_id': meta['record_id'], 'clinical_event_id': str(process), 'event_kind': kind,
            'concept': concept, 'source_code': meta['source_code'], 'source_system': 'constructed_fixture',
            'mapping_ref': 'pro-solid-2.4', 'status': meta['status'], 'experiencer': 'patient',
            'time': {'kind': 'point', 'clock_id': manifest['clock_id'], 'start_min_us': us,
                     'start_max_us': us, 'end_min_us': us, 'end_max_us': us,
                     'precision': ('second' if '.' not in str(tv) else 'microsecond'),
                     'timestamp_role': 'occurrence'},
            'value': value, 'unit': unit_text, 'original_value': original_value,
            'original_unit': original_unit, 'comparator': 'eq',
            'provenance': {'source_file': meta['source_file'], 'source_key': meta['source_key'],
                           'source_record_sha256': meta['source_hash'], 'adapter_rule': 'pro-solid-2.4',
                           'assertion_ids': [assertion_id]}, 'record_version': 1, 'supersedes': None})
    graph_hash = digest('\n'.join(sorted(' '.join(x.n3() for x in triple) + ' .' for triple in source)))
    case = {k: manifest[k] for k in ('patient_id', 'episode_id', 'age', 'source_search_complete')}
    case.update(case_id='PRO-SOLID-01', description='Projected PRO/SOLID synthetic trajectory',
                events=output, budget_override=None)
    return case, {'profile': 'pro-solid-2.4', 'source_graph_sha256': graph_hash,
                  'snapshot_id': manifest['snapshot_id'], 'bindings': evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'examples/pro-solid/source-rows.json')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'examples/pro-solid/manifest.json')
    parser.add_argument('--graph', type=Path, help='Validate/project existing Turtle instead of building from rows')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/pro-solid-run')
    args = parser.parse_args()
    source = Graph().parse(args.graph) if args.graph else build_graph(json.loads(args.source.read_text()))
    case, evidence = project(source, json.loads(args.manifest.read_text()))
    from reference_oracle import evaluate
    result = evaluate(case, json.loads((ROOT / 'examples/exemplar.pattern.json').read_text()),
                      json.loads((ROOT / 'ontology/toy-taxonomy.json').read_text()))
    args.output.mkdir(parents=True, exist_ok=True)
    source.serialize(args.output / 'graph.ttl', format='turtle')
    for name, content in [('matcher-case.json', case), ('evidence.json', evidence), ('match.json', result)]:
        (args.output / name).write_text(json.dumps(content, indent=2) + '\n')
    print(json.dumps({'events': len(case['events']), 'accepted_as': result['accepted_as'],
                      'total_cost': result['total_cost'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
