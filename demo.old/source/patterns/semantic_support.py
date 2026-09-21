"""Checked Rust class support for bounded temporal queries over a selected snapshot.

Only the explicit, finite Horn fragment below is admitted. Full SULO/import
consistency, arbitrary OWL inputs and inferred identity are not claimed.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import bounded_cohort as cohort
from . import bounded_intervals as bt
from . import exact_intervals as ei
from . import semantic_reference as reference

PROFILE = 'semantic-support-1.0'
QUERY_PROFILE = 'semantic-bounded-query-1.0'
PROPERTIES = {str(ei.S.hasParticipant), str(ei.S.isFeatureOf)}
FILES = ('patterns/semantic_support.py', 'patterns/semantic_reference.py', 'patterns/rustdl_worker.py',
         'patterns/bounded_cohort.py', 'patterns/requirements-semantic.lock.txt')


def iri(value):
    ei.require(isinstance(value, str) and len(value) <= 512 and
               re.fullmatch(r'[A-Za-z][A-Za-z0-9+.-]*:[^\s<>"{}|^`\\]+', value) is not None and
               not any(ord(c) < 32 or ord(c) == 127 for c in value), 'INVALID_SEMANTIC_IRI')
    ei.require(not value.startswith(('http://www.w3.org/2002/07/owl#',
                                    'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
                                    'http://www.w3.org/2000/01/rdf-schema#',
                                    'http://www.w3.org/2001/XMLSchema#')), 'RESERVED_SEMANTIC_IRI')
    return value


def rows(value, maximum, name):
    ei.require(type(value) is list and len(value) <= maximum, 'SEMANTIC_LIMIT:' + name)
    return value


def fields(value, names):
    ei.require(type(value) is dict and set(value) == set(names.split()), 'UNSUPPORTED_SEMANTIC_FIELDS')


def validate_model(model):
    fields(model, 'classes individuals class_assertions property_assertions rules disjoint')
    for field, limit in [('classes', 32), ('individuals', 64)]:
        items = rows(model[field], limit, field)
        for item in items:
            iri(item)
        ei.require(len(items) == len(set(items)), 'DUPLICATE_SEMANTIC_IRI')
    classes, individuals = set(model['classes']), set(model['individuals'])
    ei.require(not classes & individuals and not (classes | individuals) & PROPERTIES, 'SEMANTIC_PUNNING_UNSUPPORTED')

    def cls(value):
        ei.require(iri(value) in classes, 'UNDECLARED_CLASS')

    def ind(value):
        ei.require(iri(value) in individuals, 'UNDECLARED_INDIVIDUAL')

    def prop(value):
        ei.require(type(value) is str and value in PROPERTIES, 'UNSUPPORTED_PROPERTY')

    nodes = 0

    def expression(value, depth=0):
        nonlocal nodes
        nodes += 1
        ei.require(depth <= 4 and nodes <= 256 and type(value) is dict and len(value) == 1,
                   'UNSUPPORTED_RULE_EXPRESSION')
        if 'class' in value:
            cls(value['class'])
        elif 'all' in value:
            terms = rows(value['all'], 8, 'conjunction')
            ei.require(len(terms) >= 2, 'EMPTY_OR_UNARY_CONJUNCTION')
            for term in terms:
                expression(term, depth + 1)
        elif 'some' in value:
            fields(value['some'], 'property filler')
            prop(value['some']['property'])
            expression(value['some']['filler'], depth + 1)
        else:
            raise ei.ContractError('UNSUPPORTED_RULE_EXPRESSION')

    for row in rows(model['class_assertions'], 256, 'class_assertions'):
        fields(row, 'individual class')
        ind(row['individual'])
        cls(row['class'])
    for row in rows(model['property_assertions'], 256, 'property_assertions'):
        fields(row, 'subject property object')
        ind(row['subject'])
        ind(row['object'])
        prop(row['property'])
    ids = set()
    for row in rows(model['rules'], 64, 'rules'):
        fields(row, 'id if then')
        ei.identifier(row['id'])
        ei.require(row['id'] not in ids, 'DUPLICATE_RULE_ID')
        ids.add(row['id'])
        expression(row['if'])
        cls(row['then'])
    for row in rows(model['disjoint'], 32, 'disjoint'):
        fields(row, 'id classes')
        ei.identifier(row['id'])
        ei.require(row['id'] not in ids, 'DUPLICATE_RULE_ID')
        ids.add(row['id'])
        pair = rows(row['classes'], 2, 'disjoint_pair')
        ei.require(len(pair) == 2 and pair[0] != pair[1], 'INVALID_DISJOINT_PAIR')
        for item in pair:
            cls(item)
    return model


def build_model(snapshot, module):
    fields(module, 'profile source_sha256 classes individuals class_assertions property_assertions rules disjoint')
    ei.require(module['profile'] == PROFILE, 'UNSUPPORTED_SEMANTIC_PROFILE')
    ei.require(module['source_sha256'] == ei.digest(ei.canonical(snapshot.source)), 'STALE_SEMANTIC_SOURCE')
    # Reject oversized/untyped additions before building the union; the final
    # combined module must also fit the same hard limits.
    for key, limit in [('classes', 32), ('individuals', 64), ('class_assertions', 256),
                       ('property_assertions', 256), ('rules', 64), ('disjoint', 32)]:
        rows(module[key], limit, key)
    for key in ('classes', 'individuals'):
        for value in module[key]:
            iri(value)
        ei.require(len(module[key]) == len(set(module[key])), 'DUPLICATE_SEMANTIC_IRI')
    model = deepcopy({k: module[k] for k in ('classes', 'individuals', 'class_assertions',
                                           'property_assertions', 'rules', 'disjoint')})
    classes, individuals = set(model['classes']), set(model['individuals'])
    kinds = {kind: bt.selected_classes({'event_kind': kind}) for kind in bt.KINDS}
    for eid, event in sorted(snapshot.events.items()):
        witness = snapshot.evidence[eid]
        process, role, bearer = (witness[k] for k in ('process', 'patient_role', 'patient_bearer'))
        individuals.update((process, role, bearer))
        types = [(process, cls) for cls in sorted(kinds[event['event_kind']])]
        types.extend([(role, str(ei.EX.PatientRole)), (bearer, str(ei.EX.Person))])
        for individual, cls in types:
            classes.add(cls)
            model['class_assertions'].append({'individual': individual, 'class': cls})
        model['property_assertions'].extend([
            {'subject': process, 'property': str(ei.S.hasParticipant), 'object': role},
            {'subject': role, 'property': str(ei.S.isFeatureOf), 'object': bearer}])
    model['classes'], model['individuals'] = sorted(classes), sorted(individuals)
    # RDF/OWL assertions are sets. Retain all original source rows in the outer
    # context, but avoid duplicate projection assertions wasting the finite cap.
    for key in ('class_assertions', 'property_assertions'):
        model[key] = [json.loads(row) for row in sorted({ei.canonical(row) for row in model[key]})]
    return validate_model(model)


def validate_query(query, classes):
    return _validate_query(query, classes)


def _validate_query(query, classes, *, profile=QUERY_PROFILE):
    fields(query, 'profile id slots constraints')
    ei.require(query['profile'] == profile, 'UNSUPPORTED_SEMANTIC_QUERY_PROFILE')
    rows(query['slots'], 8, 'slots')
    normalized = deepcopy(query)
    normalized['profile'] = bt.QUERY_PROFILE
    for slot in normalized['slots']:
        fields(slot, 'id class_iri')
        ei.require(iri(slot['class_iri']) in classes, 'UNDECLARED_SELECTOR')
        slot['class_iri'] = str(ei.S.Process)
    bt.validate(normalized, query=True)
    return query


def ofn(model):
    """Serialize only validated constructs. No user-supplied OWL text/imports."""
    validate_model(model)
    def term(expression):
        if 'class' in expression:
            return '<' + expression['class'] + '>'
        if 'all' in expression:
            return 'ObjectIntersectionOf(' + ' '.join(term(e) for e in expression['all']) + ')'
        some = expression['some']
        return 'ObjectSomeValuesFrom(<' + some['property'] + '> ' + term(some['filler']) + ')'
    lines = ['Ontology(']
    lines.extend('Declaration(Class(<' + c + '>))' for c in model['classes'])
    lines.extend('Declaration(NamedIndividual(<' + i + '>))' for i in model['individuals'])
    lines.extend('Declaration(ObjectProperty(<' + p + '>))' for p in sorted(PROPERTIES))
    lines.extend('ClassAssertion(<' + a['class'] + '> <' + a['individual'] + '>)'
                 for a in model['class_assertions'])
    lines.extend('ObjectPropertyAssertion(<' + a['property'] + '> <' + a['subject'] + '> <' + a['object'] + '>)'
                 for a in model['property_assertions'])
    lines.extend('SubClassOf(' + term(r['if']) + ' <' + r['then'] + '>)' for r in model['rules'])
    lines.extend('DisjointClasses(' + ' '.join('<' + c + '>' for c in r['classes']) + ')'
                 for r in model['disjoint'])
    return '\n'.join([*lines, ')', ''])


def run_backend(document, classes, timeout_seconds):
    ei.require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 60, 'INVALID_BACKEND_TIMEOUT')
    env = {key: value for key, value in os.environ.items() if not key.startswith('RUSTDL_')}
    # Use this checkout even when invoked from a different working directory.
    env['PYTHONPATH'] = os.pathsep.join([str(ei.ROOT), *sys.path])
    try:
        result = subprocess.run([sys.executable, '-m', 'patterns.rustdl_worker'],
                                input=json.dumps({'ofn': document, 'classes': classes}),
                                text=True, capture_output=True, timeout=timeout_seconds,
                                cwd=ei.ROOT, env=env, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'BACKEND_TIMEOUT'}
    except OSError as error:
        return {'status': 'BACKEND_ERROR', 'reason': str(error)}
    if result.returncode:
        return {'status': 'BACKEND_ERROR', 'returncode': result.returncode, 'stderr': result.stderr[-4000:]}
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {'status': 'MALFORMED_BACKEND_RESPONSE', 'stderr': result.stderr[-4000:]}
    if type(response) is not dict:
        return {'status': 'MALFORMED_BACKEND_RESPONSE'}
    if result.stderr:
        response['stderr'] = result.stderr[-4000:]
    return response


def check(model, classes, *, timeout_seconds=20):
    validate_model(model)
    ei.require(type(classes) is list and len(classes) <= 8 and all(c in model['classes'] for c in classes),
               'UNDECLARED_SELECTOR')
    expected = reference.evaluate(model)
    document = ofn(model)
    actual = run_backend(document, classes, timeout_seconds)
    result = {'reference': expected, 'backend_result': actual, 'ontology_ofn': document,
              'ontology_sha256': ei.digest(document), 'memberships': None}
    def blocked(reason):
        return {**result, 'status': 'UNRESOLVED_SEMANTICS', 'reason_code': reason}
    if actual.get('status') != 'OK':
        return blocked(actual.get('status', 'MALFORMED_BACKEND_RESPONSE'))
    if set(actual) - {'status', 'consistent', 'memberships', 'dropped', 'warnings', 'backend', 'stderr'}:
        return blocked('MALFORMED_BACKEND_RESPONSE')
    if type(actual.get('consistent')) is not bool or type(actual.get('memberships')) is not dict:
        return blocked('MALFORMED_BACKEND_RESPONSE')
    if actual.get('dropped') != {} or actual.get('warnings') != [] or actual.get('stderr'):
        return blocked('BACKEND_DIAGNOSTIC')
    backend = actual.get('backend', {})
    if (type(backend) is not dict or backend.get('name') != 'rustdl' or backend.get('version') != '0.4.28' or
            any(not isinstance(backend.get(key), str) or not re.fullmatch('[0-9a-f]{64}', backend[key])
                for key in ('native_sha256', 'wrapper_sha256'))):
        return blocked('MALFORMED_BACKEND_RESPONSE')
    if actual['consistent'] != expected['consistent']:
        return blocked('CONSISTENCY_DISAGREEMENT')
    if not expected['consistent']:
        if actual['memberships'] != {}:
            return blocked('MALFORMED_BACKEND_RESPONSE')
        return {**result, 'status': 'INCONSISTENT_ONTOLOGY', 'reason_code': 'NAMED_DISJOINTNESS_CLASH'}
    wanted = {cls: expected['memberships'][cls] for cls in classes}
    if actual['memberships'] != wanted:
        return blocked('CLASS_SUPPORT_DISAGREEMENT')
    return {**result, 'status': 'READY', 'memberships': wanted}


def execute(snapshot, query, module, *, timeout_seconds=20):
    ei.require(snapshot.source['profile'] == bt.PROFILE_ID, 'UNSUPPORTED_SNAPSHOT_PROFILE')
    return _execute_checked(snapshot, query, module, timeout_seconds=timeout_seconds)


def _execute_checked(snapshot, query, module, *, timeout_seconds=20, profile=PROFILE,
                     query_profile=QUERY_PROFILE, interpretation=None, extra_files=()):
    interpretation = interpretation or {}
    query, module = deepcopy(query), deepcopy(module)
    model = build_model(snapshot, module)
    _validate_query(query, model['classes'], profile=query_profile)
    selectors = sorted({s['class_iri'] for s in query['slots']})
    support = check(model, selectors, timeout_seconds=timeout_seconds)
    context = {'profile': profile, 'source_context_id': snapshot.context_id, 'query': deepcopy(query),
               'module': deepcopy(module), 'model_sha256': ei.digest(ei.canonical(model)),
               'ontology_sha256': support['ontology_sha256'],
               'backend': support['backend_result'], 'timeout_seconds': timeout_seconds,
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in FILES + extra_files}}
    context.update(interpretation)
    context_id = ei.digest(ei.canonical(context))
    result = {'profile': profile, 'status': support['status'], 'context': context, 'context_id': context_id,
              'semantic_support': support, 'matching': None, 'full_owl_mapping_verified': False,
              'semantic_support_complete': support['status'] == 'READY',
              'scope': 'selected_snapshot_and_explicit_semantic_module'}
    result.update(interpretation)
    if support['status'] != 'READY':
        return result
    types = {eid: {cls for cls in selectors if witness['process'] in support['memberships'][cls]}
             for eid, witness in snapshot.evidence.items()}
    matching = cohort._execute_supported(snapshot, query, types)
    matching['profile'] = query_profile
    matching['context'] = {**matching['context'], 'profile': query_profile, 'semantic_context_id': context_id, **interpretation}
    matching['context_id'] = ei.digest(ei.canonical(matching['context']))
    for trajectory in matching['trajectories']:
        for binding in trajectory['bindings']:
            for slot in binding['slots'].values():
                witness = snapshot.evidence[slot['event_id']]
                slot.update({key: witness[key] for key in ('process', 'patient_role', 'patient_bearer')})
                slot['semantic_support'] = {'status': 'ENTAILED', 'context_id': context_id,
                                            'individual': witness['process'], 'class': slot['selected_class_iri']}
    matching.update(interpretation)
    matching['semantic_scope'] = result['scope']
    result['matching'] = matching
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    folder = ei.ROOT / 'examples/semantic-support'
    parser.add_argument('--source', type=Path, default=ei.ROOT / 'examples/bounded-interval/source.json')
    parser.add_argument('--module', type=Path, default=folder / 'module.json')
    parser.add_argument('--query', type=Path, default=folder / 'query.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/semantic-support-run')
    parser.add_argument('--timeout-seconds', type=float, default=20)
    args = parser.parse_args()
    try:
        snapshot = bt.prepare(json.loads(args.source.read_text()))
        result = execute(snapshot, json.loads(args.query.read_text()), json.loads(args.module.read_text()),
                         timeout_seconds=args.timeout_seconds)
    except bt.InconsistentSource as error:
        parser.exit(2, json.dumps(error.details) + '\n')
    except (ei.ContractError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.output / 'module.ofn').write_text(result['semantic_support']['ontology_ofn'])
    print(json.dumps({'status': result['status'], 'output': str(args.output),
                      'certain_patient_ids': result['matching']['certain_patient_ids'] if result['matching'] else None}))
    if result['status'] != 'READY':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
