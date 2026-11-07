"""MIMIC-IV demo inputevents into the execution store, through the SULO adapter (store stage 2).

The route is the canonical one (decision log D8, D9 and D15):

1. `mimic_inputevents.stage` admits the source rows.
2. Each admitted row becomes one pending claim, `mimic_claim_import.describe`, in a patient-local
   record claim store built as the importer builds it: one patient clock whose origin is the
   patient's earliest admitted start, before any item filter. A record claim store holds at most
   32 claims (the store schema's limit and the importer's `MAX_CLAIMS`), so each ICU stay's claims
   are split, in source-record order, into stores of at most 32. Every store gets the importer's
   isolation certificate.
3. A caller-declared accept-all analysis policy accepts every claim of the store. This is not
   clinical review: it states that the analysis reads every recorded source row as asserted.
4. `trajectory_graph_sulo.from_claims` selects the accepted claims through the patient-local
   graph route, and `trajectory_graph_sulo.ingest_rows` projects the snapshot.
5. `store_patients` maps the adapter's rows to the execution-store ingest contract, and
   `execution_store.build` writes the snapshot.

Labelling (D6, D14). The demo has no reviewed clinical mappings, so the concepts are source item
classes (`mimic_claim_import.ITEM_NS` plus the itemid), never clinical concepts. Each item class
has its d_items category as an ancestor, labelled `source_dictionary_grouping`; the event kind's
class and its ontology ancestors come from the adapter (`event_kind_closure`). The ingest's
`provenance.labels`, copied into the snapshot manifest, say `clinical_mapping_verified: false`
and `concept_basis: source_item_class`.

Times are integer microseconds on the patient-local calendar from 0001-01-01T00:00: the clock
origin's coordinate plus the adapter's tightened bounds (`origin_us + start_us`). There is one
clock per patient. Coverage is one period per ICU stay, intime to outtime, with basis
`inferred:icustay`. A patient the adapter marks not box-representable, or whose events share a
time variable, is refused (`INGEST_PATIENT_NOT_BOX_REPRESENTABLE`) and counted by the adapter's
reason. See docs/execution-store.md, "Stage 2".
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote
import zlib

from . import claim_isolation
from . import claim_projection as cp
from . import claim_rdf as cr
from . import exact_intervals as ei
from . import joint_evidence as joint
from . import local_claim_projection as lcp
from . import mimic_claim_import as importer
from . import mimic_inputevents as admission
from . import patient_local as local
from . import trajectory_graph_sulo as tgs

PROFILE = 'mimic-store-ingest-1.0'
INGEST_PROFILE = 'execution-store-ingest-1.0'
ORIGIN = '0001-01-01T00:00:00'
SOURCE = 'inputevents'
COVERAGE_BASIS = 'inferred:icustay'
CONCEPT_BASIS = 'source_item_class'
GROUP_NS = 'https://example.org/trajectory/mimic-record-item-category/'
GROUP_BASIS = 'source_dictionary_grouping'
KIND_BASIS = 'event_kind_closure'
ONTOLOGY_BASIS = 'ontology_closure'
CLAIMS_PER_STORE = importer.MAX_CLAIMS
POLICY_ID = 'caller_declared_accept_all'
POLICY_REASON = 'Caller-declared accept-all analysis policy over pending source records; no clinical review.'
REFUSAL = 'INGEST_PATIENT_NOT_BOX_REPRESENTABLE'
FIXTURE = 'examples/mimic-store-ingest'  # a repository resource, resolved only by the command line
FIXTURE_DATASET = 'synthetic-mimic-store-2.2'
SELECTION = {'mode': 'retrospective', 'version': 'as_released', 'available_at': 'not_applied',
             'note': ('every admitted inputevents row of the release as read, all accepted for analysis; the release '
                      'carries no revision history or source availability times, so neither selects records')}
LABELS = {'clinical_mapping_verified': False, 'concept_basis': CONCEPT_BASIS, 'ancestor_basis': GROUP_BASIS,
          'structural_concept_basis': KIND_BASIS, 'coverage_basis': COVERAGE_BASIS, 'history_mode': 'retrospective',
          'acceptance': 'caller_declared_accept_all_analysis_not_clinical_review'}
FILES = tuple(sorted(set(
    ('patterns/mimic_store_ingest.py', 'patterns/execution_store.py', 'schemas/execution-store-manifest.schema.json',
     'patterns/claim_isolation.py', 'patterns/claim_rdf.py', 'ontology/claim-description-profile.ttl',
     'ontology/local-claim-description-profile.ttl')
    + importer.FILES + tgs.FILES + tgs.RDF_FILES + lcp.FILES + joint.FILES)))


class MimicStoreError(ValueError):
    """An input the converter refuses as a whole; the message is a stable code."""


def require(condition, code):
    if not condition:
        raise MimicStoreError(code)


def micros(label):
    """Microseconds from 0001-01-01T00:00 to a patient-local label ('2150-01-01 10:00:00' or with 'T')."""
    return local.coordinate(label, ORIGIN)


def group_iri(category):
    return GROUP_NS + quote(category, safe='')


def artifacts(paths=FILES):
    return {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in paths}


# ---------------------------------------------------------------- MIMIC rows to claim stores

def semantic_policy(itemids):
    """The joint semantic policy declaring these items' source item code classes (at most 32)."""
    return {'profile': 'joint-semantic-policy-1.0', 'id': 'source_item_codes',
            'classes': sorted(importer.ITEM_NS + i for i in set(itemids)), 'rules': [], 'disjoint': []}


def accept_all(store, semantic):
    """The caller-declared accept-all analysis policy over one claim store."""
    policy = {'profile': cp.POLICY_PROFILE, 'id': POLICY_ID, 'store_sha256': cr.digest(store),
              'semantic_policy_sha256': cr.digest(semantic),
              'decisions': [{'id': 'accept_' + c['id'], 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
                             'action': 'accept', 'supersedes': None, 'reason': POLICY_REASON}
                            for c in store['claims']]}
    cp.validate_policy(store, policy, semantic)
    return policy


def prepare(input_dir, dataset_id):
    """Admit the rows and build the claim stores, as `mimic_claim_import.run` builds one per stay.

    Returns the admission result, the ICU stays, the item dictionary, the claim stores (each at
    most CLAIMS_PER_STORE claims, one stay each, with a semantic policy declaring its items) and
    every claim's provenance.
    """
    paths = importer.source_paths(input_dir)
    staged = admission.stage(**paths, dataset_id=dataset_id)
    manifests = {f['table']: f for f in staged['summary']['context']['files']}
    dimensions = {}
    for table in ('icustays', 'd_items'):
        rows, manifest = admission.read_table(paths[table], table)
        require(manifest == manifests[table], 'SOURCE_CHANGED_DURING_RUN:' + table)
        dimensions[table] = rows
    segments = staged['segments']
    namespace = 'dataset_' + cr.digest(dataset_id)
    context = {'profile': PROFILE, 'dataset_id': dataset_id, 'admission_context_id': staged['summary']['context_id'],
               'origin_policy': 'minimum_admitted_start_per_patient_before_item_filter',
               'claims_per_store': CLAIMS_PER_STORE, 'acceptance_policy_id': POLICY_ID,
               'converter_sha256': artifacts(('patterns/mimic_store_ingest.py',))['patterns/mimic_store_ingest.py']}
    context_id = cr.digest(context)
    origins, groups = {}, defaultdict(list)
    for segment in sorted(segments, key=lambda s: (s['start']['raw_value'], s['id'])):
        origins.setdefault(segment['patient_id'], {'label': segment['start']['raw_value'],
                                                   'source_record_id': segment['id'], 'source': segment['source']})
        groups[(segment['patient_id'], segment['icu_stay_id'])].append(segment)
    stores, provenance = [], {}
    for (patient, stay), members in sorted(groups.items()):
        members.sort(key=lambda s: s['source']['record_number'])
        origin = origins[patient]
        for index in range(0, len(members), CLAIMS_PER_STORE):
            chunk = members[index:index + CLAIMS_PER_STORE]
            store = {'profile': lcp.RECORD_STORE_PROFILE, 'dataset_id': namespace,
                     'snapshot_id': 'import_' + cr.digest([context_id, patient, stay, index // CLAIMS_PER_STORE]),
                     'clocks': [{'clock_id': 'patient_' + patient, 'scope': 'patient:' + patient,
                                 'origin': origin['label'].replace(' ', 'T'), 'policy': local.POLICY,
                                 'origin_source_key': origin['source_record_id']}],
                     'claims': [importer.describe(s, dataset_id) for s in chunk]}
            # A semantic policy declares at most 32 classes, so each store declares its own items.
            stores.append({'patient_id': patient, 'episode_id': stay, 'part': index // CLAIMS_PER_STORE, 'store': store,
                           'semantic_policy': semantic_policy(s['item']['itemid'] for s in chunk)})
            for claim, segment in zip(store['claims'], chunk):
                # The importer's claim provenance, field for field.
                provenance[claim['id']] = {
                    'claim_sha256': cr.digest(claim), 'source_record_id': segment['id'], 'source': segment['source'],
                    'item_source': segment['item_source'], 'stay_source': segment['stay_source'],
                    'origin_source': origin['source'], 'origin_source_record_id': origin['source_record_id'],
                    'recorded_at': segment['recorded_at'], 'source_available_at': None,
                    'source_history_verified': False, 'clinical_mapping_verified': False}
    return {'context': context, 'context_id': context_id, 'admission': staged, 'stays': dimensions['icustays'],
            'items': {r['itemid']: r for r in dimensions['d_items']}, 'stores': stores,
            'claim_provenance': provenance}


def adapter_rows(store, semantic, isolation=True):
    """One claim store through acceptance, the graph route and `ingest_rows`.

    Module-level so that worker processes can run it. Returns the rows and the route's facts.
    """
    lcp.validate_store(store)
    certified = False
    if isolation:
        claim_isolation.check(cr.encode(store, fields=cr.LOCAL_FIELDS), local=True)
        certified = True
    view = tgs.from_claims(store, accept_all(store, semantic), semantic)
    return tgs.ingest_rows(view), {'route': view.route, 'files': sorted(view.files), 'isolation_certified': certified,
                                   'accepted_claims': len(view.provenance['accepted_claim_ids'])}


def run_adapter(prepared, workers=1, isolation=True):
    """Every claim store through the adapter, in parallel if asked; rows in store order."""
    jobs = [(s['store'], s['semantic_policy'], isolation) for s in prepared['stores']]
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(adapter_rows, *zip(*jobs), chunksize=4))
    else:
        results = [adapter_rows(*job) for job in jobs]
    rows = [row for result, _ in results for row in result]
    routes = Counter(info['route'] for _, info in results)
    return rows, {'stores': len(results), 'routes': dict(routes),
                  'isolation_certified': sum(info['isolation_certified'] for _, info in results),
                  'accepted_claims': sum(info['accepted_claims'] for _, info in results),
                  'route_files': sorted({f for _, info in results for f in info['files']})}


# ---------------------------------------------------------------- adapter rows to the store contract

def _refusal(rows):
    """The adapter's reason a patient's events are not boxes, or None.

    Per store, `ingest_rows` gives the verdict. Across a patient's stores, a time variable named by
    two rows is also an equality between events, so it is refused the same way.
    """
    if any(r['non_box_constraints'] for r in rows):
        return 'SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE'
    if any(r['shared_variables'] for r in rows) or any(not r['box_representable'] for r in rows):
        return 'SHARED_SOURCE_VARIABLE'
    uses = Counter(v for r in rows for v in {r['start_var'], r['end_var']})
    if any(n > 1 for n in uses.values()):
        return 'SHARED_SOURCE_VARIABLE'
    return None


def _closure(concepts, vocabulary, categories):
    """{concept: strict ancestors, basis} for every concept the rows carry, plus groupings.

    Ontology classes take their named ancestors from the adapter's vocabulary. A source item class
    takes its d_items category as its one ancestor (`source_dictionary_grouping`), if it has one.
    """
    ancestors, bases = {}, {}
    pending = list(concepts)
    while pending:
        c = pending.pop()
        if c in ancestors:
            continue
        if c in vocabulary.named:
            above, basis = sorted(vocabulary.ancestors(c) - {c}), ONTOLOGY_BASIS
        elif c.startswith(importer.ITEM_NS):
            category = categories.get(c[len(importer.ITEM_NS):], '')
            above, basis = ([group_iri(category)] if category else []), CONCEPT_BASIS
        elif c.startswith(GROUP_NS):
            above, basis = [], GROUP_BASIS
        else:
            raise MimicStoreError('UNCLASSIFIED_CONCEPT:' + c)
        ancestors[c], bases[c] = above, basis
        pending.extend(above)
    return ancestors, bases


def _evidence(row, claim, origin_us):
    supports = row['claim_supports']
    require(len(supports) == 1 and supports[0]['assertion_id'] == 'claim_' + row['event_id'], 'CLAIM_SUPPORT_MISMATCH')
    claim_id, claim_sha256 = supports[0]['assertion_id'], supports[0]['claim_sha256']
    require(claim is not None and claim['claim_sha256'] == claim_sha256, 'CLAIM_DIGEST_MISMATCH')
    recorded = claim['recorded_at']['raw_value']
    return {
        'adapter_profile': row['profile'], 'snapshot_context_id': row['snapshot_context_id'],
        'time_domain': row['time_domain'], 'evidence_id': row['evidence_id'],
        'evidence_context_id': row['evidence_context_id'], 'claim': row['claim'], 'claim_id': claim_id,
        'claim_sha256': claim_sha256, 'patient_bearer': row['patient_bearer'], 'source_record': row['source_record'],
        'source_hash': row['source_hash'], 'source_key': row['source_key'], 'record_id': row['record_id'],
        'record_status': row['record_status'], 'episode_id': row['episode_id'],
        'selection_state': row['selection_state'], 'claim_supports': supports,
        'clock_policy': row['clock_policy'], 'clock_origin': row['clock_origin'], 'clock_origin_us': origin_us,
        'declared_start_us': row['declared_start_us'], 'declared_end_us': row['declared_end_us'],
        'start_var': row['start_var'], 'end_var': row['end_var'],
        'concept_bases': {c['concept']: sorted({b['kind'] for b in c['basis']}) for c in row['concepts']},
        'entailment': row['entailment'], 'vocabulary_disjointness': row['vocabulary_disjointness'],
        'source_table': SOURCE, 'source_record_number': claim['source']['record_number'],
        'source_row_sha256': claim['source']['row_sha256'], 'source_file_sha256': claim['source']['file_sha256'],
        'recorded_at_us': micros(recorded) if recorded else None, 'available_at_us': None,
        'history_mode': 'retrospective', 'source_history_verified': False, 'clinical_mapping_verified': False}


def _coverage(stays):
    """{patient: [periods]} from ICU stays, and the stays left out for want of times."""
    out, skipped = defaultdict(set), Counter()
    for stay in stays:
        try:
            lo, hi = micros(stay['intime']), micros(stay['outtime'])
        except (ei.ContractError, ValueError):
            skipped['STAY_TIMES_MISSING_OR_INVALID'] += 1
            continue
        if lo > hi:
            skipped['STAY_OUTTIME_BEFORE_INTIME'] += 1
            continue
        out[stay['subject_id']].add((lo, hi))
    return {p: [{'source': SOURCE, 'lo': lo, 'hi': hi, 'basis': COVERAGE_BASIS} for lo, hi in sorted(periods)]
            for p, periods in out.items()}, skipped


def store_patients(rows, *, claim_provenance, stays, items):
    """Map the adapter's ingest rows to the store's patients and closure.

    `rows` are `ingest_rows` output over any number of views; `claim_provenance` is keyed by claim
    id; `stays` are icustays rows (every subject is a patient, with or without events); `items`
    maps itemid to its d_items row. Returns (patients, closure ancestors, ancestor bases, refused,
    counts). Patients the adapter marks not box-representable are left out and counted.
    """
    by_patient = defaultdict(list)
    for row in rows:
        require(row['profile'] == tgs.INGEST_PROFILE, 'UNSUPPORTED_ADAPTER_ROWS')
        by_patient[row['patient_id']].append(row)
    coverage, skipped = _coverage(stays)
    vocabulary = tgs.Vocabulary()
    categories = {i: r['category'] for i, r in items.items()}
    concepts = {c['concept'] for row in rows for c in row['concepts']}
    ancestors, bases = _closure(concepts, vocabulary, categories)
    patients, refused, events_refused = [], Counter(), 0
    for pid in sorted({s['subject_id'] for s in stays} | set(by_patient)):
        prows = sorted(by_patient.get(pid, []), key=lambda r: r['event_id'])
        reason = _refusal(prows)
        if reason:
            refused[reason] += 1
            events_refused += len(prows)
            continue
        clocks = {(r['clock_id'], r['clock_policy'], r['clock_origin']) for r in prows}
        if len(clocks) > 1:
            refused['CLOCKS_NOT_REBASABLE'] += 1
            events_refused += len(prows)
            continue
        clock_id = next(iter(clocks))[0] if clocks else 'patient_' + pid
        events = []
        for r in prows:
            require(r['clock_policy'] == local.POLICY, 'UNSUPPORTED_CLOCK_POLICY:' + r['clock_policy'])
            require(not r['ongoing'], 'ONGOING_EVENT')
            origin_us = micros(r['clock_origin'])
            direct = sorted({r['kind_class']} | {c['concept'] for c in r['concepts']
                                                 if any(b['kind'] == 'semantic_fact' for b in c['basis'])})
            expanded = set(direct).union(*(ancestors[c] for c in direct))
            carried = {c['concept'] for c in r['concepts']}
            # The closure must reach exactly the adapter's concepts, plus the declared groupings.
            require({c for c in expanded if not c.startswith(GROUP_NS)} == carried, 'CONCEPT_CLOSURE_MISMATCH')
            events.append({'event_id': r['event_id'], 'kind': r['event_kind'], 'source': SOURCE, 'concepts': direct,
                           'start': [origin_us + r['start_us'][0], origin_us + r['start_us'][1]],
                           'end': [origin_us + r['end_us'][0], origin_us + r['end_us'][1]],
                           'evidence': _evidence(r, claim_provenance.get('claim_' + r['event_id']), origin_us)})
        patients.append({'patient_id': pid, 'clock_id': clock_id, 'box_representable': True,
                         'events': events, 'coverage': coverage.get(pid, [])})
    counts = {'events_refused': events_refused, 'stays_without_coverage': dict(sorted(skipped.items()))}
    refusals = {REFUSAL: {'patients': sum(refused[k] for k in refused if k != 'CLOCKS_NOT_REBASABLE'),
                          'by_adapter_reason': dict(sorted((k, v) for k, v in refused.items()
                                                           if k != 'CLOCKS_NOT_REBASABLE'))},
                'CLOCKS_NOT_REBASABLE': {'patients': refused['CLOCKS_NOT_REBASABLE']}}
    return patients, ancestors, bases, refusals, counts


def ingest_document(patients, ancestors, bases, *, dataset_id, provenance):
    """The execution-store ingest, with a closure version binding the ancestors and their bases."""
    closure = {c: sorted(a) for c, a in sorted(ancestors.items())}
    version = PROFILE + ':' + ei.digest(ei.canonical({'ancestors': closure, 'bases': dict(sorted(bases.items()))}))
    return {'profile': INGEST_PROFILE, 'dataset_id': dataset_id + ':' + CONCEPT_BASIS,
            'provenance': {**provenance, 'selection': SELECTION, 'labels': LABELS,
                           'ancestor_bases': dict(sorted(Counter(bases.values()).items()))},
            'closure': {'version': version, 'ancestors': closure}, 'patients': patients}


def convert(input_dir, dataset_id, workers=1, isolation=True):
    """Local MIMIC files to a store ingest through the canonical route; with counts and timings."""
    timings = {}
    t = time.perf_counter()
    prepared = prepare(input_dir, dataset_id)
    timings['admission_and_claims'] = time.perf_counter() - t
    t = time.perf_counter()
    rows, route = run_adapter(prepared, workers, isolation) if prepared['stores'] else ([], {'stores': 0})
    timings['adapter'] = time.perf_counter() - t
    t = time.perf_counter()
    patients, ancestors, bases, refused, extra = store_patients(
        rows, claim_provenance=prepared['claim_provenance'], stays=prepared['stays'], items=prepared['items'])
    require(patients, 'NO_PATIENTS')
    summary = prepared['admission']['summary']
    events = sum(len(p['events']) for p in patients)
    inside = sum(any(c['lo'] <= e['start'][0] <= c['hi'] for c in p['coverage']) for p in patients for e in p['events'])
    counts = {'source_rows': summary['input_rows'], 'admission_outcomes': summary['outcome_counts'],
              'admitted_rows': summary['staged_segments'], 'claims': len(prepared['claim_provenance']),
              'claim_stores': len(prepared['stores']),
              'icu_stays': len(prepared['stays']),
              'icu_stays_with_claims': len({(s['patient_id'], s['episode_id']) for s in prepared['stores']}),
              'patients': len({s['subject_id'] for s in prepared['stays']}),
              'patients_in_store': len(patients),
              'patients_with_events': sum(bool(p['events']) for p in patients),
              'events': events, 'events_starting_outside_coverage': events - inside,
              'coverage_periods': sum(len(p['coverage']) for p in patients),
              'item_classes': sum(b == CONCEPT_BASIS for b in bases.values()),
              'groupings': sum(b == GROUP_BASIS for b in bases.values()), **extra}
    provenance = {'converter': PROFILE, 'context_id': prepared['context_id'], 'context': prepared['context'],
                  'route': {'steps': ['mimic_inputevents.stage', 'mimic_claim_import.describe',
                                      'claim_isolation.check' if isolation else 'claim_isolation.skipped',
                                      'accept_all:' + POLICY_ID, 'trajectory_graph_sulo.from_claims',
                                      'trajectory_graph_sulo.ingest_rows', 'mimic_store_ingest.store_patients'],
                            'adapter_ingest_profile': tgs.INGEST_PROFILE, 'claims_per_store': CLAIMS_PER_STORE,
                            **{k: v for k, v in route.items() if k != 'route_files'}},
                  'refused': refused, 'counts': counts}
    ingest = ingest_document(patients, ancestors, bases, dataset_id=dataset_id, provenance=provenance)
    timings['mapping'] = time.perf_counter() - t
    return {'ingest': ingest, 'rows': rows, 'prepared': prepared, 'counts': counts, 'refused': refused,
            'route': route, 'timings_seconds': timings}


# ---------------------------------------------------------------- command line

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--input-dir', type=Path, default=None,
                        help='directory holding inputevents, icustays and d_items (.csv or .csv.gz)')
    parser.add_argument('--dataset-id', help='required for supplied files; e.g. mimic-iv-demo-2.2')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/execution-store-mimic-run',
                        help='git-ignored directory for the snapshot and the run summary')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--threads', type=int, default=1, help='DuckDB threads')
    args = parser.parse_args(argv)
    from . import execution_store as es
    try:
        dataset_id, fixture = args.dataset_id, Path(ei.ROOT / FIXTURE)
        args.input_dir = args.input_dir or fixture
        if dataset_id is None and args.input_dir.resolve() == fixture.resolve():
            dataset_id = FIXTURE_DATASET
        require(dataset_id is not None, 'DATASET_ID_REQUIRED')
        inputs = {p.resolve() for p in importer.source_paths(args.input_dir).values()}
        require(not any(args.output.resolve() == p or args.output.resolve() in p.parents for p in inputs),
                'OUTPUT_OVERWRITES_INPUT')
        result = convert(args.input_dir, dataset_id, workers=args.workers)
        t = time.perf_counter()
        snapshot = es.build(result['ingest'], args.output / 'store', threads=args.threads)
        result['timings_seconds']['store_build'] = time.perf_counter() - t
    except (ValueError, LookupError, OSError, EOFError, csv.Error, zlib.error) as error:
        print(json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}), file=sys.stderr)
        return 2
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    summary = {'profile': PROFILE, 'snapshot_id': manifest['snapshot_id'], 'snapshot': str(snapshot),
               'labels': manifest['labels'], 'counts': result['counts'], 'refused': result['refused'],
               'tables': {n: t['rows'] for n, t in sorted(manifest['tables'].items())},
               'timings_seconds': {k: round(v, 3) for k, v in result['timings_seconds'].items()}}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
