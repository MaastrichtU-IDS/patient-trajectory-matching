"""Explicitly accept structured claims into a bounded analysis assertion view."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from jsonschema import Draft202012Validator
from rdflib import Graph, RDF, URIRef
from . import bounded_intervals as bt
from . import exact_intervals as ei
from . import joint_evidence as joint
from . import semantic_support as ss
from . import claim_rdf as cr
from . import claim_isolation

PROFILE = 'claim-projection-1.0'
STORE_PROFILE = 'claim-store-1.0'
POLICY_PROFILE = 'claim-acceptance-policy-1.0'
SCHEMA = ei.ROOT / 'schemas/claim-store.schema.json'
ROW_KINDS = ('variables', 'events', 'constraints', 'semantic_facts')
FILES = ('patterns/claim_projection.py', 'patterns/claim_rdf.py', 'patterns/claim_isolation.py',
         'schemas/claim-store.schema.json', 'ontology/claim-description-profile.ttl',
         'ontology/vendor/sulo-0.2.14.ttl', 'patterns/joint_evidence.py',
         'schemas/joint-evidence.schema.json', 'patterns/bounded_intervals.py',
         'schemas/bounded-interval.schema.json', 'patterns/requirements.lock.txt') + ss.FILES


def validate_store(store):
    # Finite JSON tree, before recursive schema/graph work. No arbitrary Python inputs.
    def bounded(value, depth=0):
        ei.require(depth <= 16, 'CLAIM_JSON_DEPTH')
        if type(value) is dict:
            ei.require(all(type(k) is str for k in value), 'CLAIM_JSON_KEY')
            for item in value.values(): bounded(item, depth + 1)
        elif type(value) is list:
            ei.require(len(value) <= 256, 'CLAIM_ROW_LIMIT')
            for item in value: bounded(item, depth + 1)
        else:
            ei.require(type(value) in (int, str), 'CLAIM_JSON_VALUE')
            if type(value) is str: ei.require(len(value) <= 2048, 'CLAIM_STRING_LIMIT')
            else: ei.checked_us(value)
    bounded(store)
    ei.require(len(cr.canonical(store).encode()) <= cr.MAX_BYTES, 'CLAIM_DOCUMENT_LIMIT')
    schema = json.loads(SCHEMA.read_text())
    ei.require(not list(Draft202012Validator(schema).iter_errors(store)), 'INVALID_CLAIM_STORE_SCHEMA')
    claims = bt.unique(store['claims'], 'id')
    clocks = bt.unique(store['clocks'], 'clock_id')
    for c in clocks.values():
        ei.normalize(c['origin'], c['origin'])
        ei.require(c['scope'] == 'global' or (c['scope'].startswith('patient:') and
            c['scope'][8:] in {a['patient_id'] for a in claims.values()}), 'INVALID_CLAIM_CLOCK_SCOPE')
    total = 0
    for claim in claims.values():
        ei.require(any(claim['bundle'].values()), 'EMPTY_CLAIM_BUNDLE')
        for kind in ROW_KINDS:
            rows = claim['bundle'][kind]; total += len(rows)
            bt.unique(rows, 'id')
            for row in rows:
                if kind != 'constraints':
                    ei.require(bt.scope(row) == bt.scope(claim), 'CLAIM_SCOPE_MISMATCH')
                if kind == 'variables':
                    ei.require(row['lower_us'] <= row['upper_us'], 'REVERSED_CLAIM_BOUNDS')
                    ei.require(row['clock_id'] in clocks, 'UNKNOWN_CLAIM_CLOCK')
                    ei.require(clocks[row['clock_id']]['scope'] in ('global', 'patient:' + claim['patient_id']),
                               'CLAIM_CLOCK_SCOPE_MISMATCH')
                if kind == 'semantic_facts' and row['kind'] == 'class': ss.iri(row['class_iri'])
    ei.require(total <= 512, 'CLAIM_TOTAL_ROW_LIMIT')
    return store


def validate_policy(store, policy, semantic_policy):
    schema = json.loads(SCHEMA.read_text())['$defs']['policy']
    ei.require(not list(Draft202012Validator(schema).iter_errors(policy)), 'INVALID_CLAIM_POLICY_SCHEMA')
    joint.validate_policy(semantic_policy)
    ei.require(policy['store_sha256'] == cr.digest(store), 'STALE_CLAIM_STORE')
    ei.require(policy['semantic_policy_sha256'] == cr.digest(semantic_policy), 'STALE_CLAIM_SEMANTIC_POLICY')
    claims = bt.unique(store['claims'], 'id'); decisions = bt.unique(policy['decisions'], 'id')
    children = defaultdict(list); roots = defaultdict(list)
    for decision in decisions.values():
        cid = decision['claim_id']
        ei.require(cid in claims, 'UNKNOWN_DECISION_CLAIM')
        claim = claims[cid]
        ei.require(decision['claim_sha256'] == cr.digest(claim), 'STALE_DECISION_CLAIM')
        key = (claim['source_id'], claim['source_record_id'])
        parent = decision['supersedes']
        if parent is None:
            roots[key].append(decision['id'])
            ei.require(decision['action'] != 'withdraw', 'WITHDRAWAL_WITHOUT_PARENT')
        else:
            ei.require(parent in decisions and parent != decision['id'], 'INVALID_DECISION_PARENT')
            old = claims.get(decisions[parent]['claim_id'])
            ei.require(old is not None and (old['source_id'], old['source_record_id']) == key
                and bt.scope(old) == bt.scope(claim), 'CROSS_SOURCE_OR_SCOPE_DECISION')
            if decision['action'] == 'withdraw':
                ei.require(decision['claim_id'] == decisions[parent]['claim_id'], 'WITHDRAWAL_CLAIM_MISMATCH')
            children[parent].append(decision['id'])
    ei.require(all(len(v) == 1 for v in roots.values()), 'MULTIPLE_DECISION_ROOTS')
    ei.require(all(len(v) == 1 for v in children.values()), 'DECISION_FORK')
    done = set()
    for start in decisions:
        seen = set(); current = start
        while current is not None and current not in done:
            ei.require(current not in seen, 'DECISION_CYCLE')
            seen.add(current); current = decisions[current]['supersedes']
        done.update(seen)
    return claims, decisions, children


def select(store, policy, semantic_policy):
    validate_store(store)
    claims, decisions, children = validate_policy(store, policy, semantic_policy)
    accepted = {d['claim_id']: d['id'] for d in decisions.values()
                if d['id'] not in children and d['action'] == 'accept'}
    decisions_report = [{'decision_id': d['id'], 'claim_id': d['claim_id'],
        'state': 'SUPERSEDED' if d['id'] in children else d['action'].upper(), 'reason': d['reason']}
        for d in sorted(decisions.values(), key=lambda d: d['id'])]
    claim_report = [{'claim_id': cid, 'state': 'ACCEPTED' if cid in accepted else
        ('NOT_SELECTED' if any(d['claim_id'] == cid for d in decisions.values()) else 'PENDING'),
        'claim_sha256': cr.digest(claim)} for cid, claim in sorted(claims.items())]
    variants = defaultdict(dict)
    for cid, did in sorted(accepted.items()):
        claim = claims[cid]
        for kind in ROW_KINDS:
            for row in claim['bundle'][kind]:
                key = kind + ':' + row['id']
                item = variants[key].setdefault(cr.canonical(row), {'row': row, 'support': []})
                item['support'].append({'assertion_id': cid, 'support_ids': [did],
                    'patient_id': claim['patient_id'], 'episode_id': claim['episode_id'],
                    'source_id': claim['source_id'], 'source_record_id': claim['source_record_id'],
                    'source_sha256': claim['source_sha256'], 'claim_sha256': cr.digest(claim)})
    selected = {kind: [] for kind in ROW_KINDS}; supports = {}; blockers = []
    for key, choices in sorted(variants.items()):
        if len(choices) != 1:
            blockers.append({'reason': 'CONFLICTING_ACCEPTED_ROW', 'row': key,
                             'variants': list(choices.values())}); continue
        item = next(iter(choices.values())); supports[key] = item['support']
        selected[key.split(':')[0]].append(item['row'])
    variables = {v['id']: v for v in selected['variables']}
    for c in selected['constraints']:
        for support in supports['constraints:' + c['id']]:
            for field in ('left_var', 'right_var'):
                if c[field] in variables and bt.scope(variables[c[field]]) != bt.scope(support):
                    blockers.append({'reason': 'CLAIM_CONSTRAINT_SCOPE', 'constraint': c['id']})
    return {'accepted_claim_ids': sorted(accepted), 'decisions': decisions_report, 'claims': claim_report,
            'row_supports': supports, 'selected': selected, 'blockers': blockers}


def execute(store, policy, semantic_policy, query, *, timeout_seconds=20):
    ei.require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 60, 'INVALID_BACKEND_TIMEOUT')
    # Bound JSON nesting before copying; JSON and RDF recover the same contract.
    if isinstance(store, Graph): store = cr.decode(store)
    validate_store(store)
    store, policy, semantic_policy, query = deepcopy((store, policy, semantic_policy, query))
    selection = select(store, policy, semantic_policy)
    classes = set(semantic_policy['classes']) | {str(ei.EX.PatientRole), str(ei.EX.Person)}
    for kind in bt.KINDS: classes.update(bt.selected_classes({'event_kind': kind}))
    ss.validate_query(query, classes)
    claim_graph = cr.encode(store)
    isolation = claim_isolation.check(claim_graph)
    context = {'profile': PROFILE, 'store_sha256': cr.digest(store), 'policy': policy,
        'semantic_policy': semantic_policy, 'query': query, 'timeout_seconds': timeout_seconds,
        'interpretation': 'caller_accepted_assertions_for_analysis',
        'history_mode': 'explicit_policy_revision_order_not_source_time',
        'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in FILES}}
    context_id = cr.digest(context)
    result = {'profile': PROFILE, 'context_id': context_id, 'context': context,
        'selection': selection, 'claim_graph_turtle': claim_graph.serialize(format='turtle'),
        'claim_isolation': isolation, 'accepted_graph_turtle': None, 'source': None,
        'semantic_module': None, 'semantic_run': None, 'axiom_supports': [], 'matching': None,
        'clinical_mapping_verified': False, 'clinical_knowledge_status': 'UNKNOWN',
        'source_history_verified': False, 'accepted_view_full_owl_verified': False,
        'source_provenance_basis': 'caller_supplied_hashes_not_original_source_verification'}
    if selection['blockers']:
        result['status'] = 'BLOCKED_ACCEPTED_CONFLICT'; return result
    if not any(selection['selected'].values()):
        result['status'] = 'EMPTY_ACCEPTED_VIEW'; return result
    source = {'profile': bt.PROFILE_ID, 'dataset_id': store['dataset_id'],
              'snapshot_id': 'accepted_' + context_id, 'clocks': deepcopy(store['clocks']),
              **{k: selection['selected'][k] for k in ('events', 'variables', 'constraints')}}
    try:
        snapshot = bt.prepare(source)
        ei.require(len(snapshot.events) <= 30, 'CLAIM_ACCEPTED_EVENT_LIMIT')
        evidence = {'selected_semantic_facts': selection['selected']['semantic_facts'],
                    'row_supports': selection['row_supports']}
        module, axiom_supports = joint.compile_semantics(snapshot, evidence, semantic_policy)
        # Restrict multiplicative work before temporal enumeration; no truncation.
        candidates = len(snapshot.events) ** len(query['slots'])
        ei.require(candidates <= 20000, 'CLAIM_CANDIDATE_LIMIT')
    except bt.InconsistentSource as error:
        result.update(status='INCONSISTENT_ACCEPTED_TIME', validation=error.details); return result
    except ei.ContractError as error:
        result.update(status='INVALID_ACCEPTED_VIEW', validation={'reason': str(error)}); return result
    run = ss.execute(snapshot, query, module, timeout_seconds=timeout_seconds)
    result.update(status=run['status'], semantic_run=run)
    if run['status'] != 'READY': return result
    accepted = Graph()
    for triple in snapshot.graph: accepted.add(triple)
    for fact in module['class_assertions']:
        accepted.add((URIRef(fact['individual']), RDF.type, URIRef(fact['class'])))
    for fact in module['property_assertions']:
        accepted.add((URIRef(fact['subject']), URIRef(fact['property']), URIRef(fact['object'])))
    result.update(source=source, semantic_module=module, axiom_supports=axiom_supports,
                  matching=run['matching'], accepted_graph_turtle=accepted.serialize(format='turtle'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    example = ei.ROOT / 'examples/claim-projection'
    parser.add_argument('--store', type=Path, default=example / 'store.json')
    parser.add_argument('--graph', type=Path, help='Read a closed claim-description Turtle graph instead of --store')
    for name in ('policy', 'semantic-policy', 'query'):
        parser.add_argument('--' + name, type=Path, default=example / (name + '.json'))
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/claim-projection-run/result.json')
    args = parser.parse_args()
    try:
        inputs = [args.graph or args.store, args.policy, args.semantic_policy, args.query]
        ei.require(args.output.resolve() not in {p.resolve() for p in inputs}, 'OUTPUT_OVERWRITES_INPUT')
        for p in inputs: ei.require(p.stat().st_size <= (4 * 1024 * 1024 if p == args.graph else cr.MAX_BYTES), 'INPUT_FILE_LIMIT')
        store = Graph().parse(args.graph, format='turtle') if args.graph else json.loads(args.store.read_text())
        result = execute(store, *(json.loads(p.read_text()) for p in inputs[1:]))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent, delete=False) as out:
            tmp = Path(out.name)
            try:
                out.write(json.dumps(result, indent=2) + '\n'); out.flush()
                tmp.replace(args.output)
            finally:
                tmp.unlink(missing_ok=True)
    except (ei.ContractError, OSError, ValueError, SyntaxError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    print(json.dumps({'status': result['status'], 'accepted_claim_ids': result['selection']['accepted_claim_ids'],
                      'claim_isolation': result['claim_isolation']['status'], 'output': str(args.output)}))
    if result['status'] not in ('READY', 'EMPTY_ACCEPTED_VIEW'): raise SystemExit(2)


if __name__ == '__main__':
    main()
