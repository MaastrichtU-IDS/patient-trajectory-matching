"""Describe bounded MIMIC inputevents claims; never accept or query them."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import tempfile
import sys
import zlib

from jsonschema import Draft202012Validator
from . import claim_isolation
from . import claim_projection as cp
from . import claim_rdf as cr
from . import exact_intervals as ei
from . import local_claim_projection as projection
from . import mimic_inputevents as admission
from . import patient_local as local

PROFILE = 'mimic-claim-import-1.0'
SCHEMA = ei.ROOT / 'schemas/mimic-claim-import.schema.json'
ITEM_NS = 'https://example.org/trajectory/mimic-record-item/'
MAX_CLAIMS = 32
MAX_EPISODES = 256
FILES = tuple(sorted(set(('patterns/mimic_claim_import.py', 'patterns/mimic_inputevents.py',
    'schemas/mimic-claim-import.schema.json') + cp.FILES + projection.FILES)))
RESOURCE_ERRORS = {'CLAIM_DOCUMENT_LIMIT', 'CLAIM_STRING_LIMIT', 'CLAIM_TREE_LIMIT', 'CLAIM_TREE_LIMIT_OR_ALIAS',
                   'CLAIM_GRAPH_LIMIT', 'CLAIM_TOTAL_ROW_LIMIT'}


def validate_request(request):
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)),
               'INVALID_MIMIC_CLAIM_IMPORT_REQUEST')
    return request


def source_paths(input_dir):
    paths = {}
    for table in admission.HEADERS:
        existing = [Path(input_dir) / (table + suffix) for suffix in ('.csv', '.csv.gz')
                    if (Path(input_dir) / (table + suffix)).is_file()]
        ei.require(len(existing) == 1, 'MISSING_OR_AMBIGUOUS_TABLE:' + table)
        paths[table] = existing[0]
    return paths


def describe(segment, dataset_id):
    """A JSON description only: IRI-valued fields remain quoted string values."""
    source = segment['source']
    rid = 'row_' + source['csv_sha256'] + '_' + str(source['record_number'])
    eid = 'input_' + source['csv_sha256'] + '_' + str(source['record_number'])
    scope = {'patient_id': segment['patient_id'], 'episode_id': segment['icu_stay_id']}
    variables = [{'id': eid + '_' + side, **scope,
                  'clock_id': 'patient_' + segment['patient_id'],
                  'local_lower': segment[side]['raw_value'], 'local_upper': segment[side]['raw_value'],
                  'source_key': segment['id'] + ':' + side + ':recorded-label-exact'}
                 for side in ('start', 'end')]
    return {'id': 'claim_' + eid, **scope,
        'source_id': 'inputevents_' + cr.digest(dataset_id), 'source_record_id': rid,
        'source_sha256': source['file_sha256'],
        'bundle': {'variables': variables, 'constraints': [],
            'events': [{'id': eid, 'record_id': rid, **scope,
                'event_kind': 'recorded_input_segment', 'status': 'recorded',
                'start_var': eid + '_start', 'end_var': eid + '_end', 'source_key': segment['id']}],
            'semantic_facts': [{'id': eid + '_item', **scope, 'kind': 'class',
                'subject': {'event_id': eid}, 'class_iri': ITEM_NS + segment['item']['itemid']}]}}


def run(input_dir, request):
    validate_request(request)
    request = json.loads(cr.canonical(request))
    paths = source_paths(input_dir)
    staged = admission.stage(**paths, dataset_id=request['dataset_id'])
    manifests = {f['table']: f for f in staged['summary']['context']['files']}
    dimensions = {}
    for table in ('icustays', 'd_items'):
        rows, manifest = admission.read_table(paths[table], table)
        ei.require(manifest == manifests[table], 'SOURCE_CHANGED_DURING_RUN:' + table)
        dimensions[table] = rows
    items = {r['itemid']: r for r in dimensions['d_items']}
    selected_items = set(request['itemids'])
    ei.require(all(i in items and items[i]['linksto'] == 'inputevents' for i in selected_items),
               'UNKNOWN_INPUT_ITEM_SELECTOR')
    all_patients = {r['subject_id'] for r in dimensions['icustays']}
    patients = all_patients if request['patient_ids'] == 'all' else set(request['patient_ids'])
    ei.require(patients <= all_patients, 'UNKNOWN_REQUESTED_PATIENT')
    roster = sorted((r for r in dimensions['icustays'] if r['subject_id'] in patients),
                    key=lambda r: (r['subject_id'], r['stay_id']))
    ei.require(len(roster) <= MAX_EPISODES, 'EPISODE_LIMIT')
    # Schema-compatible, deterministic namespace; preserve the original dataset label in context.
    dataset_namespace = 'dataset_' + cr.digest(request['dataset_id'])
    context = {'profile': PROFILE, 'request': request, 'admission_context_id': staged['summary']['context_id'],
        'source_files': staged['summary']['context']['files'], 'dataset_namespace': dataset_namespace,
        'origin_policy': 'minimum_admitted_start_per_patient_before_item_filter',
        'time_semantics': 'recorded_source_intervals', 'time_domain': local.TIME_DOMAIN,
        'item_mapping': 'literal_source_item_code_class_only', 'item_namespace': ITEM_NS,
        'acceptance': 'no_decisions_all_claims_pending', 'history_mode': 'retrospective_source_records',
        'limits': {'claims_per_stay': MAX_CLAIMS, 'icu_stays': MAX_EPISODES},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    context_id = cr.digest(context)
    semantic_policy = {'profile': 'joint-semantic-policy-1.0', 'id': 'source_item_codes',
        'classes': sorted(ITEM_NS + i for i in selected_items), 'rules': [], 'disjoint': []}
    origins, groups = {}, defaultdict(list)
    for segment in sorted(staged['segments'], key=lambda s: (s['start']['raw_value'], s['id'])):
        patient = segment['patient_id']
        if patient not in patients: continue
        origins.setdefault(patient, {'label': segment['start']['raw_value'], 'source_record_id': segment['id'],
                                     'source': segment['source']})
        if segment['item']['itemid'] in selected_items:
            groups[(patient, segment['icu_stay_id'])].append(segment)
    episodes, record_links = [], {}
    for stay in roster:
        patient, episode_id = stay['subject_id'], stay['stay_id']
        segments = sorted(groups[(patient, episode_id)], key=lambda s: s['source']['record_number'])
        episode = {'patient_id': patient, 'episode_id': episode_id, 'selected_records': len(segments),
            'status': 'NO_SELECTED_ADMITTED_RECORDS', 'reason': None, 'store': None,
            'store_sha256': None, 'acceptance_policy': None, 'isolation': None, 'claim_provenance': {}}
        if segments:
            if len(segments) > MAX_CLAIMS:
                episode.update(status='BLOCKED_RESOURCE_LIMIT', reason='CLAIMS_PER_STAY_LIMIT')
            else:
                origin = origins[patient]
                store = {'profile': projection.RECORD_STORE_PROFILE, 'dataset_id': dataset_namespace,
                    'snapshot_id': 'import_' + cr.digest([context_id, patient, episode_id]),
                    'clocks': [{'clock_id': 'patient_' + patient, 'scope': 'patient:' + patient,
                        'origin': origin['label'].replace(' ', 'T'), 'policy': local.POLICY,
                        'origin_source_key': origin['source_record_id']}],
                    'claims': [describe(s, request['dataset_id']) for s in segments]}
                try:
                    projection.validate_store(store)
                    certificate = claim_isolation.check(cr.encode(store, fields=cr.LOCAL_FIELDS), local=True)
                except ei.ContractError as error:
                    if str(error) not in RESOURCE_ERRORS: raise
                    episode.update(status='BLOCKED_RESOURCE_LIMIT', reason=str(error))
                else:
                    policy = {'profile': cp.POLICY_PROFILE, 'id': 'pending_' + cr.digest(store),
                        'store_sha256': cr.digest(store), 'semantic_policy_sha256': cr.digest(semantic_policy),
                        'decisions': []}
                    cp.validate_policy(store, policy, semantic_policy)
                    episode.update(status='PENDING_CLAIMS', store=store, store_sha256=cr.digest(store),
                                   acceptance_policy=policy, isolation=certificate)
                    for claim, segment in zip(store['claims'], segments):
                        episode['claim_provenance'][claim['id']] = {
                            'claim_sha256': cr.digest(claim), 'source_record_id': segment['id'],
                            'source': segment['source'], 'item_source': segment['item_source'],
                            'stay_source': segment['stay_source'], 'origin_source': origin['source'],
                            'origin_source_record_id': origin['source_record_id'],
                            'recorded_at': segment['recorded_at'], 'source_available_at': None,
                            'source_history_verified': False, 'clinical_mapping_verified': False}
                        record_links[segment['id']] = {'claim_id': claim['id'], 'store_sha256': cr.digest(store)}
        episodes.append(episode)
    episode_map = {(e['patient_id'], e['episode_id']): e for e in episodes}
    ledger = []
    for entry in staged['reconciliation']:
        raw = entry['raw']; link = record_links.get(entry['record_id'])
        if entry['outcome'] != 'STAGED_SEGMENT': outcome = 'NOT_ADMITTED'
        elif raw['subject_id'] not in patients: outcome = 'OUTSIDE_PATIENT_SCOPE'
        elif raw['itemid'] not in selected_items: outcome = 'OUTSIDE_ITEM_SCOPE'
        else: outcome = 'PENDING_CLAIM' if link else 'BLOCKED_RESOURCE_LIMIT'
        ledger.append({'record_id': entry['record_id'], 'source': entry['source'],
            'admission_outcome': entry['outcome'], 'admission_reasons': entry['reasons'],
            'duplicate_of': entry['duplicate_of'], 'import_outcome': outcome,
            'claim_id': link['claim_id'] if link else None,
            'store_sha256': link['store_sha256'] if link else None,
            'reason': episode_map[(raw['subject_id'], raw['stay_id'])]['reason']
                      if outcome == 'BLOCKED_RESOURCE_LIMIT' else None})
    counts = dict(sorted(Counter(r['import_outcome'] for r in ledger).items()))
    blocked = sum(e['status'] == 'BLOCKED_RESOURCE_LIMIT' for e in episodes)
    summary = {'profile': PROFILE, 'context': context, 'context_id': context_id,
        'status': 'BLOCKED_INCOMPLETE_IMPORT' if blocked else 'COMPLETED_PENDING_IMPORT',
        'input_rows': len(ledger), 'import_outcome_counts': counts,
        'reconciliation_complete': len(ledger) == staged['summary']['input_rows'],
        'selected_records': sum(len(s) for s in groups.values()),
        'described_claims': len(record_links), 'blocked_stays': blocked,
        'description_complete_over_selected_records': blocked == 0,
        'represented_patients': len(patients), 'represented_icu_stays': len(roster),
        'episode_outcome_counts': dict(sorted(Counter(e['status'] for e in episodes).items())),
        'accepted_claims': 0, 'matching': None, 'clinical_mapping_verified': False,
        'clinical_knowledge_status': 'UNKNOWN', 'source_history_verified': False,
        'physical_elapsed_time_verified': False, 'source_provenance_basis': 'computed_from_read_file_bytes'}
    return {'summary': summary, 'semantic_policy': semantic_policy, 'episodes': episodes,
            'origins': origins, 'import_reconciliation': ledger, 'admission': staged}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ei.ROOT / 'examples/mimic-inputevents')
    parser.add_argument('--request', type=Path, default=ei.ROOT / 'examples/mimic-claim-import/synthetic-request.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/mimic-claim-import-run/result.json')
    args = parser.parse_args(argv)
    temporary = None
    try:
        protected = {p.resolve() for p in source_paths(args.input_dir).values()} | {args.request.resolve()}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size <= 256 * 1024, 'REQUEST_SIZE_LIMIT')
        result = run(args.input_dir, json.loads(args.request.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, indent=2, ensure_ascii=False); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, RecursionError) as error:
        print(json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}), file=sys.stderr)
        return 2
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({k: v for k, v in result['summary'].items() if k != 'context'}, sort_keys=True))
    return 0 if result['summary']['status'] == 'COMPLETED_PENDING_IMPORT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
