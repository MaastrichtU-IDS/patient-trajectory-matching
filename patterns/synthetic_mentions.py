"""Letter mentions timed from their letter's text: the synthetic converter's option `normalised_v1` (E0), as a wrapper.

The converter (`synthetic_ingest.py`) times each letter mention from birth, or an earlier record, to the letter (D30,
`at_or_before_letter`). With `mention_time='normalised_v1'` this wrapper calls the converter's own `convert_patient`
unchanged (or a wrapper of it with the same signature, such as `store_conditions_gene`'s) and then narrows the start of
each mention event (`#m<i>`):

- the range `mention_time.normalise` reads from the letter's text, the mention's span, the letter's date, the birth date
  and the date of the same source's previous letter (the note of that source the patient holds whose `occurred` starts
  latest before this one's), clamped to D30's range: start = [max(floor, earliest), min(letter's latest, latest)];
- if the normaliser gives no range, or one that does not meet D30's range, D30's range, counted by reason.

So a mention is never timed wider than under D30, nor after its letter. Nothing else changes: the same events, concepts,
evidence (but for a `mention_time` entry on each narrowed mention), coverage, series and excluded records. The provenance
names the rule (`mention_time`) and adds `mention_normaliser`: the two modules' sha256 and the counts, by rule and by
fallback reason. Both are in the ingest's provenance, so in the snapshot's input digest and identity.

With any other `mention_time` the wrapper is the identity: it returns the converter's output unchanged, so a default
build's tables, provenance and snapshot are byte-identical with or without it.

Why a wrapper (D38's precedent, `store_conditions_gene`): the converter and the generator stay byte-identical, so no
committed report that binds them goes stale and the kept builds stay reusable (D34). No build loads this module, nor
any matching code: the scaling build worker imports the converter alone.

Nothing here reads the generator's truth, its data-quality cases, a record's or mention's truth link, the mention
annotations' time expressions or a record's `mentions` (the converter reads the annotations; this reads the span the
converter puts in each mention event's evidence). A test checks the source and the behaviour.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import mention_time as mt
from . import synthetic_ingest as si

NORMALISED = 'normalised_v1'
MENTION_TIMES = si.MENTION_TIMES + (NORMALISED,)
RULE_TEXT = ('normalised_v1: each letter mention\'s start is the day range its letter\'s text gives it '
             '(patterns/mention_time.py, read from the mention\'s clause, the letter\'s date, the birth date and the '
             'same source\'s previous letter), clamped to at_or_before_letter_v1\'s range (from birth, or an earlier '
             'record, to the letter); with no range, or one outside that range, at_or_before_letter_v1')
MODULES = ('patterns/mention_time.py', 'patterns/synthetic_mentions.py')
_HERE = Path(__file__).resolve().parent


def module_digests():
    return {m: hashlib.sha256((_HERE / Path(m).name).read_bytes()).hexdigest() for m in MODULES}


def previous_letters(patient):
    """{(source, record_id): the `occurred` of the same source's previous note, or None}, for every note."""
    notes = sorted((r for r in patient['records'] if r['kind'] in si.MENTION_KINDS),
                   key=lambda r: (r['source'], si.micros(r['occurred']['earliest']), r['record_id']))
    out = {}
    for r in notes:
        start = si.micros(r['occurred']['earliest'])
        earlier = [n for n in notes if n['source'] == r['source'] and si.micros(n['occurred']['earliest']) < start]
        out[(r['source'], r['record_id'])] = earlier[-1]['occurred'] if earlier else None
    return out


def narrow(patient, converted, counts):
    """The converted patient with each mention event's start narrowed by `normalised_v1`; `counts` accumulates, per
    mention event, `normalised_<rule>` or `fallback_<reason>` (`outside_d30` for a range that does not meet D30's)."""
    records = {(r['source'], r['record_id']): r for r in patient['records']}
    previous = previous_letters(patient)
    events = []
    for event in converted['events']:
        if event['kind'] != 'mention':
            events.append(event)
            continue
        ev = event['evidence']
        key = (ev['source'], ev['record_id'])
        record = records[key]
        got = mt.normalise(record['text'], ev['mention']['span'], record['occurred'], patient['birth_date'],
                           previous[key])
        floor, top = event['start']
        if 'reason' in got:
            reason = got['reason']
        else:
            lo, hi = max(floor, si.micros(got['earliest'])), min(top, si.micros(got['latest']))
            if lo <= hi:
                counts[f"normalised_{got['rule']}"] = counts.get(f"normalised_{got['rule']}", 0) + 1
                timing = {k: got[k] for k in ('rule', 'anchor', 'expression')}
                events.append({**event, 'start': [lo, hi], 'evidence': {**ev, 'mention_time': timing}})
                continue
            reason = 'outside_d30'
        counts[f'fallback_{reason}'] = counts.get(f'fallback_{reason}', 0) + 1
        events.append(event)
    return {**converted, 'events': events}


def convert_patient(patient, codes, mention_time='at_or_before_letter', series=False, base=si, counts=None):
    """`base.convert_patient` (the converter's, by default), with each mention narrowed under `normalised_v1`; any other
    `mention_time` is passed to it unchanged and its output returned as it is."""
    if mention_time != NORMALISED:
        return base.convert_patient(patient, codes, mention_time, series)
    out, skipped = base.convert_patient(patient, codes, 'at_or_before_letter', series)
    return narrow(patient, out, {} if counts is None else counts), skipped


def iter_patients(cohort, skipped=None, mention_time='at_or_before_letter', series=False, base=si, counts=None):
    """`synthetic_ingest.iter_patients` through this module's `convert_patient`; the normaliser's counts accumulate in
    `counts`."""
    codes = si._codes(cohort)
    skipped = {} if skipped is None else skipped
    counts = {} if counts is None else counts
    for patient in sorted(cohort['patients'], key=lambda p: p['patient_id']):
        converted, why = convert_patient(patient, codes, mention_time, series, base, counts)
        for k, v in why.items():
            skipped[k] = skipped.get(k, 0) + v
        yield converted


def synthetic_provenance(cohort, skipped, mention_time='at_or_before_letter', series=False, base=si, counts=None):
    """`base.synthetic_provenance`; under `normalised_v1` its `mention_time` names this rule, and `mention_normaliser`
    holds the modules' digests and the counts `iter_patients` accumulated."""
    if mention_time != NORMALISED:
        return base.synthetic_provenance(cohort, skipped, mention_time, series)
    out = base.synthetic_provenance(cohort, skipped, 'at_or_before_letter', series)
    out['mention_time'] = RULE_TEXT
    out['mention_normaliser'] = {'modules': module_digests(), 'counts': dict(sorted((counts or {}).items()))}
    return out


def synthetic_ingest(cohort, mention_time='at_or_before_letter', series=False, base=si):
    """The whole cohort as an ingest document (profile execution-store-ingest-2.0)."""
    header = si.synthetic_header(cohort)
    skipped, counts = {}, {}
    patients = list(iter_patients(cohort, skipped, mention_time, series, base, counts))
    return {**header, 'provenance': synthetic_provenance(cohort, skipped, mention_time, series, base, counts),
            'patients': patients}
