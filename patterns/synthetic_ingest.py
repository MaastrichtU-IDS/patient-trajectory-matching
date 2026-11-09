"""Convert the synthetic paediatric cohort into the execution-store ingest contract.

The cohort (`demos/mockups/synthetic/generate.py`) has two layers: the generator's truth and
the records the sources hold. This converter reads only the records, as a real source
adapter would (docs/execution-store.md, "Synthetic converter"):

- Selection is retrospective and takes the latest version: a record another record supersedes
  is dropped, and `available_at` does not select (it is kept as the event's `available_at`). The mode is
  recorded in the ingest's `provenance.selection` and carried into the snapshot manifest.
- A record whose status does not assert performance (`ordered` tests and procedures) is
  skipped and counted, so a requested EEG never matches a step asking for an EEG.
- A coded record becomes one event whose direct concept is the cohort concept carrying that
  (system, code). Codes no concept carries (the cohort's deliberate local codes) are skipped
  and counted, never guessed.
- A clinic, referral or discharge letter contributes one point event per mention asserted
  `present` of the `patient`. Negated, hypothetical, possible and family-history mentions
  are skipped. A mention is timed by its letter, the only time the record states.
- Event identifiers are `source/record_id`, with `#m<i>` for a letter's i-th mention: the source is
  kept in the key as a safeguard, and the store refuses duplicate event ids.
- Times are the record's `occurred` earliest and latest, read as patient-local wall-clock
  times and counted in microseconds from 0001-01-01T00:00. The record's `recorded_at`,
  `available_at` and `status` become the event's typed record fields (D26), the rest of the
  record's provenance its free-form `evidence`; a stop's evidence keeps its own fields as written.
- A medication order is an exposure from the order. A later `stopped` order for the same
  medication closes the latest open order before it; an order never closed is ongoing.
  A stop with no order to close is skipped and counted. Dispensings are point events.
- Coverage is each source's record span for the patient: from the earliest `occurred` to the
  latest over every record the source holds, converted or not (basis `record_span`). It is
  inferred, and gaps inside the span are not represented.
- Ancestors are the transitive closure of each concept's parents and classes.

Nothing here reads `truth`, `quality` or a record's `truth_event_id`.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

INGEST_PROFILE = 'execution-store-ingest-2.0'
ORIGIN = datetime(1, 1, 1)
MENTION_KINDS = ('note',)
# Record statuses that assert the coded thing happened. `ordered` (a test or procedure requested,
# not yet done) does not: an ordered EEG must not match a step asking for an EEG.
PERFORMED = (None, 'active', 'final', 'amended', 'stopped')
SELECTION = {'mode': 'retrospective', 'version': 'latest', 'available_at': 'not_applied',
             'note': ('every record held at the cohort snapshot, superseded versions dropped; availability '
                      'times are kept as each event\'s available_at but do not select records')}
CONVERTER = Path(__file__).resolve()


def micros(text):
    """Microseconds from 0001-01-01T00:00 to a patient-local ISO time ('2019-10-19T23:59')."""
    delta = datetime.fromisoformat(text) - ORIGIN
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def bounds(record):
    occurred = record['occurred']
    return [micros(occurred['earliest']), micros(occurred['latest'])]


def ancestor_closure(concepts):
    """{concept: sorted strict ancestors} through parents and classes."""
    direct = {cid: set(c.get('parents', [])) | set(c.get('classes', [])) for cid, c in concepts.items()}
    closure = {}
    def walk(cid, path):
        if cid in closure:
            return closure[cid]
        if cid in path:
            raise ValueError(f'CONCEPT_CYCLE:{cid}')
        out = set()
        for parent in direct[cid]:
            out |= {parent} | walk(parent, path | {cid})
        closure[cid] = out
        return out
    for cid in sorted(direct):
        walk(cid, frozenset())
    return {cid: sorted(closure[cid]) for cid in sorted(closure)}


def _key(record):
    """Event identifier: source and record identifier. The generator makes record identifiers unique, but
    the source is kept in the key as a safeguard (an earlier generator reused C029-R0271)."""
    return f"{record['source']}/{record['record_id']}"


def _record(record):
    """The record fields the store keeps as typed event columns (D26): when the source wrote the record down and
    when a researcher's system could first see it, on the patient-local clock, and its status where it has one."""
    out = {'recorded_at': micros(record['recorded_at']), 'available_at': micros(record['available_at'])}
    if record.get('status') is not None:
        out['status'] = record['status']
    return out


def _evidence(record, own=True, **extra):
    """Free-form provenance of a record. For the event's own record (`own`), the fields `_record` types are left
    out; the evidence of another record (a stop) keeps them, as its source wrote them."""
    out = {'record_id': record['record_id'], 'source': record['source'], 'record_kind': record['kind'],
           'occurred': {k: record['occurred'][k] for k in ('earliest', 'latest', 'precision', 'as_written')}}
    if not own:
        out.update(recorded_at=record['recorded_at'], available_at=record['available_at'])
    for key in ('coding', 'value', 'unit', 'supersedes') + (() if own else ('status',)):
        if record.get(key) is not None:
            out[key] = record[key]
    out.update(extra)
    return out


def convert_patient(patient, codes):
    """One patient's ingest record and the counts of what was skipped and why."""
    pid = patient['patient_id']
    events, skipped = [], {}
    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1
    orders, stops = [], []
    superseded = {(r['source'], r['supersedes']) for r in patient['records'] if r.get('supersedes')}
    for record in patient['records']:
        if (record['source'], record['record_id']) in superseded:
            skip(f"superseded_{record['kind']}")
            continue
        if record.get('status') not in PERFORMED:
            skip(f"status_{record['status']}_{record['kind']}")
            continue
        if record['kind'] in MENTION_KINDS:
            for i, m in enumerate(record.get('mentions', [])):
                if m['assertion'] != 'present' or m['experiencer'] != 'patient':
                    skip(f"mention_{m['assertion']}_{m['experiencer']}")
                    continue
                events.append({'event_id': f"{_key(record)}#m{i}", 'kind': 'mention', 'source': record['source'],
                               'concepts': [m['concept']], 'start': bounds(record), **_record(record),
                               'evidence': _evidence(record, mention={'span': m['span'], 'text': m['text'],
                                                                     'assertion': m['assertion'],
                                                                     'experiencer': m['experiencer']})})
            continue
        coding = record.get('coding')
        if not coding:
            skip(f"uncoded_{record['kind']}")
            continue
        concept = codes.get((coding['system'], coding['code']))
        if concept is None:
            skip(f"unknown_code_{coding['system']}")
            continue
        if record['kind'] == 'medication_order':
            (stops if record.get('status') == 'stopped' else orders).append((concept, record))
            continue
        events.append({'event_id': _key(record), 'kind': record['kind'], 'source': record['source'],
                       'concepts': [concept], 'start': bounds(record), **_record(record), 'evidence': _evidence(record)})
    # Exposures: each stop closes the latest still-open order for the same medication before it.
    open_orders = sorted(orders, key=lambda o: (bounds(o[1]), o[1]['record_id']))
    closing = {}
    for concept, stop in sorted(stops, key=lambda s: (bounds(s[1]), s[1]['record_id'])):
        lo, hi = bounds(stop)
        candidates = [o for o in open_orders if o[0] == concept and o[1]['record_id'] not in closing
                      and bounds(o[1])[0] <= hi]
        if not candidates:
            skip('medication_stop_without_order')
            continue
        closing[candidates[-1][1]['record_id']] = stop
    for concept, order in open_orders:
        start = bounds(order)
        stop = closing.get(order['record_id'])
        if stop is None:
            end, extra = [start[0], None], {}
        else:
            lo, hi = bounds(stop)
            end_lo = max(lo, start[0])
            end = [end_lo, max(hi, end_lo)]
            extra = {'stop': _evidence(stop, own=False, attributes=stop.get('attributes'))}
        events.append({'event_id': _key(order), 'kind': 'medication_exposure', 'source': order['source'],
                       'concepts': [concept], 'start': start, 'end': end, **_record(order),
                       'evidence': _evidence(order, **extra)})
    spans = {}
    for record in patient['records']:
        lo, hi = bounds(record)
        old = spans.get(record['source'])
        spans[record['source']] = (lo, hi) if old is None else (min(old[0], lo), max(old[1], hi))
    coverage = [{'source': s, 'lo': lo, 'hi': hi, 'basis': 'record_span'} for s, (lo, hi) in sorted(spans.items())]
    events.sort(key=lambda e: e['event_id'])
    return {'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': events, 'coverage': coverage}, skipped


def _codes(cohort):
    codes = {}
    concepts = cohort['concepts']
    for cid in sorted(concepts):
        for c in concepts[cid]['codings']:
            key = (c['system'], c['code'])
            if key in codes and codes[key] != cid:
                raise ValueError(f'AMBIGUOUS_CODE:{key[0]}:{key[1]}')
            codes[key] = cid
    return codes


def synthetic_header(cohort):
    """The ingest header (profile, dataset id, closure) of a cohort, for `execution_store.StoreBuilder`."""
    concepts = cohort['concepts']
    _codes(cohort)  # refuse an ambiguous coding before anything is converted
    hierarchy = json.dumps({cid: [concepts[cid].get('parents', []), concepts[cid].get('classes', [])]
                            for cid in sorted(concepts)}, sort_keys=True, separators=(',', ':'))
    version = f"{cohort['dataset_id']}+{hashlib.sha256(hierarchy.encode()).hexdigest()[:16]}"
    return {'profile': INGEST_PROFILE, 'dataset_id': cohort['dataset_id'],
            'closure': {'version': version, 'ancestors': ancestor_closure(concepts)}}


def iter_patients(cohort, skipped=None):
    """Each converted patient in patient_id order, one at a time; the skip counts accumulate in `skipped`."""
    codes = _codes(cohort)
    skipped = {} if skipped is None else skipped
    for patient in sorted(cohort['patients'], key=lambda p: p['patient_id']):
        converted, why = convert_patient(patient, codes)
        for k, v in why.items():
            skipped[k] = skipped.get(k, 0) + v
        yield converted


def synthetic_provenance(cohort, skipped):
    """The ingest provenance, given the skip counts `iter_patients` accumulated."""
    late = sum(1 for p in cohort['patients'] for r in p['records'] if r['available_at'] > r['occurred']['latest'])
    selection = {**SELECTION, 'records_available_after_occurrence': late}
    return {'converter': 'patterns/synthetic_ingest.py',
            'converter_sha256': hashlib.sha256(CONVERTER.read_bytes()).hexdigest(),
            'generator': cohort['generator'], 'selection': selection,
            'skipped': dict(sorted(skipped.items()))}


def synthetic_ingest(cohort):
    """The whole cohort as an ingest document (profile execution-store-ingest-2.0)."""
    header = synthetic_header(cohort)
    skipped = {}
    patients = list(iter_patients(cohort, skipped))
    return {**header, 'provenance': synthetic_provenance(cohort, skipped), 'patients': patients}
