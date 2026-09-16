"""Reproduce mapping source classes and interval claims from pinned MIMIC CSV bytes."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import zlib

from jsonschema import Draft202012Validator
from . import claim_rdf as cr, exact_intervals as ei, mimic_claim_import as inputs
from . import reviewed_record_mappings as mappings

PROFILE = 'source-record-catalogue-1.0'
SCHEMA = ei.ROOT / 'schemas/source-record-catalogue.schema.json'
FILES = tuple(sorted(set(inputs.FILES + mappings.FILES + (
    'patterns/source_record_catalogue.py', 'schemas/source-record-catalogue.schema.json'))))


def _prepare(folder, request):
    request = deepcopy(request)
    ei.require(len(cr.canonical(request).encode()) <= cr.MAX_BYTES, 'CATALOGUE_REQUEST_LIMIT')
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)),
               'INVALID_SOURCE_CATALOGUE_REQUEST')
    paths = inputs.source_paths(folder)
    staged = inputs.admission.stage(**paths, dataset_id=request['dataset_id'])
    manifests = {f['table']: f for f in staged['summary']['context']['files']}
    for table, expected in request['source_sha256'].items():
        ei.require(manifests[table]['file_sha256'] == expected, 'CATALOGUE_SOURCE_HASH_MISMATCH:' + table)
    rows, manifest = inputs.admission.read_table(paths['d_items'], 'd_items')
    ei.require(manifest == manifests['d_items'], 'CATALOGUE_DICTIONARY_CHANGED')
    dictionary = inputs.admission.index_dimension(rows, 'd_items', 'itemid')
    entries, evidence = [], []
    for code in sorted(request['itemids']):
        ei.require(code in dictionary, 'CATALOGUE_UNKNOWN_ITEM')
        number, row = dictionary[code]
        ei.require(row['linksto'] == 'inputevents', 'CATALOGUE_REQUIRES_INPUT_ITEM')
        entry = {'code': code, 'label': row['label'], 'source_class_iri': inputs.ITEM_NS + code}
        entries.append(entry)
        evidence.append({'code': code, 'dictionary_row': row,
                         'source': inputs.admission.evidence('d_items', number, row, manifest)})
    catalogue = {'profile': 'mapping-source-catalogue-1.0',
                 'dataset_id': 'dataset_' + cr.digest(request['dataset_id']),
                 'release': request['release'], 'source_kind': 'supplied_catalogue', 'entries': entries}
    mappings.validate('catalogue', catalogue)
    selected = set(request['itemids'])
    counts = {code: Counter() for code in sorted(selected)}
    for row in staged['reconciliation']:
        if row['raw']['itemid'] in selected: counts[row['raw']['itemid']][row['outcome']] += 1
    context = {'profile': PROFILE, 'request': request, 'admission_context_id': staged['summary']['context_id'],
               'source_files': staged['summary']['context']['files'],
               'catalogue_sha256': cr.digest(catalogue),
               'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    report = {'profile': PROFILE, 'status': 'VERIFIED_SOURCE_CATALOGUE', 'context': context,
              'context_id': cr.digest(context), 'catalogue': catalogue, 'dictionary_evidence': evidence,
              'selected_item_outcomes': {code: dict(sorted(value.items())) for code, value in counts.items()},
              'input_rows': staged['summary']['input_rows'],
              'reconciliation_complete': staged['summary']['reconciliation_complete'],
              'catalogue_dictionary_fidelity_verified': True,
              'clinical_mapping_verified': False, 'source_publisher_authenticated': False,
              'source_claims_accepted': 0, 'mapped_query_executed': False,
              'scope': 'Pinned bytes, dictionary labels and importer record classes; no terminology decision'}
    return report, staged


def build(folder, request):
    """Return aggregate evidence and a mapping-compatible catalogue; no patient rows."""
    return _prepare(folder, request)[0]


def audit_store(folder, request, catalogue, store):
    """Check every supplied interval claim and clock against the pinned source snapshot.

    Subsets are allowed for bounded batches. This does not establish source coverage,
    clinical truth, source acceptance, measurement fidelity or inter-store alignment.
    """
    report, staged = _prepare(folder, request)
    ei.require(catalogue == report['catalogue'], 'SOURCE_CATALOGUE_MISMATCH')
    ei.require(store.get('profile') == inputs.projection.RECORD_STORE_PROFILE, 'SOURCE_REQUIRES_RECORDED_STORE')
    inputs.projection.validate_store(store)
    ei.require(store['dataset_id'] == catalogue['dataset_id'], 'SOURCE_STORE_DATASET_MISMATCH')
    selected = set(request['itemids']); originals = {}; origins = {}
    for segment in sorted(staged['segments'], key=lambda s: (s['start']['raw_value'], s['id'])):
        patient = segment['patient_id']
        origins.setdefault(patient, {'clock_id': 'patient_' + patient, 'scope': 'patient:' + patient,
            'origin': segment['start']['raw_value'].replace(' ', 'T'), 'policy': inputs.local.POLICY,
            'origin_source_key': segment['id']})
        if segment['item']['itemid'] in selected:
            claim = inputs.describe(segment, request['dataset_id']); originals[claim['id']] = claim
    for claim in store['claims']:
        ei.require(claim['id'] in originals and claim == originals[claim['id']], 'SOURCE_CLAIM_MISMATCH')
    patients = {claim['patient_id'] for claim in store['claims']}
    expected_clocks = sorted((origins[p] for p in patients), key=lambda c: c['clock_id'])
    ei.require(sorted(store['clocks'], key=lambda c: c['clock_id']) == expected_clocks, 'SOURCE_CLOCK_MISMATCH')
    context = {'profile': PROFILE, 'catalogue_context_id': report['context_id'],
               'catalogue_sha256': cr.digest(catalogue), 'store_sha256': cr.digest(store)}
    return {'status': 'VERIFIED_SOURCE_STORE', 'context': context, 'context_id': cr.digest(context),
            'catalogue_report': report, 'checked_claims': len(store['claims']),
            'claim_row_fidelity_verified': True, 'recorded_clock_origin_verified': True,
            'complete_source_coverage_verified': False, 'clinical_mapping_verified': False,
            'source_acceptance_verified': False, 'measurement_fidelity_verified': False,
            'clock_alignment_verified': False}


def execute(folder, request, catalogue, terminology, proposals, review, interval_store, interval_policy,
            measurement_store, measurement_policy, alignment, query):
    audit = audit_store(folder, request, catalogue, interval_store)
    result = mappings.execute(catalogue, terminology, proposals, review, interval_store, interval_policy,
                              measurement_store, measurement_policy, alignment, query)
    return {**result, 'source_audit': audit}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv); temporary = None
    try:
        protected = {p.resolve() for p in inputs.source_paths(args.input_dir).values()} | {args.request.resolve()}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size <= cr.MAX_BYTES, 'CATALOGUE_REQUEST_LIMIT')
        result = build(args.input_dir, json.loads(args.request.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error) as error:
        parser.exit(2, str(error) + '\n')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status': result['status'], 'context_id': result['context_id']}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
