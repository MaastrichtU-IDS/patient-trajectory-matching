"""Patient-local calendar coordinates, bounded queries and lossless RDF ingestion."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from rdflib import Graph, Literal, Namespace, RDF, URIRef, XSD
from rdflib.exceptions import ParserError
from rdflib.plugins.parsers.notation3 import BadSyntax

from . import bounded_intervals as bt
from . import bounded_cohort as cohort
from . import bounded_rdf as br
from . import exact_intervals as ei

PROFILE_ID = 'patient-local-interval-1.0'
RDF_PROFILE = 'patient-local-rdf-1.0'
QUERY_PROFILE = 'patient-local-query-1.0'
POLICY = 'patient-local-calendar-microseconds-v1'
TIME_DOMAIN = 'bounded-patient-local-calendar-microseconds'
PL = Namespace('https://example.org/trajectory/patient-local/')
SCHEMA = ei.ROOT / 'schemas/patient-local.schema.json'
ONTOLOGY = ei.ROOT / 'ontology/patient-local-profile.ttl'
FILES = ('patterns/patient_local.py', 'schemas/patient-local.schema.json',
         'ontology/patient-local-profile.ttl')


def parse_local(value, *, origin=False):
    ei.require(isinstance(value, str), 'INVALID_LOCAL_DATETIME')
    separator = 'T' if origin else '[T ]'
    pattern = r'[0-9]{4}-[0-9]{2}-[0-9]{2}' + separator + r'[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?'
    ei.require(bool(re.fullmatch(pattern, value)), 'INVALID_LOCAL_DATETIME')
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise ei.ContractError('INVALID_LOCAL_DATETIME') from error


def coordinate(value, origin):
    """Exact arithmetic on naive calendar labels, with no inferred timezone.

    This converts a declared bound or recorded label, not its clinical precision.
    The origin is a fixed coordinate reference; uncertain anchors remain variables.
    """
    delta = parse_local(value) - parse_local(origin, origin=True)
    return ei.checked_us((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds)


def normalize(source):
    schema = json.loads(SCHEMA.read_text())
    bt._validate_contract(source, schema)
    source = json.loads(ei.canonical(source))
    clocks = bt.unique(source['clocks'], 'clock_id')
    for clock in clocks.values():
        parse_local(clock['origin'], origin=True)
    for variable in source['variables']:
        ei.require(variable['clock_id'] in clocks, 'UNKNOWN_CLOCK')
        clock = clocks[variable['clock_id']]
        for side in ('lower', 'upper'):
            variable[side + '_us'] = coordinate(variable['local_' + side], clock['origin'])
    return source


def compile_source(source):
    return bt._compile_validated(normalize(source))


def _compile_rdf_source(source):
    # Both raw labels and numeric bounds are evidence. Neither may override the other.
    raw = json.loads(ei.canonical(source))
    for variable in raw['variables']:
        del variable['lower_us'], variable['upper_us']
    derived = normalize(raw)
    ei.require(derived == source, 'LOCAL_COORDINATE_MISMATCH')
    return bt._compile_validated(derived)


def _build_graph(source):
    graph, evidence = bt.build_graph(source, origin_datatype=XSD.dateTime)
    graph.bind('pl', PL)
    def scalar(parent, suffix, cls, value):
        node = URIRef(str(parent) + '/' + suffix)
        graph.add((parent, ei.S.hasDirectPart, node))
        graph.add((node, RDF.type, cls))
        graph.add((node, ei.S.hasValue, Literal(value, datatype=XSD.string, normalize=False)))
    for clock in source['clocks']:
        scalar(bt.D['clocks/' + clock['clock_id']], 'origin-source', PL.OriginSourceKey, clock['origin_source_key'])
    for variable in source['variables']:
        parent = bt.D['variables/' + variable['id']]
        for side, cls in [('lower', PL.LocalLowerLexical), ('upper', PL.LocalUpperLexical)]:
            scalar(parent, 'local-' + side, cls, variable['local_' + side])
    return graph, evidence


def prepare(source):
    return bt._prepare_compiled(compile_source(source), profile_id=PROFILE_ID,
        graph_builder=_build_graph, extra_files=FILES, time_domain=TIME_DOMAIN)


def export_source(source):
    return br._export_snapshot(prepare(source), RDF_PROFILE)


def prepare_graph(graph):
    return br._prepare_graph(graph, profile_id=RDF_PROFILE, source_profile=PROFILE_ID,
        origin_datatype=XSD.dateTime, compiler=_compile_rdf_source,
        read_clock=lambda r, n: {'origin_source_key': r.string(n, PL.OriginSourceKey)},
        read_variable=lambda r, n: {'local_lower': r.string(n, PL.LocalLowerLexical),
                                    'local_upper': r.string(n, PL.LocalUpperLexical)},
        extra_ontologies=(ONTOLOGY,), extra_files=FILES, time_domain=TIME_DOMAIN)


def validate_query(query):
    return bt._validate_contract(query, json.loads(SCHEMA.read_text())['$defs']['query'], query=True)


def execute(snapshot, query):
    ei.require(snapshot.source['profile'] == PROFILE_ID, 'UNSUPPORTED_SNAPSHOT_PROFILE')
    validate_query(query)
    types = {kind: bt.selected_classes({'event_kind': kind}) for kind in bt.KINDS}
    result = cohort._execute_supported(snapshot, query,
        {event['id']: types[event['event_kind']] for event in snapshot.events.values()}, profile_id=QUERY_PROFILE)
    # Bind the interpretation in the query context, not just a display annotation.
    result['context'].update(time_domain=TIME_DOMAIN, gap_semantics='local_calendar_coordinate_difference',
                             physical_elapsed_time_verified=False)
    result['context_id'] = ei.digest(ei.canonical(result['context']))
    result.update(time_domain=TIME_DOMAIN, physical_elapsed_time_verified=False,
                  clinical_mapping_verified=False, source_history_verified=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--source', type=Path)
    inputs.add_argument('--graph', type=Path)
    example = ei.ROOT / 'examples/patient-local'
    parser.add_argument('--query', type=Path, default=example / 'query.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/patient-local-run')
    args = parser.parse_args()
    try:
        query = validate_query(json.loads(args.query.read_text()))
        graph = (Graph().parse(args.graph, format='turtle') if args.graph else
                 export_source(json.loads((args.source or example / 'source.json').read_text())))
        snapshot = prepare_graph(graph)
        result = execute(snapshot, query)
    except bt.InconsistentSource as error:
        parser.exit(2, json.dumps(error.details) + '\n')
    except (ei.ContractError, OSError, UnicodeError, json.JSONDecodeError, ParserError, BadSyntax) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'graph.ttl').write_text(ei.turtle_text(snapshot.graph))
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'profile': QUERY_PROFILE, 'certain_patient_ids': result['certain_patient_ids'],
                      'possible_patient_ids': result['possible_patient_ids'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
