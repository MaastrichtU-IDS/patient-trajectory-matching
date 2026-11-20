"""A letter mention's time, normalised from the letter's own text and dates: the range of rule `normalised_v1` (E0).

Since D30 the synthetic converter times every letter mention from birth (or an earlier record) to the letter: a letter
says a finding happened at or before it, not when. Most letters say more ("a febrile seizure (about 3 weeks ago)",
"Background: myoclonic jerks at 1 years 3 months"). This module reads that: given a letter's text, one mention's span,
the letter's date, the patient's birth date and the date of the same source's previous letter, it returns the day range
the mention's finding happened in, or none. It is deterministic and reads nothing else: no annotation but the span, and
none of the generator's truth, data-quality cases or time-expression annotations (a test checks the source and the
behaviour).

**Where the time is read.** In the mention's clause: the letter text from the last clause boundary before the span (a
`.`, `;` or `:` followed by white space) to the first one after it. The generator's `time_expression` annotation is a
verbatim substring of that clause for every present mention about the patient (checked by E0's scoring tool), but it is
the generator's pairing of an expression with its mention, so it is never read: the clause is parsed instead.

**The rules** (`RULES`). Each is widened to the writer's rounding, under either reading of a month or a year (calendar,
or fixed: 365-day years and 30.44-day months), never to the reader's assumption (the design study's lesson):
- an explicit date, day first (`19-10-2019`, `19/10/2019`, `19.10.2019`) or ISO (`2019-10-19`): that day;
- `this week`: the letter's day and the 6 days before it;
- `about a week ago`, `about N weeks ago`: 7N days before the letter, plus or minus 4 days;
- `at [around] N days`: day N of life, plus or minus 1 day (counting from day 0 or day 1);
- `at [around] N months`: N months of age rounded to the nearest month, plus or minus half a month (the calendar month
  plus or minus 16 days, or (N +- 1/2) x 30.44 days);
- `at [around] N years M months`: the same, M months after N years;
- `at [around] N years`: the whole year of age, N to N + 1 years. A writer who rounds months prints "N years" from N years
  to N years 11.5 months (the generator's `age_text` does), so the naive reading, about N years, would be wrong;
- with no expression in the clause, a "since the last review" cue: the mention followed by `was started`, or preceded by
  `This confirms` or `Gestart met`: from the start of the same source's previous letter to the letter. With no previous
  letter, none.

None, with a reason, when: the clause holds expressions of more than one range (`several_expressions`); the mention is
followed by `was stopped` (`stop_cue`: the clause's time is the stop, not the finding, and a medicine's mention is timed
by its start); there is no expression and no cue (`no_expression`); a date is not a calendar date (`invalid_date`).
A genetic result ("... result: pathogenic SCN1A variant") and a reclassification are not cues: the store times a genetic
result by its sample, which may precede the previous letter.
"""
from __future__ import annotations

from datetime import date, timedelta
import math
import re

RULE = 'normalised_v1'
MONTH = 30.44  # the fixed reading's month, in days
RULES = {
    'date': 'an explicit date, day first (dd-mm-yyyy, dd/mm/yyyy, dd.mm.yyyy) or ISO (yyyy-mm-dd): that day',
    'this_week': 'this week: the letter\'s day and the 6 days before it',
    'weeks_ago': 'about a week ago, about N weeks ago: 7N days before the letter, plus or minus 4 days',
    'age_days': 'at [around] N days: day N of life, plus or minus 1 day',
    'age_months': ('at [around] N months: rounded to the nearest month, the hull of the calendar month N plus or minus '
                   '16 days and (N +- 1/2) x 30.44 days, from birth'),
    'age_years_months': 'at [around] N years M months: as at N months, M months after N years (12N + M calendar months, '
                        'or 365N days plus (M +- 1/2) x 30.44 days)',
    'age_years': ('at [around] N years: the whole year of age, the hull of the calendar years N to N + 1 (less a day) and '
                  'days 365N to 365N + 364'),
    'since_previous_letter': ('no expression, and the mention followed by "was started" or preceded by "This confirms" or '
                              '"Gestart met": from the start of the same source\'s previous letter to the letter'),
}
REASONS = ('several_expressions', 'stop_cue', 'no_expression', 'no_previous_letter', 'invalid_date')

_BOUNDARY = re.compile(r'[.;:](?=\s|$)')
_DATE_DMY = re.compile(r'(?<![\d.\-/])(\d{1,2})([-/.])(\d{1,2})\2(\d{4})(?![\d\-/]|\.\d)')
_DATE_ISO = re.compile(r'(?<![\d.\-/])(\d{4})-(\d{2})-(\d{2})(?![\d\-/]|\.\d)')
_THIS_WEEK = re.compile(r'\bthis week\b', re.IGNORECASE)
_WEEKS_AGO = re.compile(r'\babout (a|\d+) weeks? ago\b', re.IGNORECASE)
_AGE = re.compile(r'\bat (?:around |about )?(\d+) (days?|months?|years?)(?: (\d+) months?)?\b', re.IGNORECASE)
_STOP_AFTER = re.compile(r'\s+was stopped\b', re.IGNORECASE)
_STARTED_AFTER = re.compile(r'\s+was started\b', re.IGNORECASE)
_CUE_BEFORE = re.compile(r'\b(?:This confirms|Gestart met)\s+$', re.IGNORECASE)

# Planted defects for E0's rules (rule 6, each must be caught); None in every real run. `naive_years`: "at N years" read
# as about N years (half a month after the birthday) instead of the whole year of age; `month_off_by_one`: N months read
# as N + 1; `result_cue`: a genetic result ("result:") and a reclassification read as since the previous letter;
# `stop_ignored`: the clause's time applied to a mention followed by "was stopped".
_DEFECT = None
DEFECTS = ('naive_years', 'month_off_by_one', 'result_cue', 'stop_ignored')


def day_of(text):
    """The calendar day of an ISO date or minute time ('2019-10-19' or '2019-10-19T23:59')."""
    return date.fromisoformat(text[:10])


def add_months(day, n):
    """The calendar date n months after `day`, its day of the month clamped to the month's last."""
    y, m = divmod(day.month - 1 + n, 12)
    year, month = day.year + y, m + 1
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def clause(text, span):
    """(start, end) of the clause holding the span: from the last boundary at or before its start to the first at or
    after its end."""
    s, e = span
    lo = max([m.end() for m in _BOUNDARY.finditer(text) if m.end() <= s] or [0])
    after = _BOUNDARY.search(text, e)
    return lo, after.start() if after else len(text)


def _age_range(birth, n, unit, extra):
    """(first day, last day) of an age written as `at N <unit>[ M months]`, under both readings of a month and a year."""
    unit = unit.lower().rstrip('s')
    defect = _DEFECT
    if unit == 'day':
        return birth + timedelta(days=n - 1), birth + timedelta(days=n + 1), 'age_days'
    if unit == 'month' or extra is not None:
        years, months = (0, n) if unit == 'month' else (n, int(extra))
        if defect == 'month_off_by_one':
            months += 1
        fixed_lo = 365 * years + math.floor((months - 0.5) * MONTH)
        fixed_hi = 365 * years + math.ceil((months + 0.5) * MONTH)
        calendar = add_months(birth, 12 * years + months)
        lo = min(birth + timedelta(days=fixed_lo), calendar - timedelta(days=16))
        hi = max(birth + timedelta(days=fixed_hi), calendar + timedelta(days=16))
        return lo, hi, 'age_months' if unit == 'month' else 'age_years_months'
    if defect == 'naive_years':
        return (birth + timedelta(days=365 * n), max(birth + timedelta(days=365 * n + 16),
                                                     add_months(birth, 12 * n) + timedelta(days=16)), 'age_years')
    lo = min(birth + timedelta(days=365 * n), add_months(birth, 12 * n))
    hi = max(birth + timedelta(days=365 * n + 364), add_months(birth, 12 * (n + 1)) - timedelta(days=1))
    return lo, hi, 'age_years'


def expressions(text, letter, birth):
    """Every time expression in `text` (one clause) with its range: [(rule, matched text, first day, last day)], in order
    of position. `letter` is the letter's (first day, last day), `birth` the birth date. An invalid date gives the rule
    `invalid_date` and no range."""
    found = []
    for m in _DATE_DMY.finditer(text):
        try:
            d = date(int(m[4]), int(m[3]), int(m[1]))
        except ValueError:
            found.append((m.start(), 'invalid_date', m[0], None, None))
            continue
        found.append((m.start(), 'date', m[0], d, d))
    for m in _DATE_ISO.finditer(text):
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            found.append((m.start(), 'invalid_date', m[0], None, None))
            continue
        found.append((m.start(), 'date', m[0], d, d))
    for m in _THIS_WEEK.finditer(text):
        found.append((m.start(), 'this_week', m[0], letter[0] - timedelta(days=6), letter[1]))
    for m in _WEEKS_AGO.finditer(text):
        n = 1 if m[1].lower() == 'a' else int(m[1])
        found.append((m.start(), 'weeks_ago', m[0], letter[0] - timedelta(days=7 * n + 4),
                      letter[1] - timedelta(days=7 * n - 4)))
    for m in _AGE.finditer(text):
        lo, hi, rule = _age_range(birth, int(m[1]), m[2], m[3])
        found.append((m.start(), rule, m[0], lo, hi))
    return [x[1:] for x in sorted(found, key=lambda x: x[0])]


def normalise(text, span, letter, birth_date, previous=None):
    """The day range of the finding one mention states, read from its letter: a dict with `earliest` and `latest` (ISO
    minute times on the patient's clock, the first day's 00:00 and the last day's 23:59), `rule` (a key of `RULES`),
    `anchor` (`birth`, `letter`, `previous_letter` or `calendar`) and `expression` (the text read, or the cue); or a
    dict with only `reason` (one of `REASONS`).

    `text` is the letter's text and `span` the mention's [start, end) in it; `letter` and `previous` (the same source's
    previous letter, or None) are each {'earliest', 'latest'} ISO times, as a record's `occurred` writes them;
    `birth_date` is ISO."""
    birth = day_of(birth_date)
    letter_days = (day_of(letter['earliest']), day_of(letter['latest']))
    lo, hi = clause(text, span)
    s, e = span
    before, after = text[lo:s], text[e:hi]
    defect = _DEFECT
    if _STOP_AFTER.match(after) and defect != 'stop_ignored':
        return {'reason': 'stop_cue'}
    found = expressions(text[lo:hi], letter_days, birth)
    if any(f[0] == 'invalid_date' for f in found):
        return {'reason': 'invalid_date'}
    ranges = {(f[2], f[3]) for f in found}
    if len(ranges) > 1:
        return {'reason': 'several_expressions'}
    if found:
        rule, matched, first, last = found[0]
        anchor = {'date': 'calendar', 'this_week': 'letter', 'weeks_ago': 'letter'}.get(rule, 'birth')
        return _out(first, last, rule, anchor, matched)
    cue = _STARTED_AFTER.match(after) or _CUE_BEFORE.search(before)
    if defect == 'result_cue' and not cue:
        cue = re.search(r'result:\s*$', text[max(0, lo - 20):s]) or re.search(r'reclassified the\s+$', before)
    if not cue:
        return {'reason': 'no_expression'}
    if previous is None:
        return {'reason': 'no_previous_letter'}
    return _out(day_of(previous['earliest']), letter_days[1], 'since_previous_letter', 'previous_letter',
                cue[0].strip())


def _out(first, last, rule, anchor, matched):
    return {'earliest': f'{first.isoformat()}T00:00', 'latest': f'{last.isoformat()}T23:59', 'rule': rule,
            'anchor': anchor, 'expression': matched}
