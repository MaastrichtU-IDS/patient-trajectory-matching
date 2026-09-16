"""Verify measurement catalogue entries and supplied claims against pinned CSV bytes."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import zlib

from jsonschema import Draft202012Validator
from . import claim_rdf as cr, exact_intervals as ei, mimic_measurement_import as inputs
from . import reviewed_measurement_mappings as mappings

PROFILE = 'measurement-source-catalogue-1.0'
SCHEMA = ei.ROOT / 'schemas/measurement-source-catalogue.schema.json'
FILES = tuple(sorted(set(inputs.FILES + mappings.FILES + (
    'patterns/measurement_source_catalogue.py', 'schemas/measurement-source-catalogue.schema.json'))))


def _prepare(folder, request, claim_ids=()):
    request = deepcopy(request)
    claim_ids = set(claim_ids)
    ei.require(len(cr.canonical(request).encode()) <= cr.MAX_BYTES, 'CATALOGUE_REQUEST_LIMIT')
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)),
               'INVALID_MEASUREMENT_CATALOGUE_REQUEST')
    paths = inputs.paths_for(folder)
    rows, chart_manifest = inputs.read_observations(paths['chartevents'])
    dimensions = {t: inputs.source.read_table(paths[t], t) for t in ('icustays', 'd_items')}
    manifests = {'chartevents': chart_manifest, **{t: v[1] for t, v in dimensions.items()}}
    for table, expected in request['source_sha256'].items():
        ei.require(manifests[table]['file_sha256'] == expected, 'CATALOGUE_SOURCE_HASH_MISMATCH:' + table)
    stays = inputs.source.index_dimension(dimensions['icustays'][0], 'icustays', 'stay_id')
    items = inputs.source.index_dimension(dimensions['d_items'][0], 'd_items', 'itemid')
    admissions = {}
    for _, stay in stays.values():
        ei.require(admissions.setdefault(stay['hadm_id'], stay['subject_id']) == stay['subject_id'],
                   'CONFLICTING_ADMISSION_PATIENT')
    entries, evidence = [], []
    for code in sorted(request['itemids']):
        ei.require(code in items, 'CATALOGUE_UNKNOWN_ITEM')
        number, row = items[code]
        ei.require(row['linksto'] == 'chartevents', 'CATALOGUE_REQUIRES_CHART_ITEM')
        entries.append({'code': code, 'label': row['label'], 'source_class_iri': mappings.ITEM_NS + code})
        evidence.append({'code': code, 'dictionary_row': row,
                         'source': inputs.source.evidence('d_items', number, row, manifests['d_items'])})
    catalogue = {'profile': 'mapping-source-catalogue-1.0',
                 'dataset_id': 'dataset_' + cr.digest(request['dataset_id']), 'release': request['release'],
                 'source_kind': 'supplied_catalogue', 'entries': entries}
    mappings.mappings.validate('catalogue', catalogue)
    selected = set(request['itemids']); counts = {c: Counter() for c in sorted(selected)}
    all_counts, seen, origins, originals = Counter(), {}, {}, {}
    # Apply the importer's admission and exact-duplicate policy to all rows before
    # item filtering. This reproduces clocks even for stores taken from a subset.
    for number, row in enumerate(rows, 1):
        source = inputs.source.evidence('chartevents', number, row, chart_manifest)
        record_id = 'chartevents:' + request['dataset_id'] + ':' + chart_manifest['csv_sha256'] + ':' + str(number)
        prior = seen.setdefault(source['row_sha256'], record_id)
        outcome = 'DUPLICATE_ROW' if prior != record_id else inputs.admit(row, stays, items)[0]
        all_counts[outcome] += 1
        if row['itemid'] in selected: counts[row['itemid']][outcome] += 1
        if outcome != 'ADMITTED_MEASUREMENT': continue
        patient = row['subject_id']; order = (row['charttime'], record_id)
        if patient not in origins or order < origins[patient][0]:
            origins[patient] = (order, {'clock_id': 'patient_' + patient, 'scope': 'patient:' + patient,
                'origin': row['charttime'].replace(' ', 'T'), 'policy': inputs.local.POLICY,
                'origin_source_key': record_id})
        claim_id = 'claim_chart_' + source['csv_sha256'] + '_' + str(number)
        if row['itemid'] in selected and claim_id in claim_ids:
            claim = inputs.describe({'raw': row, 'source': source, 'record_id': record_id}, request['dataset_id'])
            originals[claim['id']] = claim
    context = {'profile': PROFILE, 'request': request, 'source_files': list(manifests.values()),
               'catalogue_sha256': cr.digest(catalogue),
               'origin_policy': 'minimum_admitted_charttime_per_patient_before_item_filter',
               'duplicate_policy': 'first_exact_source_row_only',
               'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    report = {'profile': PROFILE, 'status': 'VERIFIED_MEASUREMENT_SOURCE_CATALOGUE',
              'context': context, 'context_id': cr.digest(context), 'catalogue': catalogue,
              'dictionary_evidence': evidence, 'input_rows': len(rows),
              'admission_outcome_counts': dict(sorted(all_counts.items())),
              'selected_item_outcomes': {c: dict(sorted(v.items())) for c, v in counts.items()},
              'reconciliation_complete': sum(all_counts.values()) == len(rows),
              'catalogue_dictionary_fidelity_verified': True, 'clinical_mapping_verified': False,
              'source_publisher_authenticated': False, 'source_claims_accepted': 0,
              'mapped_query_executed': False, 'unit_conversion_performed': False}
    return report, originals, {p: v[1] for p, v in origins.items()}


def build(folder, request):
    """Aggregate catalogue evidence only; no patient records in the returned report."""
    return _prepare(folder, request)[0]


def audit_store(folder, request, catalogue, store):
    """Reproduce every supplied claim and clock. Subsets do not certify coverage."""
    catalogue, store = deepcopy((catalogue, store))
    inputs.claims.validate_store(store)
    report, originals, origins = _prepare(folder, request, (c['id'] for c in store['claims']))
    ei.require(catalogue == report['catalogue'], 'SOURCE_CATALOGUE_MISMATCH')
    ei.require(store['dataset_id'] == catalogue['dataset_id'], 'SOURCE_STORE_DATASET_MISMATCH')
    for claim in store['claims']:
        ei.require(claim['id'] in originals and claim == originals[claim['id']], 'SOURCE_MEASUREMENT_CLAIM_MISMATCH')
    patients = {claim['patient_id'] for claim in store['claims']}
    expected = sorted((origins[p] for p in patients), key=lambda c: c['clock_id'])
    ei.require(sorted(store['clocks'], key=lambda c: c['clock_id']) == expected, 'SOURCE_MEASUREMENT_CLOCK_MISMATCH')
    context = {'profile': PROFILE, 'catalogue_context_id': report['context_id'],
               'catalogue_sha256': cr.digest(catalogue), 'store_sha256': cr.digest(store)}
    return {'status': 'VERIFIED_MEASUREMENT_SOURCE_STORE', 'context': context, 'context_id': cr.digest(context),
            'catalogue_report': report, 'checked_claims': len(store['claims']),
            'measurement_claim_row_fidelity_verified': True, 'recorded_clock_origin_verified': True,
            'complete_source_coverage_verified': False, 'source_acceptance_verified': False,
            'clinical_mapping_verified': False, 'interval_fidelity_verified': False,
            'clock_alignment_verified': False, 'source_history_verified': False,
            'physical_elapsed_time_verified': False, 'unit_conversion_performed': False}


def execute(folder, request, catalogue, terminology, proposals, review, selector, interval_store,
            interval_policy, semantic_policy, measurement_store, measurement_policy, alignment, query,
            *, timeout_seconds=20):
    (request, catalogue, terminology, proposals, review, selector, interval_store, interval_policy,
     semantic_policy, measurement_store, measurement_policy, alignment, query) = deepcopy((
         request, catalogue, terminology, proposals, review, selector, interval_store, interval_policy,
         semantic_policy, measurement_store, measurement_policy, alignment, query))
    audit = audit_store(folder, request, catalogue, measurement_store)
    result = mappings.execute(catalogue, terminology, proposals, review, selector, interval_store,
                              interval_policy, semantic_policy, measurement_store, measurement_policy,
                              alignment, query, timeout_seconds=timeout_seconds)
    context = {'profile': PROFILE, 'source_audit_context_id': audit['context_id'],
               'query_context_id': result['context_id']}
    return {'profile': PROFILE, 'status': result['status'], 'context': context,
            'context_id': cr.digest(context), 'source_audit': audit, 'query_result': result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--catalogue', type=Path)
    parser.add_argument('--store', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv); temporary = None
    try:
        ei.require(bool(args.catalogue) == bool(args.store), 'AUDIT_REQUIRES_CATALOGUE_AND_STORE')
        documents = [p for p in (args.request, args.catalogue, args.store) if p is not None]
        protected = {p.resolve() for p in inputs.paths_for(args.input_dir).values()} | {p.resolve() for p in documents}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        for path in documents: ei.require(path.stat().st_size <= cr.MAX_BYTES, 'INPUT_FILE_LIMIT')
        values = [json.loads(p.read_text()) for p in documents]
        result = audit_store(args.input_dir, *values) if args.store else build(args.input_dir, values[0])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, RecursionError) as error:
        parser.exit(2, str(error) + '\n')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status': result['status'], 'context_id': result['context_id']}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
