"""MIMIC-IV chartevents for the execution store by route (b): bulk rows from the importer's claim identity.

Decision log D16 (draft D27, docs/decisions/chartevents-route-b.md). Route (a), every row through
the graph route, costs about 0.2 CPU-seconds a claim, so a bulk source cannot take it. Here:

1. The file is read twice, streaming. The first pass checks the header and records, hashes the
   compressed and the decompressed bytes (the record identifiers need the CSV digest) and collects
   the item ids. The second pass hashes again, refuses a file that changed between the passes
   (`SOURCE_CHANGED_DURING_RUN:chartevents`), and spills each row to one of `shards` files by a hash
   of its patient. Each shard is then grouped by patient in memory, so Python holds one shard.
2. Each patient is one batch. Its rows, in record order, are checked for exact duplicates and
   admitted by `mimic_measurement_import.admit`, the importer's own rule: a numeric value equal to
   its text, an explicit unit, `warning = 0`. Its clock origin is its earliest admitted charttime,
   as the importer chooses it. Each ICU stay's admitted rows are cut, in record order, into claim
   stores of at most 32 (`MAX_CLAIMS`, as D15 cuts inputevents).
3. Every admitted row becomes one claim by `mimic_measurement_import.describe`, the importer's pure
   claim identity, and the store row is computed from that claim and its provenance alone
   (`claim_fields`, then `store_row`): one point event and one series row. No graph is built.
   `patterns/mimic_chartevents_check.py` sends a deterministic sample of the claim stores through
   the measurement graph route and requires its rows to equal these, field for field; that check shares
   the row mapping, so `patterns/mimic_chartevents_raw_check.py` verifies every row against the raw CSV.
4. Rows admission leaves out are excluded records (D18), each with its item class and charttime;
   coverage is one period per ICU stay that holds any chart row, widened to the stay's own records.

Concepts are source item classes (`mimic-record-item/<itemid>`, the namespace inputevents uses; d_items
item ids are unique across tables), each with its d_items category as its one ancestor (D14). They
are never clinical concepts; `clinical_mapping_verified` is false. The event kind,
`recorded_measurement`, has no ontology class, so a measurement event carries its item class only.

The execution store does not take `series` yet: `patient_batch` returns it beside the store patient.
docs/decisions/chartevents-integration-notes.md lists the store hooks.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time

from . import claim_rdf as cr
from . import exact_intervals as ei
from . import measurement_claims as claims
from . import mimic_claim_import as interval_importer
from . import mimic_inputevents as source
from . import mimic_measurement_import as importer
from . import patient_local as local
from . import series_values

PROFILE = 'mimic-chartevents-route-b-1.0'
SOURCE_TABLE = 'chartevents'
# The store holds only the rows admission keeps, so the source is named by that subset (as D15 names inputevents).
SOURCE = 'chartevents:numeric_measurements'
KIND = 'recorded_measurement'
ITEM_NS = interval_importer.ITEM_NS
GROUP_NS = 'https://example.org/trajectory/mimic-record-item-category/'  # as mimic_store_ingest.GROUP_NS
CONCEPT_BASIS = 'source_item_class'
GROUP_BASIS = 'source_dictionary_grouping'
ITEM_FIELD_BASIS = 'claim_item_field'
COVERAGE_BASIS = 'inferred:icustay'
EXTENDED_COVERAGE_BASIS = 'inferred:icustay+records'
CLAIMS_PER_STORE = importer.MAX_CLAIMS
ORIGIN = '0001-01-01T00:00:00'
MAX_TIME = 315_537_897_599_999_999  # as execution_store.MAX_TIME: 9999-12-31T23:59:59.999999
MICRO = 10 ** 6
POLICY_ID = 'caller_declared_accept_all'
SERIES_COLUMNS = ('event_id', 'item_class', 'value_lexical', 'value_decimal', 'value_micro', 'unit')
LABELS = {'clinical_mapping_verified': False, 'concept_basis': CONCEPT_BASIS, 'ancestor_basis': GROUP_BASIS,
          'coverage_basis': COVERAGE_BASIS, 'extended_coverage_basis': EXTENDED_COVERAGE_BASIS,
          'history_mode': 'retrospective', 'acceptance': 'caller_declared_accept_all_analysis_not_clinical_review',
          'source': SOURCE, 'source_table': SOURCE_TABLE, 'admission_profile': importer.PROFILE,
          'admission_rule': ('numeric value equal to its text, explicit unit, warning 0; textual or qualified '
                             'values, missing units, flagged or unknown warnings, invalid and duplicate rows left out'),
          'route': 'b: rows from describe() claim identity; a deterministic sample of claim stores checked through '
                   'the measurement graph route',
          'value_policy': 'exported decimal spelling kept; exact decimal and micro-unit integer; no unit conversion',
          'excluded_records': 'excluded_records table; a none they could change is not_comparable'}
FILES = tuple(sorted(set(('patterns/mimic_chartevents.py', 'patterns/series_values.py') + importer.FILES)))


class ChartError(ValueError):
    """An input the converter refuses as a whole; the message is a stable code."""


def require(condition, code):
    if not condition:
        raise ChartError(code)


def micros(label):
    """Microseconds from 0001-01-01T00:00 to a patient-local label (as mimic_store_ingest.micros)."""
    return local.coordinate(label, ORIGIN)


def group_iri(category):
    from urllib.parse import quote
    return GROUP_NS + quote(category, safe='')


# ---------------------------------------------------------------- reading, two streaming passes

class _Hashing(io.RawIOBase):
    """A readable stream that hashes the bytes read through it."""

    def __init__(self, handle):
        self.handle, self.sha = handle, hashlib.sha256()

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self.handle.read(len(buffer))
        self.sha.update(data)
        buffer[:len(data)] = data
        return len(data)


def _records(path):
    """Yield (record number, values) and finally a manifest; the header and record shape are checked."""
    path = Path(path)
    with path.open('rb') as raw:
        outer = _Hashing(raw)
        inner_stream = gzip.GzipFile(fileobj=io.BufferedReader(outer)) if path.suffix == '.gz' else io.BufferedReader(outer)
        inner = _Hashing(inner_stream)
        text = io.TextIOWrapper(io.BufferedReader(inner), encoding='utf-8', newline='')
        reader = csv.reader(text, strict=True)
        header = next(reader, [])
        require(len(header) == len(importer.HEADER) and set(header) == set(importer.HEADER), 'HEADER_MISMATCH:chartevents')
        number = 0
        for number, values in enumerate(reader, 1):
            require(len(values) == len(header), f'MALFORMED_CSV_RECORD:chartevents:{number}')
            yield number, header, values
        # Drain whatever the CSV reader left buffered, so both digests cover the whole file.
        while text.read(1 << 20):
            pass
        while outer.read(1 << 20):
            pass
    yield None, header, {'table': 'chartevents', 'file_sha256': outer.sha.hexdigest(),
                         'csv_sha256': inner.sha.hexdigest(), 'columns': header, 'rows': number}


def read_manifest(path):
    """Pass one: the file's manifest (as the importer's `read_observations` gives it, unbounded) and
    the set of item ids the rows name."""
    itemids, column = set(), None
    for number, header, values in _records(path):
        if number is None:
            return values, itemids
        if column is None:
            column = header.index('itemid')
        itemids.add(values[column])


def shard_of(patient_id, shards):
    return int.from_bytes(hashlib.sha256(patient_id.encode('utf-8')).digest()[:4], 'big') % shards


MAX_OPEN_SHARDS = 64  # well under macOS's default limit of 256 descriptors


def spill(path, manifest, work_dir, shards, patients=None, max_open=MAX_OPEN_SHARDS):
    """Pass two: each row to its patient's shard file, as a JSON line [record number, values...].

    Only patients in `patients` (a set) are kept when it is given. The file's digests must equal
    pass one's. At most `max_open` shard files are open at once (least recently used closed first,
    then reopened to append), so any shard count works. Sizing: one shard is grouped in memory, and
    Python holds roughly 5 to 10 times a shard's bytes, so choose `shards` of about the expanded CSV
    size over the memory a shard may take. The demo (65 MB) fits in one; full MIMIC-IV chartevents
    (about 30 GB expanded) wants several hundred. Returns the shard paths that hold rows.
    """
    from collections import OrderedDict
    work_dir = Path(work_dir)
    handles, out, column = OrderedDict(), {}, None
    try:
        for number, header, values in _records(path):
            if number is None:
                require(values == manifest, 'SOURCE_CHANGED_DURING_RUN:chartevents')
                break
            if column is None:
                column = header.index('subject_id')
            pid = values[column]
            if patients is not None and pid not in patients:
                continue
            k = shard_of(pid, shards)
            if k in handles:
                handles.move_to_end(k)
            else:
                if len(handles) >= max_open:
                    handles.popitem(last=False)[1].close()
                first = k not in out
                out.setdefault(k, work_dir / f'chartevents-shard-{k:04d}.jsonl')
                handles[k] = out[k].open('w' if first else 'a', encoding='utf-8')
            handles[k].write(json.dumps([number] + values, ensure_ascii=False, separators=(',', ':')) + '\n')
    finally:
        for h in handles.values():
            h.close()
    return [out[k] for k in sorted(out)]


def patient_rows(shard_paths, header):
    """Yield (patient_id, [(record number, row)]) shard by shard; patients by id within a shard."""
    for shard in shard_paths:
        grouped = defaultdict(list)
        with Path(shard).open(encoding='utf-8') as handle:
            for line in handle:
                number, *values = json.loads(line)
                row = dict(zip(header, values))
                grouped[row['subject_id']].append((number, row))
        for pid in sorted(grouped):
            yield pid, sorted(grouped.pop(pid), key=lambda pair: pair[0])


# ---------------------------------------------------------------- dimensions and the closure

def source_paths(input_dir):
    return importer.paths_for(input_dir)


def dimensions(paths):
    """icustays and d_items as the importer reads and indexes them; their manifests."""
    tables = {t: source.read_table(paths[t], t) for t in ('icustays', 'd_items')}
    stays = source.index_dimension(tables['icustays'][0], 'icustays', 'stay_id')
    items = source.index_dimension(tables['d_items'][0], 'd_items', 'itemid')
    admissions = {}
    for _, stay in stays.values():
        require(admissions.setdefault(stay['hadm_id'], stay['subject_id']) == stay['subject_id'],
                'CONFLICTING_ADMISSION_PATIENT')
    return stays, items, {t: r[1] for t, r in tables.items()}


def closure(itemids, items):
    """{concept: ancestors}, {concept: basis} for the item classes the rows name (D14). An item the
    dictionary does not hold has no concept; an item with an empty category has no ancestor."""
    ancestors, bases = {}, {}
    for i in sorted(itemids):
        if i not in items:
            continue
        category = items[i][1]['category']
        above = [group_iri(category)] if category else []
        ancestors[ITEM_NS + i], bases[ITEM_NS + i] = above, CONCEPT_BASIS
        for g in above:
            ancestors[g], bases[g] = [], GROUP_BASIS
    return ancestors, bases


# ---------------------------------------------------------------- values

# The value columns' rules live in `series_values`, which the store's validator also uses (D27).
micro_value = series_values.micro_value


def value_columns(value_lexical):
    """(decimal text, value in micro-units or None).

    The decimal text is `format(Decimal, 'f')`, exactly as `measurement_claims.select` renders the
    value: exact and scale-preserving, but not a value key ('1.5' and '1.50' differ, '-0' stays).
    Equality, grouping and comparison use `value_micro`, exact whenever it is not null; a null
    `value_micro` (finer than 10**-6, or beyond BIGINT) must be compared by exact decimal arithmetic.
    """
    d = claims.decimal_value(value_lexical)
    return format(d, 'f'), micro_value(d)


def value_shape(value_lexical):
    """A lexical class of an exported number, used to stratify the graph-route sample."""
    v = value_lexical
    mantissa = v.lower().split('e')[0]
    return ('-' if v.startswith('-') else '+' if v.startswith('+') else '') + (
        'e' if 'e' in v.lower() else '') + ('.x' if v.lstrip('+-').startswith('.') else '') + (
        'x.' if v.endswith('.') else '') + ('frac' if '.' in v.strip('.') else 'int') + (
        '0' if '.' in mantissa and mantissa.endswith('0') else '')


# ---------------------------------------------------------------- one patient: admission, claims, rows

def context_for(dataset_id, manifest, dimension_manifests):
    """The run context whose digest names every claim store (as the importer's context names its own)."""
    context = {'profile': PROFILE, 'dataset_id': dataset_id, 'admission_profile': importer.PROFILE,
               'source_files': [manifest] + [dimension_manifests[t] for t in ('icustays', 'd_items')],
               'dataset_namespace': 'dataset_' + cr.digest(dataset_id),
               'origin_policy': 'minimum_admitted_charttime_per_patient_before_item_filter',
               'claims_per_store': CLAIMS_PER_STORE, 'acceptance_policy_id': POLICY_ID}
    return context, cr.digest(context)


def admit_patient(rows, *, manifest, stays, items, dataset_id):
    """Ledger entries for one patient's rows, in record order, as the importer builds them."""
    entries, seen = [], {}
    for number, row in rows:
        evidence = source.evidence('chartevents', number, row, manifest)
        record_id = 'chartevents:' + dataset_id + ':' + manifest['csv_sha256'] + ':' + str(number)
        entry = {'record_id': record_id, 'raw': row, 'source': evidence, 'duplicate_of': None}
        prior = seen.setdefault(evidence['row_sha256'], record_id)
        if prior != record_id:
            entry.update(outcome='DUPLICATE_ROW', reasons=['EXACT_DUPLICATE_SOURCE_ROW'], duplicate_of=prior)
        else:
            outcome, reasons = importer.admit(row, stays, items)
            entry.update(outcome=outcome, reasons=reasons)
        entries.append(entry)
    return entries


def origin_of(entries):
    """The patient's earliest admitted charttime, ties broken by record id as the importer breaks them."""
    best = None
    for e in entries:
        if e['outcome'] == 'ADMITTED_MEASUREMENT':
            key = (e['raw']['charttime'], e['record_id'])
            if best is None or key < (best['label'], best['source_record_id']):
                best = {'label': e['raw']['charttime'], 'source_record_id': e['record_id'], 'source': e['source']}
    return best


def store_groups(entries):
    """{(stay, part): admitted entries}: each stay's admitted rows in record order, cut into stores of
    at most CLAIMS_PER_STORE."""
    by_stay = defaultdict(list)
    for e in entries:
        if e['outcome'] == 'ADMITTED_MEASUREMENT':
            by_stay[e['raw']['stay_id']].append(e)
    out = {}
    for stay, members in sorted(by_stay.items()):
        for index in range(0, len(members), CLAIMS_PER_STORE):
            out[(stay, index // CLAIMS_PER_STORE)] = members[index:index + CLAIMS_PER_STORE]
    return out


def claim_store(pid, stay, part, chunk, origin, *, context_id, dataset_id, namespace):
    """The claim store exactly as the importer would build it for this chunk (route (a)'s input)."""
    return {'profile': claims.PROFILE, 'dataset_id': namespace,
            'snapshot_id': 'import_' + cr.digest([context_id, pid, stay, part]),
            'clocks': [{'clock_id': 'patient_' + pid, 'scope': 'patient:' + pid,
                        'origin': origin['label'].replace(' ', 'T'), 'policy': local.POLICY,
                        'origin_source_key': origin['source_record_id']}],
            'claims': [importer.describe(e, dataset_id) for e in chunk]}


def provenance_of(entry, origin):
    """What the store row takes from the source row rather than the claim (shared by both routes)."""
    storetime = entry['raw']['storetime']
    return {'source_record_number': entry['source']['record_number'],
            'source_row_sha256': entry['source']['row_sha256'], 'source_file_sha256': entry['source']['file_sha256'],
            'recorded_at_us': micros(storetime) if storetime else None,
            'origin_source_record_id': origin['source_record_id']}


def claim_fields(claim, clock):
    """Route (b): the fields a store row needs, read from a `describe()` claim and its clock."""
    event, variable = claim['bundle']['events'][0], claim['bundle']['variables'][0]
    require(len(claim['bundle']['events']) == 1 and len(claim['bundle']['variables']) == 1
            and event['time_var'] == variable['id'], 'UNEXPECTED_CLAIM_SHAPE')
    origin_us = micros(clock['origin'])
    return {'patient_id': claim['patient_id'], 'episode_id': claim['episode_id'], 'event_id': event['id'],
            'record_id': event['record_id'], 'source_key': event['source_key'], 'record_status': event['status'],
            'event_kind': event['event_kind'], 'time_var': event['time_var'], 'item_id': event['item_id'],
            'value_lexical': event['value_lexical'], 'unit_lexical': event['unit_lexical'],
            'claim_id': claim['id'], 'claim_sha256': cr.digest(claim), 'source_record': claim['source_record_id'],
            'clock_id': variable['clock_id'], 'clock_origin': clock['origin'], 'clock_origin_us': origin_us,
            'start_offset_us': [local.coordinate(variable['local_lower'], clock['origin']),
                                local.coordinate(variable['local_upper'], clock['origin'])]}


def store_row(fields, provenance):
    """(store event, series row) from a claim's fields: a point event on the patient-local calendar.

    The D5 record fields are the store's typed event fields (D26): the claim's status and identifier and digest,
    the selection state, the source table, the source row's and file's sha256 and, where the row has a storetime,
    the time it was recorded; the rest of the provenance is the free-form evidence. No availability time is known.

    The event's time is the clock origin's coordinate plus the claim variable's offset from it, as
    the interval route computes `origin_us + start_us`. The event names its claim time variable,
    is never proper (a point cannot be) and has no end.
    """
    concept = ITEM_NS + fields['item_id']
    lo, hi = (fields['clock_origin_us'] + v for v in fields['start_offset_us'])
    require(fields['event_kind'] == KIND, 'UNEXPECTED_EVENT_KIND')
    decimal, micro = value_columns(fields['value_lexical'])
    record = {'status': fields['record_status'], 'selection_state': 'accepted', 'claim_id': fields['claim_id'],
              'claim_sha256': fields['claim_sha256'], 'source_table': SOURCE_TABLE,
              'record_sha256': provenance['source_row_sha256'], 'file_sha256': provenance['source_file_sha256']}
    if provenance['recorded_at_us'] is not None:
        record['recorded_at'] = provenance['recorded_at_us']
    event = {'event_id': fields['event_id'], 'kind': KIND, 'source': SOURCE, 'concepts': [concept],
             'start': [lo, hi], 'start_var': fields['time_var'], **record,
             'evidence': {'source_record': fields['source_record'], 'record_id': fields['record_id'],
                          'source_key': fields['source_key'], 'episode_id': fields['episode_id'],
                          'acceptance_policy_id': POLICY_ID, 'clock_id': fields['clock_id'],
                          'clock_origin': fields['clock_origin'], 'clock_origin_us': fields['clock_origin_us'],
                          'concept_bases': {concept: [ITEM_FIELD_BASIS]},
                          'source_record_number': provenance['source_record_number'],
                          'origin_source_record_id': provenance['origin_source_record_id'],
                          'history_mode': 'retrospective', 'source_history_verified': False,
                          'clinical_mapping_verified': False}}
    series = {'event_id': fields['event_id'], 'item_class': concept, 'value_lexical': fields['value_lexical'],
              'value_decimal': decimal, 'value_micro': micro, 'unit': fields['unit_lexical']}
    return event, series


def bulk_rows(chunk, origin, pid, dataset_id):
    """Route (b) for one claim store's chunk: the claims by `describe()` and their store rows."""
    clock = {'clock_id': 'patient_' + pid, 'origin': origin['label'].replace(' ', 'T')}
    out = []
    for entry in chunk:
        claim = importer.describe(entry, dataset_id)
        out.append(store_row(claim_fields(claim, clock), provenance_of(entry, origin)))
    return out


def rows_digest(rows):
    """sha256 of a claim store's bulk rows (events and series) as this converter emits them, so a later
    pass can show it re-derived the same rows. It is taken before any merge or store mapping, so it does
    not show that these are the rows the store holds; integration compares with the built store."""
    return cr.digest([[event, series] for event, series in rows])


def excluded_records(entries, items):
    """The rows admission left out: item class and charttime (all time if unreadable), with reasons.
    Exact duplicates add nothing; a row whose item the dictionary does not hold is only counted."""
    out, counts = [], Counter()
    for e in entries:
        if e['outcome'] == 'ADMITTED_MEASUREMENT':
            continue
        if e['outcome'] == 'DUPLICATE_ROW':
            counts['duplicate_rows_skipped'] += 1
            continue
        raw = e['raw']
        if raw['itemid'] not in items:
            counts['rows_with_unknown_item'] += 1
            continue
        try:
            source.local_time(raw['charttime'])
            start = [micros(raw['charttime'])] * 2
        except (ValueError, ei.ContractError):
            start = [0, MAX_TIME]
            counts['rows_with_unreadable_charttime'] += 1
        out.append({'source': SOURCE, 'concept': ITEM_NS + raw['itemid'], 'start': start,
                    'reason': '|'.join(sorted(set(e['reasons']))) or e['outcome']})
        counts['rows'] += 1
    return out, counts


def coverage(stays, events):
    """One period per ICU stay, widened to the stay's own admitted records (points), as the
    inputevents converter widens it. Returns the periods, skipped stays and outlying distances."""
    by_stay = defaultdict(list)
    for e in events:
        by_stay[e['evidence']['episode_id']].append(e)
    periods, skipped, outside = set(), Counter(), []
    for stay in stays:
        try:
            lo, hi = micros(stay['intime']), micros(stay['outtime'])
        except (ei.ContractError, ValueError):
            skipped['STAY_TIMES_MISSING_OR_INVALID'] += 1
            continue
        if lo > hi:
            skipped['STAY_OUTTIME_BEFORE_INTIME'] += 1
            continue
        records = by_stay.pop(stay['stay_id'], [])
        for e in records:
            beyond = max(lo - e['start'][0], e['start'][1] - hi, 0)
            if beyond:
                outside.append(beyond)
        first = min([lo] + [e['start'][0] for e in records])
        last = max([hi] + [e['start'][1] for e in records])
        periods.add((first, last, COVERAGE_BASIS if (first, last) == (lo, hi) else EXTENDED_COVERAGE_BASIS))
    require(not by_stay, 'EVENT_STAY_WITHOUT_ICU_STAY')
    return ([{'source': SOURCE, 'lo': lo, 'hi': hi, 'basis': basis} for lo, hi, basis in sorted(periods)],
            skipped, outside)


class Run:
    """One conversion's fixed inputs: the manifests, dimensions, closure and context."""

    def __init__(self, input_dir, dataset_id):
        self.input_dir, self.paths = Path(input_dir), source_paths(input_dir)
        self.dataset_id = dataset_id
        self.manifest, itemids = read_manifest(self.paths['chartevents'])
        self.stays, self.items, self.dimension_manifests = dimensions(self.paths)
        self.context, self.context_id = context_for(dataset_id, self.manifest, self.dimension_manifests)
        self.namespace = self.context['dataset_namespace']
        self.ancestors, self.bases = closure(itemids, self.items)
        self.stays_of = defaultdict(list)
        for _, stay in self.stays.values():
            self.stays_of[stay['subject_id']].append(stay)
        self.unknown_items = len(itemids - set(self.items))

    def patients(self):
        return sorted(self.stays_of)

    def batches(self, work_dir, shards=16, patients=None):
        """Every patient's batch, patients with chart rows first (shard by shard), then those without."""
        paths = spill(self.paths['chartevents'], self.manifest, work_dir, shards, patients)
        seen = set()
        for pid, rows in patient_rows(paths, self.manifest['columns']):
            seen.add(pid)
            yield self.patient_batch(pid, rows)
        for pid in self.patients():
            if pid not in seen and (patients is None or pid in patients):
                yield self.patient_batch(pid, [])

    def patient_batch(self, pid, rows):
        """One patient: the store patient (points, coverage, excluded records), its series rows, a
        summary of each claim store, and counts."""
        entries = admit_patient(rows, manifest=self.manifest, stays=self.stays, items=self.items,
                                dataset_id=self.dataset_id)
        origin = origin_of(entries)
        groups = store_groups(entries)
        events, series, stores = [], [], []
        for (stay, part), chunk in groups.items():
            rows_ = bulk_rows(chunk, origin, pid, self.dataset_id)
            stores.append({'key': (pid, stay, part), 'claims': len(chunk), 'rows_sha256': rows_digest(rows_),
                           'strata': strata(chunk, part, origin)})
            for event, s in rows_:
                events.append(event)
                series.append(s)
        excluded, excluded_counts = excluded_records(entries, self.items)
        # A stay with no chart row at all is not covered by chartevents: no row shows the source
        # recording there. (D15 covers stays without *admitted* inputevents rows; this is stricter.)
        recorded = {e['raw']['stay_id'] for e in entries}
        stays = self.stays_of.get(pid, [])
        periods, skipped, outside = coverage([s for s in stays if s['stay_id'] in recorded], events)
        events.sort(key=lambda e: e['event_id'])
        series.sort(key=lambda s: s['event_id'])
        patient = {'patient_id': pid, 'clock_id': 'patient_' + pid, 'box_representable': True,
                   'events': events, 'coverage': periods}
        if excluded:
            patient['excluded'] = excluded
        counts = Counter({'rows': len(rows), 'events': len(events), 'claim_stores': len(stores)})
        counts.update(Counter('outcome:' + e['outcome'] for e in entries))
        counts.update(Counter('reason:' + r for e in entries for r in e['reasons']))
        counts.update({'excluded:' + k: v for k, v in excluded_counts.items()})
        counts.update({'coverage_skipped:' + k: v for k, v in skipped.items()})
        counts['events_outside_own_stay'] += len(outside)
        counts['stays_without_chart_rows'] += sum(s['stay_id'] not in recorded for s in stays)
        counts['series_without_micro_value'] += sum(s['value_micro'] is None for s in series)
        return {'patient_id': pid, 'patient': patient, 'series': series, 'stores': stores, 'counts': counts,
                'outside_us': outside}

    def claim_stores(self, pid, rows, keys):
        """The claim stores with these keys, rebuilt from the patient's rows exactly as the importer
        builds them, with each claim's provenance: the input of route (a)."""
        entries = admit_patient(rows, manifest=self.manifest, stays=self.stays, items=self.items,
                                dataset_id=self.dataset_id)
        origin = origin_of(entries)
        out = {}
        for (stay, part), chunk in store_groups(entries).items():
            if (pid, stay, part) not in keys:
                continue
            store = claim_store(pid, stay, part, chunk, origin, context_id=self.context_id,
                                dataset_id=self.dataset_id, namespace=self.namespace)
            out[(pid, stay, part)] = {'store': store, 'chunk': chunk, 'origin': origin,
                                      'provenance': {c['id']: provenance_of(e, origin)
                                                     for c, e in zip(store['claims'], chunk)}}
        return out

    def header(self):
        """The store ingest's header fields: dataset and closure, with a version binding both."""
        closure_ = {c: sorted(a) for c, a in sorted(self.ancestors.items())}
        bases = dict(sorted(self.bases.items()))
        version = PROFILE + ':' + ei.digest(ei.canonical({'ancestors': closure_, 'bases': bases}))
        return {'profile': 'execution-store-ingest-2.0', 'dataset_id': self.dataset_id + ':' + CONCEPT_BASIS,
                'closure': {'version': version, 'ancestors': closure_, 'bases': bases}}


def strata(chunk, part, origin):
    """The sample strata a claim store covers: each (item, unit), each value shape (trailing zeros
    included), whether some value has no exact micro-unit integer, whether the store is a stay's first
    or a later part, and whether it holds the patient's clock-origin row. These steer the graph route
    to claim-field and RDF round-trip variety; they are not a test of the shared row mapping."""
    out = {('item_unit', e['raw']['itemid'], e['raw']['valueuom']) for e in chunk}
    out |= {('value_shape', value_shape(e['raw']['valuenum'])) for e in chunk}
    out |= {('value_micro_null', micro_value(claims.decimal_value(e['raw']['valuenum'])) is None) for e in chunk}
    out.add(('part', 'first' if part == 0 else 'later'))
    out.add(('origin_row', any(e['record_id'] == origin['source_record_id'] for e in chunk)))
    return sorted(out)


def main(argv=None):
    """Route (b) over local files, writing aggregate counts only (patients may be limited)."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--dataset-id', required=True)
    parser.add_argument('--shards', type=int, default=16)
    args = parser.parse_args(argv)
    t = time.perf_counter()
    run = Run(args.input_dir, args.dataset_id)
    totals = Counter()
    with tempfile.TemporaryDirectory() as work:
        for batch in run.batches(work, args.shards):
            totals.update(batch['counts'])
    print(json.dumps({'profile': PROFILE, 'counts': dict(sorted(totals.items())),
                      'seconds': round(time.perf_counter() - t, 2)}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
