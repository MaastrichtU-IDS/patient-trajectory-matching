"""Select temporal and semantic facts through one explicit source history."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from . import bounded_intervals as bt
from . import evidence_selection as es
from . import exact_intervals as ei
from . import semantic_support as ss

PROFILE = 'joint-evidence-selection-1.0'
POLICY_PROFILE = 'joint-semantic-policy-1.0'
SCHEMA = ei.ROOT / 'schemas/joint-evidence.schema.json'
FILES = ('patterns/joint_evidence.py', 'schemas/joint-evidence.schema.json',
         'patterns/semantic_support.py', 'patterns/semantic_reference.py',
         'patterns/rustdl_worker.py', 'patterns/requirements-semantic.lock.txt')


def validate_policy(policy):
    ss.fields(policy, 'profile id classes rules disjoint')
    ei.require(policy['profile'] == POLICY_PROFILE, 'UNSUPPORTED_JOINT_POLICY')
    ei.identifier(policy['id'])
    ss.validate_model({**{key: policy[key] for key in ('classes', 'rules', 'disjoint')},
                       'individuals': [], 'class_assertions': [], 'property_assertions': []})
    return policy


def validate(archive, request, policy):
    schema = json.loads(SCHEMA.read_text())
    for value, contract, label in ((archive, schema, 'JOINT_ARCHIVE'),
                                  (request, schema['$defs']['request'], 'SELECTION_REQUEST')):
        ei.require(not list(Draft202012Validator(contract).iter_errors(value)), 'INVALID_' + label + '_SCHEMA')
    validate_policy(policy)
    validated = es._validate_archive(archive, request)
    archive = validated[0]
    # All stored facts must have supported syntax; references to selected
    # events/local declarations are resolved only after revision selection.
    for assertion in archive['assertions']:
        bt.unique(assertion['bundle']['semantic_facts'], 'id')
        for fact in assertion['bundle']['semantic_facts']:
            ei.require(bt.scope(fact) == bt.scope(assertion), 'SEMANTIC_ASSERTION_SCOPE_MISMATCH')
            if fact['kind'] == 'individual':
                ei.identifier(fact['local_id'])
            elif fact['kind'] == 'class':
                ss.iri(fact['class_iri'])
            for key in ('subject', 'object'):
                if key in fact:
                    ref = fact[key]
                    ei.identifier(next(iter(ref.values())))
    return validated


def compile_semantics(snapshot, selection, policy):
    """Resolve only selected scoped facts; never infer event or local identity."""
    facts = selection['selected_semantic_facts']
    declarations = {(f['patient_id'], f['episode_id'], f['local_id'])
                    for f in facts if f['kind'] == 'individual'}
    def local(scope, name):
        ei.require((*scope, name) in declarations, 'MISSING_SELECTED_SEMANTIC_INDIVIDUAL')
        return str(bt.D['semantic/' + '/'.join((*scope, name))])

    def resolve(ref, scope):
        if 'local_id' in ref:
            return local(scope, ref['local_id'])
        eid = ref['event_id']
        ei.require(eid in snapshot.events, 'MISSING_SELECTED_SEMANTIC_EVENT')
        ei.require(bt.scope(snapshot.events[eid]) == scope, 'CROSS_SCOPE_SEMANTIC_REFERENCE')
        return snapshot.evidence[eid]['process']

    module = {'profile': ss.PROFILE, 'source_sha256': ei.digest(ei.canonical(snapshot.source)),
              **{key: es.copied(policy[key]) for key in ('classes', 'rules', 'disjoint')},
              'individuals': sorted(local((p, e), i) for p, e, i in declarations),
              'class_assertions': [], 'property_assertions': []}
    supports = defaultdict(list)
    for fact in facts:
        scope = bt.scope(fact)
        if fact['kind'] == 'individual':
            axiom = {'kind': 'NamedIndividual', 'individual': local(scope, fact['local_id'])}
        elif fact['kind'] == 'class':
            assertion = {'individual': resolve(fact['subject'], scope), 'class': fact['class_iri']}
            module['class_assertions'].append(assertion)
            axiom = {'kind': 'ClassAssertion', **assertion}
        else:
            assertion = {'subject': resolve(fact['subject'], scope), 'property': fact['property_iri'],
                         'object': resolve(fact['object'], scope)}
            module['property_assertions'].append(assertion)
            axiom = {'kind': 'ObjectPropertyAssertion', **assertion}
        supports[ei.canonical(axiom)].append({'fact_id': fact['id'],
            'support': selection['row_supports']['semantic_facts:' + fact['id']]})
    # Validation includes combined limits, the declared class vocabulary and
    # every emitted construct; an orphan fact is never silently discarded.
    ss.build_model(snapshot, module)
    return module, [{'axiom': json.loads(key), 'facts': value} for key, value in sorted(supports.items())]


def execute(archive, request, policy, query, *, timeout_seconds=20):
    archive, request, policy, query = (es.copied(v) for v in (archive, request, policy, query))
    validated = validate(archive, request, policy)
    # Validate query even when history is blocked. The vocabulary is the fixed
    # policy plus the built-in projection classes, not selected positive facts.
    classes = set(policy['classes']) | {str(ei.EX.PatientRole), str(ei.EX.Person)}
    for kind in bt.KINDS:
        classes.update(bt.selected_classes({'event_kind': kind}))
    ss.validate_query(query, classes)
    selection = es._select_validated(*validated, profile=PROFILE,
        row_kinds=(*es.ROW_KINDS, 'semantic_facts'), extra_artifacts=FILES)
    semantic_run, module, axiom_supports = None, None, []
    status = selection['status']
    validation = None
    if status == 'READY':
        snapshot = bt.prepare(selection['source'])
        try:
            module, axiom_supports = compile_semantics(snapshot, selection, policy)
        except ei.ContractError as error:
            status = 'INVALID_SELECTED_SEMANTICS'
            validation = {'reason': str(error)}
        else:
            semantic_run = ss.execute(snapshot, query, module, timeout_seconds=timeout_seconds)
            status = semantic_run['status']
    context = {'profile': PROFILE, 'selection_context_id': selection['context_id'],
               'semantic_context_id': semantic_run['context_id'] if semantic_run else None,
               'policy': policy, 'query': query, 'timeout_seconds': timeout_seconds,
               'semantic_policy': request['semantic_policy'],
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in FILES}}
    return {'profile': PROFILE, 'context_id': ei.digest(ei.canonical(context)), 'context': context,
            'status': status, 'selection': selection, 'semantic_module': module,
            'axiom_supports': axiom_supports, 'semantic_run': semantic_run, 'validation': validation,
            'matching': semantic_run['matching'] if semantic_run else None,
            'full_owl_mapping_verified': False, 'historical_ontology_replay': False,
            'availability_basis': 'caller_declared_source_availability_and_archive_coverage'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    example = ei.ROOT / 'examples/joint-evidence'
    for name in ('archive', 'request', 'policy', 'query'):
        parser.add_argument('--' + name, type=Path, default=example / (name + '.json'))
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/joint-evidence-run')
    parser.add_argument('--timeout-seconds', type=float, default=20)
    args = parser.parse_args()
    try:
        result = execute(*(json.loads(getattr(args, name).read_text()) for name in ('archive', 'request', 'policy', 'query')),
                         timeout_seconds=args.timeout_seconds)
    except (ei.ContractError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'output': str(args.output),
                      'certain_patient_ids': result['matching']['certain_patient_ids'] if result['matching'] else None}))
    if result['status'] not in ('READY', 'EMPTY_SELECTED_EVIDENCE'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
