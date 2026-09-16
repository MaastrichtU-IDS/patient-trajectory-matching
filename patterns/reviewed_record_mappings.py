"""Compile explicitly reviewed record-class mappings into bounded semantic rules."""
import argparse
from copy import deepcopy
import json
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator
from . import claim_rdf as cr, exact_intervals as ei, mixed_record_query as mixed
from . import joint_evidence as joint, semantic_support as semantic

PROFILE = 'reviewed-record-mappings-1.0'
NAMES = ('catalogue', 'terminology', 'mappings', 'review')
FILES = tuple(sorted(set(mixed.FILES + (
    'patterns/reviewed_record_mappings.py', 'schemas/reviewed-record-mappings.schema.json'))))


def validate(kind, value):
    ei.require(len(cr.canonical(value).encode()) <= cr.MAX_BYTES, 'MAPPING_DOCUMENT_LIMIT')
    schema = json.loads((ei.ROOT / 'schemas/reviewed-record-mappings.schema.json').read_text())
    schema = {**schema, 'oneOf': [{'$ref': '#/$defs/' + kind}]}
    ei.require(not list(Draft202012Validator(schema).iter_errors(value)), 'INVALID_MAPPING_' + kind.upper())


def unique(rows, key):
    result = {r[key]: r for r in rows}
    ei.require(len(result) == len(rows), 'DUPLICATE_MAPPING_' + key.upper())
    return result


def compile_policy(catalogue, terminology, mappings, review):
    documents = deepcopy((catalogue, terminology, mappings, review))
    for kind, value in zip(NAMES, documents): validate(kind, value)
    catalogue, terminology, mappings, review = documents
    ei.require(mappings['catalogue_sha256'] == cr.digest(catalogue), 'STALE_SOURCE_CATALOGUE')
    ei.require(mappings['terminology_sha256'] == cr.digest(terminology), 'STALE_TERMINOLOGY')
    ei.require(review['mappings_sha256'] == cr.digest(mappings), 'STALE_MAPPING_REVIEW')
    sources = unique(catalogue['entries'], 'code'); terms = unique(terminology['terms'], 'code')
    entries = unique(mappings['entries'], 'id'); unique(mappings['entries'], 'source_code')
    source_classes = {r['source_class_iri'] for r in sources.values()}
    target_classes = {r['record_class_iri'] for r in terms.values()}
    concepts = {r['concept_iri'] for r in terms.values()}
    ei.require(len(source_classes) == len(sources) and len(target_classes) == len(terms) and len(concepts) == len(terms), 'AMBIGUOUS_MAPPING_CLASS')
    ei.require(not (source_classes & target_classes or concepts & (source_classes | target_classes)), 'CONCEPT_RECORD_CLASS_COLLISION')
    for iri in source_classes | target_classes | concepts | {terminology['system'], terminology['record_class_namespace']}:
        semantic.iri(iri)
    for iri in source_classes | target_classes:
        ei.require(not iri.startswith(('https://w3id.org/sulo/', 'http://www.w3.org/', 'https://www.w3.org/')), 'RESERVED_RECORD_CLASS')
    for term in terms.values():
        ei.require(term['record_class_iri'] == terminology['record_class_namespace'] + term['code'], 'RECORD_CLASS_NAMESPACE_MISMATCH')
    ei.require({r['source_code'] for r in entries.values()} == set(sources), 'INCOMPLETE_CATALOGUE_MAPPING')
    for row in entries.values(): ei.require(row['target_code'] in terms, 'UNKNOWN_TARGET_TERM')
    decisions = {}; latest = {}; children = set()
    for decision in review['decisions']:
        mid = decision['mapping_id']; parent = decision['supersedes']
        ei.require(decision['id'] not in decisions, 'DUPLICATE_REVIEW_DECISION')
        ei.require(mid in entries and decision['mapping_sha256'] == cr.digest(entries[mid]), 'STALE_OR_UNKNOWN_MAPPING_DECISION')
        if parent is None:
            ei.require(mid not in latest and decision['action'] == 'accept', 'INVALID_REVIEW_ROOT')
        else:
            ei.require(parent in decisions and decisions[parent]['mapping_id'] == mid and parent == latest.get(mid) and parent not in children, 'INVALID_REVIEW_CHAIN')
            children.add(parent)
        decisions[decision['id']] = decision; latest[mid] = decision['id']
    outcomes = []
    for mid in sorted(entries):
        decision = decisions.get(latest.get(mid))
        outcomes.append({'mapping_id': mid, 'mapping_sha256': cr.digest(entries[mid]),
                         'status': 'PENDING' if decision is None else 'ACCEPTED' if decision['action'] == 'accept' else 'WITHDRAWN',
                         'latest_decision': deepcopy(decision)})
    context = {'profile': PROFILE, 'input_sha256': {k: cr.digest(v) for k, v in zip(NAMES, documents)},
               'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES},
               'scope': 'one_way_record_class_implications_over_supplied_catalogue',
               'clinical_mapping_verified': False, 'reviewer_identity_verified': False,
               'catalogue_source_fidelity_verified': False, 'historical_replay_verified': False}
    result = {'profile': PROFILE, 'context': context, 'context_id': cr.digest(context),
              'outcomes': outcomes, 'semantic_policy': None, 'rule_evidence': []}
    if any(row['status'] != 'ACCEPTED' for row in outcomes):
        result['status'] = 'BLOCKED_MAPPING_REVIEW'; return result
    rules = []
    for row in sorted(entries.values(), key=lambda r: r['id']):
        source = sources[row['source_code']]; target = terms[row['target_code']]
        rule = {'id': 'mapping_' + row['id'], 'if': {'class': source['source_class_iri']}, 'then': target['record_class_iri']}
        rules.append(rule)
        result['rule_evidence'].append({'rule_id': rule['id'], 'mapping_id': row['id'], 'mapping_sha256': cr.digest(row),
                                       'source': source, 'target': target, 'decision_id': latest[row['id']]})
    policy = {'profile': 'joint-semantic-policy-1.0', 'id': 'mapping_' + result['context_id'],
              'classes': sorted(source_classes | target_classes), 'rules': rules, 'disjoint': []}
    joint.validate_policy(policy)
    result.update(status='READY_REVIEWED_RECORD_MAPPING', semantic_policy=policy)
    return result


def execute(catalogue, terminology, mappings, review, interval_store, interval_policy,
            measurement_store, measurement_policy, alignment, query):
    compiled = compile_policy(catalogue, terminology, mappings, review)
    if compiled['semantic_policy'] is None:
        return {'status': 'BLOCKED_MAPPING_REVIEW', 'mapping': compiled, 'query_result': None}
    ei.require(interval_store['dataset_id'] == catalogue['dataset_id'], 'MAPPING_DATASET_MISMATCH')
    source_classes = {row['source_class_iri'] for row in catalogue['entries']}
    target_classes = {row['record_class_iri'] for row in terminology['terms']}
    ei.require(query['treatment_class_iri'] in target_classes, 'QUERY_REQUIRES_RECORD_SELECTOR')
    mixed.intervals.validate_store(interval_store)
    for claim in interval_store['claims']:
        facts = claim['bundle']['semantic_facts']; events = claim['bundle']['events']
        event_ids = {e['id'] for e in events}
        ei.require(all(f['kind'] == 'class' and set(f['subject']) == {'event_id'} and
                       f['subject']['event_id'] in event_ids and f['class_iri'] in source_classes
                       for f in facts), 'UNSUPPORTED_MAPPED_SOURCE_FACT')
        ei.require(all(sum(f['subject']['event_id'] == e['id'] for f in facts) == 1 for e in events), 'AMBIGUOUS_OR_MISSING_SOURCE_ITEM')
    # The caller must supply a policy bound to this compiled semantic policy.
    # Mapping review never edits or recreates source-claim acceptance decisions.
    result = mixed.execute(interval_store, interval_policy, compiled['semantic_policy'],
                           measurement_store, measurement_policy, alignment, query)
    return {'status': result['status'], 'mapping': compiled, 'query_result': result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    folder = ei.ROOT / 'examples/reviewed-record-mappings'
    for name in NAMES: parser.add_argument('--' + name, type=Path, default=folder / (name + '.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    paths = [getattr(args, name) for name in NAMES]
    temporary = None
    try:
        ei.require(args.output.resolve() not in {p.resolve() for p in paths}, 'OUTPUT_OVERWRITES_INPUT')
        for path in paths: ei.require(path.stat().st_size <= cr.MAX_BYTES, 'MAPPING_DOCUMENT_LIMIT')
        result = compile_policy(*(json.loads(p.read_text()) for p in paths))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + '\n')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status': result['status'], 'context_id': result['context_id']}))
    return 0 if result['semantic_policy'] is not None else 2


if __name__ == '__main__': raise SystemExit(main())
