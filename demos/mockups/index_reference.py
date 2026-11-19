"""D46: an independent reference for the trajectory page's index periods, placed without the generator's truth.

The page places each event bound of an index period over its index view (`indexViewFor` in shared/core.js), whose
records are grouped by rule reconcile_v1 (the rule's text is in core.js and in the decision log, D46). This module
implements the same rule and the page's period resolution again, in Python, from their statements, so that the page's
windows can be compared with it patient by patient (D46 rule 3):

    python demos/mockups/index_reference.py compare COHORT.json WINDOWS.json   # prints a JSON summary; exit 1 on a mismatch

WINDOWS.json is `d46_windows.cjs windows`'s output: {"rules": {name: window}, "windows": {rule: {free_text: {patient_id:
{"start", "end"} | {"reason"}}}}}, times in whole minutes from birth (the page's engine grid), as the store request
carries them. A placed window must equal the reference's to the minute; an unplaced one must be unplaced here too.

Written separately from core.js, not translated from it line by line; what it shares with the page is the rule's text,
the page's item rules it restates (how an item is kinded, conceptualised and timed: `makeItem`, `timeOf`, `textSpan`)
and its period rules (`usable`, `eventTimes`, `pickOccurrence`, `windowFor`). It reads no `truth_event_id`, `truth` or
`quality` field: `strip` removes them before anything else runs. Synthetic data only.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import re
import sys

DAY = 86400
MONTH = 30.44
UNITS = {'second': 1 / 86400, 'minute': 1 / 1440, 'hour': 1 / 24, 'day': 1, 'week': 7, 'month': MONTH, 'year': 365.25}
PRECISION = {'minute': 0, 'day': 1, 'approximate': 2, 'month': 3}
GRID = 1440
INF = math.inf
MEASURED = {'measurement', 'diary_summary'}
MEDICATION = {'medication_order', 'dispensing'}
GENETIC = {'test_order', 'genetic_result', 'screening_result'}


def strip(cohort):
    """The cohort without the generator's truth: no `truth`, no `quality`, no `truth_event_id` on records or mentions."""
    out = json.loads(json.dumps(cohort))
    for p in out['patients']:
        p.pop('truth', None)
        p.pop('quality', None)
        for r in p['records']:
            r.pop('truth_event_id', None)
            for m in r.get('mentions') or []:
                m.pop('truth_event_id', None)
    return out


def to_days(amount, unit):
    return float(amount or 0) * UNITS.get(unit, 1)


def js_round(x):
    return math.floor(x + 0.5)


def to_grid(d):
    return js_round(d * GRID)


class Cohort:
    def __init__(self, data):
        self.concepts = data['concepts']
        self.codes = {(c['system'], c['code']): cid for cid, k in self.concepts.items() for c in k['codings']}
        self._up = {}

    def up(self, cid):
        """The concept and its ancestors by `parents` (the page's `ancestors`; drug classes are separate)."""
        if cid not in self._up:
            out, stack = set(), [cid]
            while stack:
                c = stack.pop()
                if c not in out:
                    out.add(c)
                    stack.extend(self.concepts.get(c, {}).get('parents', []))
            self._up[cid] = out
        return self._up[cid]

    def is_a(self, cid, target):
        return bool(cid) and target in self.up(cid)

    def compatible(self, a, b):
        return bool(a) and bool(b) and (self.is_a(a, b) or self.is_a(b, a))

    def concept_of(self, coding):
        return self.codes.get((coding['system'], coding['code'])) if coding else None

    def kind_of(self, cid):
        return self.concepts.get(cid, {}).get('kind')

    def most_specific(self, cids):
        best = None
        for c in cids:
            if best is None or len(self.up(c)) > len(self.up(best)):
                best = c
        return best


def uniq(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


class Patient:
    def __init__(self, cohort, p):
        self.c, self.p = cohort, p
        self.birth = datetime.fromisoformat(p['birth_date'] + 'T00:00')
        self.last_contact = self.day(p['follow_up']['last_contact'])

    def day(self, iso):
        at = datetime.fromisoformat(iso[:16]) if len(iso) > 10 else datetime.fromisoformat(iso[:10] + 'T00:00')
        delta = at - self.birth
        return (delta.days * DAY + delta.seconds) / DAY

    def span(self, r):
        o = r['occurred']
        return {'e': self.day(o['earliest']), 'l': self.day(o['latest']), 'precision': o['precision']}

    def text_span(self, note, m):
        nd, t = self.day(note['occurred']['earliest']), m.get('time_expression') or ''

        def out(e, l):
            return {'e': e, 'l': l, 'precision': 'approximate'}
        if 'this week' in t:
            return out(nd - 7, nd)
        if 'a week ago' in t:
            return out(nd - 10, nd - 4)
        g = re.search(r'about (\d+) weeks ago', t)
        if g:
            return out(nd - (int(g[1]) + 1) * 7, nd - max(0, int(g[1]) - 1) * 7)
        g = re.search(r'(\d{2})-(\d{2})-(\d{4})', t)
        if g:
            d = self.day(f'{g[3]}-{g[2]}-{g[1]}')
            return {'e': d, 'l': d, 'precision': 'day'}
        g = re.search(r'(\d+) (days?|months?|years?)(?: (\d+) months?)?', t)
        if g:
            n, unit = int(g[1]), g[2][0]
            d = n if unit == 'd' else n * MONTH if unit == 'm' else n * 365.25 + int(g[3] or 0) * MONTH
            spread = 45 if unit == 'y' else 30
            return out(js_round(d - spread), js_round(d + spread))
        return out(nd - 120, nd)

    # Rule reconcile_v1 (D46).
    def happenings(self):
        c, day = self.c, self.day
        rng = lambda r: (day(r['occurred']['earliest']), day(r['occurred']['latest']))  # noqa: E731
        meets = lambda a, b: a[0] <= b[1] and a[1] >= b[0]  # noqa: E731
        mid = lambda a: (a[0] + a[1]) / 2  # noqa: E731

        def nearest(cands, x):
            best = None
            for h in cands:  # strictly nearer replaces, so a tie keeps the happening made first
                if best is None or abs(mid(h['range']) - mid(x)) < abs(mid(best['range']) - mid(x)):
                    best = h
            return best

        def grow(h, x):
            h['range'] = (min(h['range'][0], x[0]), max(h['range'][1], x[1]))

        alone, findings, diary, genetic, notes, meds = [], [], [], [], [], {}
        for r in self.p['records']:
            k = r['kind']
            if k == 'note':
                notes.append(r)
            elif k in MEASURED:
                continue
            elif not r.get('occurred'):
                alone.append({'recs': [r], 'ms': [], 'concept': None})
            elif k in MEDICATION and c.concept_of(r.get('coding')):
                meds.setdefault(c.concept_of(r['coding']), []).append(r)
            elif k in GENETIC:
                genetic.append(r)
            elif k not in MEDICATION and c.concept_of(r.get('coding')):
                findings.append(r)
            elif r['source'] == 'diary' and k == 'diary_entry':
                diary.append(r)
            else:
                alone.append({'recs': [r], 'ms': [], 'concept': None})
        # 1. coded findings, diagnoses, procedures
        coded = []
        findings.sort(key=lambda r: (PRECISION[r['occurred']['precision']], day(r['occurred']['earliest']), r['record_id']))
        for r in findings:
            cid, x = c.concept_of(r['coding']), rng(r)
            h = nearest([k for k in coded if c.compatible(k['concept'], cid) and meets(k['head'], x)], x)
            if h:
                h['recs'].append(r)
                grow(h, x)
            else:
                coded.append({'recs': [r], 'ms': [], 'concept': cid, 'head': x, 'range': x})
        # 2. uncoded seizure-diary entries
        for r in diary:
            x = rng(r)
            h = nearest([k for k in coded if c.is_a(k['concept'], 'seizure') and meets(k['head'], x)], x)
            if h:
                h['recs'].append(r)
                grow(h, x)
            else:
                alone.append({'recs': [r], 'ms': [], 'concept': None})
        # 3. medication courses
        courses = []

        def rank(r):
            return 1 if r['kind'] == 'dispensing' else 0 if r.get('status') == 'active' else 2

        def supply(r):
            return (r.get('attributes') or {}).get('supply_days') or 30
        for cid, recs in meds.items():
            recs.sort(key=lambda r: (day(r['occurred']['earliest']), rank(r), r['record_id']))
            open_ = None
            for r in recs:
                at = day(r['occurred']['earliest'])
                reach = INF if open_ is None or open_['last'] is None else day(open_['last']['occurred']['earliest']) + supply(open_['last']) + 45
                if r['kind'] == 'dispensing':
                    if open_ is not None and at <= reach:
                        open_['recs'].append(r)
                        open_['last'] = r
                    else:
                        open_ = {'recs': [r], 'ms': [], 'concept': cid, 'ordered': False, 'last': r}
                        courses.append(open_)
                elif r.get('status') == 'active':
                    if open_ is not None and not open_['ordered'] and at <= reach:
                        open_['recs'].append(r)
                        open_['ordered'] = True
                    else:
                        open_ = {'recs': [r], 'ms': [], 'concept': cid, 'ordered': True, 'last': None}
                        courses.append(open_)
                elif open_ is not None:
                    open_['recs'].append(r)
                    open_ = None
                else:
                    courses.append({'recs': [r], 'ms': [], 'concept': cid, 'ordered': False, 'last': None})
        for k in courses:
            disp = [r for r in k['recs'] if r['kind'] == 'dispensing']
            stopped = any(r['kind'] == 'medication_order' and r.get('status') != 'active' for r in k['recs'])
            ends = [rng(r)[1] for r in k['recs']] + [day(r['occurred']['earliest']) + supply(r) for r in disp]
            if not disp and not stopped:
                ends.append(self.last_contact)
            k['range'] = (min(rng(r)[0] for r in k['recs']), max(ends))
        # 4. genetic tests
        tests = []
        genetic.sort(key=lambda r: (day(r['occurred']['earliest']), r['record_id']))
        for r in genetic:
            a = r.get('attributes') or {}
            g, x = a.get('gene') or a.get('gene_requested'), rng(r)
            h = nearest([k for k in tests if k['gene'] == g and meets(k['head'], x)], x) if g else None
            if h:
                h['recs'].append(r)
                grow(h, x)
            else:
                tests.append({'recs': [r], 'ms': [], 'gene': g, 'concept': f'gene:{g}' if g else None, 'head': x, 'range': x})
        # 5. letter mentions
        mentions = []
        for n in notes:
            for i, m in enumerate(n.get('mentions') or []):
                if m['experiencer'] == 'patient' and m['assertion'] in ('present', 'possible'):
                    t = self.text_span(n, m)
                    mentions.append((n, i, m, (t['e'], t['l'])))
        mentions.sort(key=lambda y: (y[3][1] - y[3][0], y[3][0], y[0]['record_id'], y[1]))
        targets = [k for k in coded + courses + tests if k['concept']]
        spoken = []
        for n, i, m, x in mentions:
            h = nearest([k for k in targets if c.compatible(k['concept'], m['concept']) and meets(k['range'], x)], x)
            if h:
                h['ms'].append((n, m))
                continue
            s = nearest([k for k in spoken if c.compatible(k['concept'], m['concept']) and meets(k['head'], x)], x)
            if s:
                s['ms'].append((n, m))
            else:
                spoken.append({'recs': [], 'ms': [(n, m)], 'concept': m['concept'], 'head': x, 'range': x})
        return coded + courses + tests + spoken + alone

    # The page's items (restated from makeItem and its provisional split), with only the fields periods read.
    def items(self):
        out = []
        for k in self.happenings():
            recs, ms = k['recs'], k['ms']
            coded_days = [self.day(r['occurred']['earliest']) for r in recs if r.get('coding') and r.get('occurred')]
            first = min(coded_days) if coded_days else None
            early = [] if first is None else [x for x in ms if x[1]['assertion'] == 'possible' and self.day(x[0]['occurred']['earliest']) < first]
            if early:
                it = self.item([], early)
                if it:
                    out.append(it)
            it = self.item(recs, [x for x in ms if not any(x is y for y in early)])
            if it:
                out.append(it)
        return out

    def item(self, recs, ms):
        c = self.c
        kinds = {r['kind'] for r in recs}
        structured = [r for r in recs if r.get('coding')]
        coded = uniq([x for x in (c.concept_of(r['coding']) for r in structured) if x])
        texted = uniq([m['concept'] for _, m in ms])
        every = uniq(coded + texted)
        it = {'free_text_only': not structured,
              'possible_only': not structured and len(ms) > 0 and all(m['assertion'] == 'possible' for _, m in ms)}
        if kinds & GENETIC or (not recs and any(x.startswith('gene:') for x in every)):
            order = next((r for r in recs if r['kind'] == 'test_order'), None)
            results = sorted((r for r in recs if r['kind'] == 'genetic_result'), key=lambda r: r['recorded_at'])
            gene = (results[0]['attributes'].get('gene') if results else None) or ((order.get('attributes') or {}).get('gene_requested') if order else None) \
                or next((x for x in texted if x.startswith('gene:')), '')[5:]
            return {**it, 'kind': 'genetic', 'concept': f'gene:{gene}', 'history': [self.day(r['recorded_at']) for r in results]}
        if kinds & MEDICATION or any(c.kind_of(x) == 'medication' for x in every):
            cid = c.most_specific([x for x in every if c.kind_of(x) == 'medication'])
            if not cid:
                return None
            active = next((r for r in recs if r['kind'] == 'medication_order' and r.get('status') == 'active'), None)
            disp = sorted((r for r in recs if r['kind'] == 'dispensing'), key=lambda r: r['occurred']['earliest'])
            start = self.span(active) if active else self.span(disp[0]) if disp else self.text_span(*ms[0]) if ms else None
            return {**it, 'kind': 'medication', 'concept': cid, 'start': start}
        if 'procedure' in kinds:
            kind = 'procedure'
        elif any(c.kind_of(x) == 'diagnosis' for x in every):
            kind = 'diagnosis'
        else:
            kind = 'phenotype'
        concept = c.most_specific([x for x in (coded if coded else texted) if c.kind_of(x) == kind])
        if not concept:
            return None
        text = next((x for x in texted if x != concept and c.is_a(x, concept)), None)
        spans = [self.span(r) for r in recs if r.get('occurred')] or [self.text_span(*x) for x in ms]
        best = sorted(spans, key=lambda s: (PRECISION[s['precision']], s['e']))[0] if spans else None
        return {**it, 'kind': kind, 'concept': concept, 'text_concept': text, 'best': best}

    # The page's period rules (restated from usable, eventTimes, pickOccurrence and windowFor).
    def event_times(self, items, free_text, eid):
        def admissible(i):
            return not (i['free_text_only'] and free_text == 'off') and not (i['possible_only'] and free_text != 'possible')

        def usable(i):
            if not i['concept'] or not admissible(i):
                return False
            if i['kind'] == 'genetic':
                return bool(i['history'])
            if i['kind'] == 'medication':
                return i['start'] is not None
            return i['best'] is not None
        items = [i for i in items if usable(i)]
        if eid == '@any_diagnosis':
            times = [i['best']['e'] for i in items if i['kind'] == 'diagnosis']
        elif eid == '@any_genetic':
            times = [i['history'][0] for i in items if i['kind'] == 'genetic']
        else:
            kind = self.c.kind_of(eid)
            if kind in ('medication', 'drug_class'):
                times = [i['start']['e'] for i in items if i['kind'] == 'medication'
                         and (i['concept'] == eid or eid in self.c.concepts.get(i['concept'], {}).get('classes', []))]
            elif kind == 'gene':
                times = [i['history'][0] for i in items if i['kind'] == 'genetic' and i['concept'] == eid]
            else:
                def concept_for(i):
                    return i['text_concept'] if free_text != 'off' and i.get('text_concept') else i['concept']
                times = [i['best']['e'] for i in items if i['kind'] in ('phenotype', 'diagnosis') and self.c.is_a(concept_for(i), eid)]
        return sorted(times)

    def event_day(self, items, free_text, eid, which, n):
        times = self.event_times(items, free_text, eid)
        n = max(1, int(n or 1))
        if which == 'first':
            pick = times[:1]
        elif which == 'last':
            pick = times[-1:]
        elif which == 'nth':
            pick = times[n - 1:n]
        elif which == 'nth_last':
            pick = [times[len(times) - n]] if len(times) >= n else []
        else:
            pick = times
        return pick[0] if pick else None

    def window(self, items, free_text, w):
        def resolve(b):
            if b['kind'] == 'birth':
                return 0
            if b['kind'] == 'age':
                return to_days(b['amount'], b['unit'])
            if b['kind'] == 'date':
                return self.day(b['date'])
            if b['kind'] == 'event':
                d = self.event_day(items, free_text, b['event'], b.get('which', 'first'), b.get('n'))
                return None if d is None else d + to_days(b.get('offset'), b.get('unit'))
            return 'span'
        s, e = resolve(w['start']), resolve(w['end'])
        if s is None or e is None:
            return {'reason': 'no anchor'}
        if s == 'span':
            s = e - to_days(w['start']['amount'], w['start']['unit'])
        if e == 'span':
            e = s + to_days(w['end']['amount'], w['end']['unit'])
        if e < 0:
            return {'reason': 'born after the window ends'}
        if e <= s:
            return {'reason': 'the window ends before it starts'}
        if e > self.last_contact:
            return {'reason': 'not followed to the end of the window'}
        return {'start': to_grid(max(0, s)), 'end': to_grid(e)}


def windows(data, rules, settings):
    cohort = Cohort(data)
    out = {}
    for p in data['patients']:
        pat = Patient(cohort, p)
        items = pat.items()
        for name, w in rules.items():
            for f in settings:
                out.setdefault(name, {}).setdefault(f, {})[p['patient_id']] = pat.window(items, f, w)
    return out


def compare(cohort_path, windows_path):
    with open(cohort_path) as f:
        data = strip(json.load(f))
    with open(windows_path) as f:
        page = json.load(f)
    settings = sorted({f for per in page['windows'].values() for f in per})
    ref = windows(data, page['rules'], settings)
    compared, placed, mismatches = 0, 0, []
    for name, per in page['windows'].items():
        for f, by in per.items():
            for pid, got in by.items():
                want = ref[name][f][pid]
                compared += 1
                placed += 'start' in want
                same = ('reason' in got) == ('reason' in want) and ('reason' in got or (got['start'], got['end']) == (want['start'], want['end']))
                if not same:
                    mismatches.append({'rule': name, 'free_text': f, 'patient_id': pid, 'page': got, 'reference': want})
    return {'compared': compared, 'placed': placed, 'mismatches': len(mismatches), 'first_mismatches': mismatches[:20]}


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != 'compare':
        sys.exit('usage: python demos/mockups/index_reference.py compare COHORT.json WINDOWS.json')
    summary = compare(sys.argv[2], sys.argv[3])
    print(json.dumps(summary))
    sys.exit(1 if summary['mismatches'] else 0)
