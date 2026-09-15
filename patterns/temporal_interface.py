"""A bounded query interface between explicit formal-core fixtures and SULO RDF.

No OWL equivalence or complete ontology reasoning is implemented. Formal inputs
are named, explicitly typed fixture witnesses plus an external coordinate table.
"""
import argparse
from itertools import permutations
import hashlib
import json
from pathlib import Path

from rdflib import Graph, Namespace, OWL, RDF, URIRef

from . import bounded_cohort as cohort
from . import bounded_intervals as bt
from . import bounded_rdf as rdf
from . import bounded_reference as reference
from . import exact_intervals as ei

F = Namespace('https://example.org/temporal-kg/v2#')
PROFILE = 'temporal-interface-1.0'
EXAMPLES = ei.ROOT / 'examples/temporal-interface'


def view(source):
    """Project only the declared common coordinate/query fragment."""
    fields = {
        'clocks': ('clock_id', 'origin', 'scope', 'policy'),
        'variables': ('id', 'patient_id', 'episode_id', 'clock_id', 'lower_us', 'upper_us'),
        'events': ('id', 'patient_id', 'episode_id', 'start_var', 'end_var'),
        'constraints': ('id', 'left_var', 'right_var', 'upper_us'),
    }
    return {kind: sorted(({k: row[k] for k in keys} for row in source[kind]),
                         key=lambda row: row.get('id', row.get('clock_id')))
            for kind, keys in fields.items()}


def formal_view(graph, interface):
    """Validate an explicit fixture interface without calling the production compiler."""
    ei.fields(interface, ('profile', 'clocks', 'variables', 'constraints', 'points', 'processes'))
    ei.require(interface['profile'] == PROFILE, 'UNSUPPORTED_FORMAL_INTERFACE')
    data = json.loads(ei.canonical(interface))
    for key in ('clocks', 'variables', 'constraints', 'points', 'processes'):
        ei.require(isinstance(data[key], list), 'FORMAL_ARRAY_REQUIRED')
    for s, p, o in graph:
        ei.require(isinstance(s, URIRef) and isinstance(p, URIRef) and isinstance(o, URIRef), 'FORMAL_NAMED_TERMS_REQUIRED')
    used, entities = set(), {}

    def typed(node, cls):
        ei.require(set(graph.objects(node, RDF.type)) == {cls}, 'FORMAL_EXPLICIT_TYPE_REQUIRED:' + str(cls))
        used.add((node, RDF.type, cls))

    def link(node, prop):
        target = ei.one(graph.objects(node, prop), 'FORMAL_UNIQUE_NAMED_LINK:' + str(prop))
        used.add((node, prop, target))
        return target

    clocks = {}
    for clock in data['clocks']:
        ei.fields(clock, ('clock_id', 'origin', 'scope', 'policy'))
        ei.identifier(clock['clock_id'])
        ei.require(clock['clock_id'] not in clocks, 'FORMAL_DUPLICATE_CLOCK')
        ei.require(clock['policy'] == 'offset-datetime-microseconds-v1', 'FORMAL_CLOCK_POLICY')
        scope = ei.text(clock['scope'])
        if scope != 'global':
            ei.require(scope.startswith('patient:'), 'FORMAL_CLOCK_SCOPE')
            ei.identifier(scope[8:])
        ei.normalize(clock['origin'], clock['origin'])
        clocks[clock['clock_id']] = clock
    variables = {}
    for variable in data['variables']:
        ei.fields(variable, ('id', 'patient_id', 'episode_id', 'clock_id', 'lower_us', 'upper_us'))
        for name in ('id', 'patient_id', 'episode_id', 'clock_id'):
            ei.identifier(variable[name])
        ei.require(variable['id'] not in variables, 'FORMAL_DUPLICATE_VARIABLE')
        ei.checked_us(variable['lower_us']); ei.checked_us(variable['upper_us'])
        ei.require(variable['lower_us'] <= variable['upper_us'], 'FORMAL_REVERSED_BOUNDS')
        ei.require(variable['clock_id'] in clocks, 'FORMAL_UNKNOWN_CLOCK')
        ei.require(clocks[variable['clock_id']]['scope'] in ('global', 'patient:' + variable['patient_id']), 'FORMAL_CLOCK_SCOPE')
        variables[variable['id']] = variable
    points = {}
    for point in data['points']:
        ei.fields(point, ('point', 'variable_id'))
        ei.identifier(point['variable_id'])
        node = URIRef(ei.text(point['point']))
        ei.require(node not in points and point['variable_id'] in variables, 'FORMAL_POINT_COORDINATE')
        typed(node, F.TimePoint)
        points[node] = point['variable_id']
    events, process_names, intervals, roles, people = [], set(), set(), set(), {}
    referenced_points = set()
    for item in data['processes']:
        ei.fields(item, ('id', 'process', 'patient_id', 'episode_id'))
        for name in ('id', 'patient_id', 'episode_id'):
            ei.identifier(item[name])
        ei.require(item['id'] not in entities, 'FORMAL_DUPLICATE_EVENT')
        process = URIRef(ei.text(item['process']))
        ei.require(process not in process_names, 'FORMAL_DUPLICATE_PROCESS')
        process_names.add(process); typed(process, F.Process)
        role = link(process, F.hasParticipant); typed(role, F.PatientRole)
        ei.require(role not in roles, 'FORMAL_REUSED_ROLE'); roles.add(role)
        person = link(role, F.isFeatureOf); typed(person, F.Patient)
        ei.require(people.get(item['patient_id'], person) == person, 'FORMAL_PATIENT_IDENTITY')
        ei.require(person not in people.values() or people.get(item['patient_id']) == person, 'FORMAL_PATIENT_ALIAS')
        people[item['patient_id']] = person
        interval = link(process, F.exactExtent); typed(interval, F.TimeInterval)
        ei.require(interval not in intervals, 'FORMAL_REUSED_EXTENT'); intervals.add(interval)
        start, end = link(interval, F.hasBeginning), link(interval, F.hasEnd)
        ei.require(start in points and end in points, 'FORMAL_UNMAPPED_ENDPOINT')
        ei.require(start != end, 'FORMAL_IDENTICAL_ENDPOINTS')
        referenced_points.update((start, end))
        sv, ev = variables[points[start]], variables[points[end]]
        scope = (item['patient_id'], item['episode_id'])
        ei.require((sv['patient_id'], sv['episode_id']) == (ev['patient_id'], ev['episode_id']) == scope
                   and sv['clock_id'] == ev['clock_id'], 'FORMAL_ENDPOINT_SCOPE')
        events.append({'id': item['id'], 'patient_id': item['patient_id'], 'episode_id': item['episode_id'],
                       'start_var': sv['id'], 'end_var': ev['id']})
        entities[item['id']] = {'process': str(process), 'patient_role': str(role), 'patient_bearer': str(person),
                                'interval': str(interval), 'start_point': str(start), 'end_point': str(end)}
    ei.require(referenced_points == set(points), 'FORMAL_UNUSED_POINT')
    different = []
    for a, b in graph.subject_objects(OWL.differentFrom):
        ei.require(a in points and b in points and a != b, 'FORMAL_POINT_DIFFERENCE')
        used.add((a, OWL.differentFrom, b))
        different.append([str(a), str(b)])
    ei.require(set(graph) == used, 'FORMAL_UNCONSUMED_TRIPLES')
    ids = set()
    for c in data['constraints']:
        ei.fields(c, ('id', 'left_var', 'right_var', 'upper_us'))
        for key in ('id', 'left_var', 'right_var'): ei.identifier(c[key])
        ei.checked_us(c['upper_us'])
        ei.require(c['id'] not in ids, 'FORMAL_DUPLICATE_CONSTRAINT'); ids.add(c['id'])
        ei.require(c['left_var'] in variables and c['right_var'] in variables, 'FORMAL_CONSTRAINT_REFERENCE')
        a, b = variables[c['left_var']], variables[c['right_var']]
        ei.require((a['patient_id'], a['episode_id'], a['clock_id']) ==
                   (b['patient_id'], b['episode_id'], b['clock_id']), 'FORMAL_CONSTRAINT_SCOPE')
    ei.require(events and variables and clocks, 'FORMAL_EMPTY_INTERFACE')
    source = {k: data[k] for k in ('clocks', 'variables', 'constraints')}
    source['events'] = events
    return {'view': view(source), 'entities': entities, 'declared_different_points': sorted(different)}


def sulo_view(graph):
    snapshot = rdf.prepare_graph(graph)
    entities = {eid: {name: evidence[name] for name in ('process', 'patient_role', 'patient_bearer',
                 'interval', 'start_descriptor', 'end_descriptor', 'start_variable', 'end_variable')}
                for eid, evidence in snapshot.evidence.items()}
    return {'view': view(snapshot.source), 'entities': entities}, snapshot


def formal_answers(source, query):
    """Finite-world reference; never calls STN closure or the production query compiler."""
    bt.validate(query, query=True)
    ei.require(all(s['class_iri'] == str(ei.S.Process) for s in query['slots']), 'COMMON_SELECTOR_IS_PROCESS_ONLY')
    answers, certain, possible = [], set(), set()
    for scope in sorted({(v['patient_id'], v['episode_id']) for v in source['variables']}):
        worlds = reference.worlds(source, scope)
        ei.require(bool(worlds), 'FORMAL_TEMPORAL_INCONSISTENCY')
        events = sorted((e for e in source['events'] if (e['patient_id'], e['episode_id']) == scope), key=lambda e: e['id'])
        slots = sorted(s['id'] for s in query['slots'])
        for selected in permutations(events, len(slots)):
            binding = dict(zip(slots, selected))
            status = reference.classify(source, binding, query['constraints'], worlds)
            answers.append({'patient_id': scope[0], 'episode_id': scope[1],
                            'slots': {s: e['id'] for s, e in binding.items()}, 'status': status})
            if status == 'CERTAIN': certain.add(scope[0])
            if status in ('CERTAIN', 'POSSIBLE'): possible.add(scope[0])
    return {'bindings': answers, 'certain_patient_ids': sorted(certain), 'possible_patient_ids': sorted(possible)}


def matching_answers(result):
    return {'bindings': [{'patient_id': t['patient_id'], 'episode_id': t['episode_id'],
                          'slots': {s: b['event_id'] for s, b in binding['slots'].items()}, 'status': binding['status']}
                         for t in result['trajectories'] for binding in t['bindings']],
            'certain_patient_ids': result['certain_patient_ids'], 'possible_patient_ids': result['possible_patient_ids']}


def compare(folder):
    manifest = json.loads((folder / 'interface.json').read_text())
    formal = formal_view(Graph().parse(folder / 'formal.ttl'), manifest)
    sulo, snapshot = sulo_view(Graph().parse(folder / 'sulo.ttl'))
    ei.require(formal['view'] == sulo['view'], 'MAPPING_VIEW_MISMATCH')
    checks = []
    for query in json.loads((folder / 'queries.json').read_text()):
        expected = formal_answers(formal['view'], query)
        actual = matching_answers(cohort.execute(snapshot, query))
        ei.require(actual == expected, 'MAPPING_ANSWER_MISMATCH:' + query['id'])
        checks.append({'query_id': query['id'], 'answers': actual})
    files = ('patterns/temporal_interface.py', 'patterns/bounded_reference.py', 'patterns/bounded_rdf.py',
             'patterns/bounded_intervals.py', 'patterns/bounded_cohort.py', 'patterns/temporal_stn.py',
             'docs/temporal-kg/ontology/temporal-core.ofn', 'docs/temporal-kg/validation/checks.json',
             'docs/temporal-kg/sulo-conformance.json', 'examples/temporal-interface/cases.json')
    context = {'profile': PROFILE, 'case': folder.name,
               'inputs': {n: hashlib.sha256((folder / n).read_bytes()).hexdigest()
                          for n in ('interface.json', 'formal.ttl', 'sulo.ttl', 'queries.json')},
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in files},
               'sulo_context': snapshot.context, 'ontology_support': 'explicit_formal_types_and_existing_bounded_sulo_profile'}
    return {'profile': PROFILE, 'context_id': ei.digest(ei.canonical(context)), 'context': context,
            'view': formal['view'], 'formal_entities': formal['entities'], 'sulo_entities': sulo['entities'],
            'formal_declared_different_points': formal['declared_different_points'],
            'checks': checks, 'full_owl_mapping_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/temporal-interface-run')
    args = parser.parse_args()
    cases = json.loads((EXAMPLES / 'cases.json').read_text())
    try:
        results = [compare(EXAMPLES / case['id']) for case in cases]
    except (ei.ContractError, ValueError) as error:
        parser.exit(2, json.dumps({'status': 'FAILED', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'result.json').write_text(json.dumps({'profile': PROFILE, 'cases': results}, indent=2) + '\n')
    print(json.dumps({'cases_passed': len(results), 'query_comparisons': sum(len(r['checks']) for r in results),
                      'full_owl_mapping_verified': False, 'output': str(args.output)}))


if __name__ == '__main__':
    main()
