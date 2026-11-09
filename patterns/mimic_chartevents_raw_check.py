"""The raw-row check for chartevents route (b): every chart row against what the ingest made of it.

The sampled graph route (`mimic_chartevents_check`) shares the row mapping with route (b): `store_row`,
`value_columns`, `provenance_of`, `micros` and the same `describe()` claim. A defect there reaches both
routes alike, and the graph check cannot see it. This check is the independent one, and it is required
over **every** row of the file. It imports nothing from `mimic_chartevents`, `mimic_measurement_import`,
`patient_local` or `measurement_claims`. It reads the CSV itself and recomputes from each raw row:

- the item class, written again as a literal namespace plus the row's `itemid`;
- the time, by `datetime` arithmetic from 0001-01-01T00:00 on the raw `charttime`;
- the recorded time from `storetime` the same way (null when empty);
- the value spelling (`valuenum`), the unit (`valueuom`), and the micro-unit integer by string parsing
  and integer arithmetic on the spelling (no `Decimal`);
- the record number in the event identifier, and the event's kind and source.

It also accounts for every row: each is an event (its first occurrence, its item known), an exact
duplicate of an earlier row, a row naming an item the dictionary does not hold, or an excluded
record whose item class and start (all time when the charttime is unreadable) must appear in the
patient's excluded records, as a multiset. Which rows admission keeps is the importer's rule and is
not recomputed here; what is checked is that every row is accounted for once, and that every event
and every excluded record is the raw row's own content.

Results are counts and field names, never values.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime
import gzip
import hashlib
import io
from pathlib import Path
import re

ITEM_NAMESPACE = 'https://example.org/trajectory/mimic-record-item/'
SOURCE = 'chartevents:numeric_measurements'
KIND = 'recorded_measurement'
ALL_TIME = (0, 315_537_897_599_999_999)
_EPOCH = datetime(1, 1, 1)
_LABEL = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})')
_NUMBER = re.compile(r'([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?')
_LO, _HI = -2 ** 63, 2 ** 63 - 1


def raw_us(label):
    """Microseconds from 0001-01-01T00:00 to 'YYYY-MM-DD HH:MM:SS', or None if it is not that."""
    m = _LABEL.fullmatch(label)
    if not m:
        return None
    try:
        delta = datetime(*map(int, m.groups())) - _EPOCH
    except ValueError:
        return None
    return (delta.days * 86_400 + delta.seconds) * 1_000_000


def raw_micro(text):
    """The spelling times 10**6 if that is an exact integer within BIGINT, else None (string parsing)."""
    m = _NUMBER.fullmatch(text)
    if not m or not (m.group(2) or m.group(3)):
        return None
    sign, whole, fraction, exponent = m.group(1), m.group(2), m.group(3) or '', m.group(4)
    digits = (whole + fraction).lstrip('0') or '0'
    scale = (int(exponent) if exponent else 0) - len(fraction) + 6
    if scale >= 0:
        if digits != '0' and len(digits) + scale > 20:
            return None
        value = int(digits + '0' * scale) if digits != '0' else 0
    else:
        kept, dropped = (digits[:scale], digits[scale:]) if len(digits) > -scale else ('', digits.rjust(-scale, '0'))
        if dropped.strip('0'):
            return None
        value = int(kept or '0')
    value = -value if sign == '-' else value
    return value if _LO <= value <= _HI else None


def _rows(path):
    path = Path(path)
    raw = gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')
    with io.TextIOWrapper(raw, encoding='utf-8', newline='') as text:
        reader = csv.reader(text, strict=True)
        header = next(reader)
        for number, values in enumerate(reader, 1):
            yield number, dict(zip(header, values)), values


class RawRowCheck:
    """Collect what the ingest made of each patient (`add`), then read the file once (`verify`)."""

    def __init__(self):
        self.events, self.excluded, self.patients, self.repeated = {}, Counter(), set(), 0

    def add(self, patient, series):
        pid = patient['patient_id']
        self.patients.add(pid)
        values = {s['event_id']: s for s in series}
        for e in patient['events']:
            s = values.get(e['event_id'], {})
            number = e['evidence']['source_record_number']
            self.repeated += number in self.events
            self.events[number] = (pid, e['event_id'], e['kind'], e['source'], tuple(e['concepts']), tuple(e['start']),
                                   'end' in e, s.get('item_class'), s.get('value_lexical'), s.get('unit'),
                                   s.get('value_micro'), e['evidence'].get('recorded_at_us'))
        for x in patient.get('excluded', []):
            self.excluded[(pid, x['source'], x['concept'], x['start'][0], x['start'][1])] += 1

    def verify(self, chartevents, d_items):
        items = {row['itemid'] for _, row, _ in _rows(d_items)}
        seen, expected, fields = set(), Counter(), Counter()
        counts = Counter()
        for number, row, values in _rows(chartevents):
            pid = row['subject_id']
            if pid not in self.patients:
                continue
            counts['rows'] += 1
            key = hashlib.blake2b('\x1f'.join(values).encode('utf-8'), digest_size=16).digest()
            first = key not in seen
            seen.add(key)
            if number in self.events:
                counts['events_checked'] += 1
                (epid, eid, kind, source, concepts, start, has_end, item_class, value_lexical, unit, micro,
                 recorded) = self.events.pop(number)
                t = raw_us(row['charttime'])
                stored = raw_us(row['storetime']) if row['storetime'] else None
                want_class = ITEM_NAMESPACE + row['itemid']
                for name, ok in (('patient', epid == pid), ('first_occurrence', first), ('item_known', row['itemid'] in items),
                                 ('event_id', eid.rsplit('_', 1)[-1] == str(number)), ('kind', kind == KIND),
                                 ('source', source == SOURCE), ('item_class', concepts == (want_class,) and item_class == want_class),
                                 ('start', t is not None and start == (t, t)), ('point', not has_end),
                                 ('value_lexical', value_lexical == row['valuenum']), ('unit', unit == row['valueuom']),
                                 ('value_micro', micro == raw_micro(row['valuenum'])),
                                 ('recorded_at_us', recorded == stored)):
                    if not ok:
                        fields[name] += 1
            elif not first:
                counts['duplicate_rows'] += 1
            elif row['itemid'] not in items:
                counts['unknown_item_rows'] += 1
            else:
                t = raw_us(row['charttime'])
                lo, hi = (t, t) if t is not None else ALL_TIME
                expected[(pid, SOURCE, ITEM_NAMESPACE + row['itemid'], lo, hi)] += 1
                counts['excluded_expected'] += 1
        excluded_differences = sum(((expected - self.excluded) + (self.excluded - expected)).values())
        result = {'rows': counts['rows'], 'events_checked': counts['events_checked'],
                  'excluded_records_checked': counts['excluded_expected'],
                  'duplicate_rows': counts['duplicate_rows'], 'unknown_item_rows': counts['unknown_item_rows'],
                  'event_field_mismatches': dict(sorted(fields.items())),
                  'excluded_record_differences': excluded_differences,
                  'events_without_raw_row': len(self.events), 'events_naming_one_row_twice': self.repeated}
        result['passed'] = not (fields or excluded_differences or self.events or self.repeated)
        return result
