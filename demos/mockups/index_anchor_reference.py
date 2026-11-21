"""An independent reference of D52's index-anchor rewrite (`index_anchor_v1`) and of the server's narrowing
(`index_hull_v1`), for the reference differential (decision log, D52, rule 2) and the worlds harness (rule 1).

It shares no code with `trajectory/store_request.js` or `index_hull.py`:
- `rewrite(base, window, nodes, cohort_patients)` takes D49's request for a page definition (the translator's output
  with `indexAnchor: false`: the page's point windows, user relations from START and END, the page's scope) and the
  definition's window, and writes D52's request: hidden steps, relations, conditions, base windows and the `index` block;
  it mirrors the translator step by step (`indexSides`, `indexBaseWindow`, `indexRewrite`), with JavaScript's rounding
  (`Math.round`) and its float arithmetic in the same order;
- `point_reason(window, nodes, free_text, coded_mode, concepts)` gives the reason a shape keeps D49's point window (the
  translator's `indexPointReason`), or None;
- `anchor_range(events, concept, ancestors, occurrence, derived, exclude_kinds)` computes a hidden step's possible
  range over a converted patient's events with dictionaries and loops (no SQL): the order-statistic bound of
  `index_hull_v1`; `narrow(...)` narrows a base window with it;
- D52c: `simplify(query, conditions, index, windows, ranges, facts_of)` simplifies a narrowed request
  (`index_simplify_v1`: c1, c2, c3 and c3's weak patients), written apart from `index_simplify.py`, over facts
  (`simplify_facts`) read from the converted patients by loops.

As `index_reference.py` (D46), it mirrors the rule: it catches a divergence between two implementations, not a flaw in
the rule. The worlds harness (`tools/d52_measure.py`) checks the rule itself against a per-world window.
"""
from __future__ import annotations

from datetime import date
import json
import math

UNITS = {'second': 1 / 86400, 'minute': 1 / 1440, 'hour': 1 / 24, 'day': 1, 'week': 7, 'month': 30.44, 'year': 365.25}
GRID = 1440
RULE = 'index_anchor_v1'
HULL_RULE = 'index_hull_v1'
STEPS = {'start': '@index_start', 'end': '@index_end'}
CONDITIONS_1_1 = 'execution-store-conditions-1.1'


def js_round(x):
    """JavaScript's Math.round: halves round up."""
    return math.floor(x + 0.5)


def to_days(amount, unit):
    try:
        a = float(amount) if amount not in (None, '') else 0.0
    except (TypeError, ValueError):
        a = 0.0
    if a != a:  # NaN, as `+amount || 0` makes it 0
        a = 0.0
    return a * UNITS.get(unit, 1)


def to_grid(days):
    return js_round(days * GRID)


def day_of(patient, iso):
    """Days of age of a date (the page's `dayOf` for a date without a time)."""
    return (date.fromisoformat(iso[:10]) - date.fromisoformat(patient['birth_date'][:10])).days


def anchor_occurrence(bound):
    which, n = bound.get('which') or 'first', max(1, int(float(bound.get('n') or 1)))
    if which == 'first' or (which == 'nth' and n == 1):
        return 'first', None
    if which == 'last' or (which == 'nth_last' and n == 1):
        return 'last', None
    return 'any', {which: n}


def hidden_steps(window):
    """[{id, bound, side, occurrence, derived}], one per distinct event bound."""
    out = []
    s, e = window['start'], window['end']
    if s['kind'] == 'event':
        occ, der = anchor_occurrence(s)
        out.append({'id': STEPS['start'], 'bound': s, 'side': 'start', 'occurrence': occ, 'derived': der})
    if e['kind'] == 'event':
        occ, der = anchor_occurrence(e)
        same = s['kind'] == 'event' and s['event'] == e['event'] and anchor_occurrence(s) == (occ, der)
        if not same:
            out.append({'id': STEPS['end'], 'bound': e, 'side': 'end', 'occurrence': occ, 'derived': der})
    return out


def sides(window):
    """(start, end, steps): each side {'step', 'offset'} or {'constant': bound}."""
    steps = hidden_steps(window)
    def step_of(side):
        for st in steps:
            if st['side'] == side:
                return st['id']
        return steps[0]['id']
    s, e = window['start'], window['end']
    start = end = None
    if s['kind'] == 'event':
        start = {'step': step_of('start'), 'days': to_days(s.get('offset'), s.get('unit'))}
    elif s['kind'] != 'span':
        start = {'constant': s}
    if e['kind'] == 'event':
        end = {'step': step_of('end'), 'days': to_days(e.get('offset'), e.get('unit'))}
    elif e['kind'] != 'span':
        end = {'constant': e}
    if s['kind'] == 'span':
        start = {'step': end['step'], 'days': end['days'] - to_days(s.get('amount'), s.get('unit'))}
    if e['kind'] == 'span':
        end = {'step': start['step'], 'days': start['days'] + to_days(e.get('amount'), e.get('unit'))}
    for x in (start, end):
        if 'step' in x:
            x['offset'] = to_grid(x['days'])
    return start, end, steps


def constant_day(bound, patient):
    if bound['kind'] == 'birth':
        return 0
    if bound['kind'] == 'age':
        return to_days(bound.get('amount'), bound.get('unit'))
    return day_of(patient, bound['date'])


def base_window(start, end, patient):
    """The patient's base window in grid minutes, or None (the page's static checks)."""
    last = day_of(patient, patient['follow_up']['last_contact'])
    if 'constant' in end:
        c = constant_day(end['constant'], patient)
        if c < 0 or c > last:
            return None
        return {'start': 0, 'end': to_grid(c)}
    if 'step' in start and start['step'] == end['step'] and end['offset'] <= start['offset']:
        return None
    return {'start': to_grid(constant_day(start['constant'], patient)) if 'constant' in start else 0, 'end': to_grid(last)}


def point_reason(window, nodes, free_text, coded_mode, concepts):
    """Why a definition the base translation answers keeps D49's point window (D52 review, S2), or None when the rewrite
    writes it."""
    bounds = [b for b in (window['start'], window['end']) if b['kind'] == 'event']
    if not bounds:
        return None
    if any(b['event'] in ('@any_diagnosis', '@any_genetic') for b in bounds):
        return 'INDEX_ANCHOR_ANY_UNSUPPORTED'
    if any((concepts.get(b['event']) or {}).get('kind') == 'gene' for b in bounds):
        return 'INDEX_ANCHOR_GENE_UNSUPPORTED'
    steps = hidden_steps(window)
    if any(st['derived'] for st in steps) and free_text != 'off':
        return 'FREE_TEXT_COUNT_UNSUPPORTED'
    if any(n.get('occ') == 'count' and n.get('within') == 'period' for n in nodes):
        return 'INDEX_PERIOD_COUNT_UNSUPPORTED'
    s = window['start']
    if window['end']['kind'] == 'event' and (s['kind'] == 'date' or (s['kind'] == 'age' and to_days(s.get('amount'), s.get('unit')) < 0)):
        return 'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED'
    if free_text == 'off' and coded_mode not in ('derived', 'store') and any(not st['derived'] for st in steps):
        return 'FREE_TEXT_CODED_ONLY_UNSUPPORTED'
    return None


def _after(rid, a, b, lo=None, hi=None):
    r = {'id': rid, 'kind': 'after', 'from': a, 'to': b}
    if lo is not None:
        r['min'] = lo
    if hi is not None:
        r['max'] = hi
    return r


_DEFECT = None  # a planted defect of the rewrite (tools/d52_measure.py defects only); see DEFECTS
DEFECTS = ('offset_dropped', 'eligibility_dropped', 'end_not_strict', 'within_open', 'after_as_from_start', 'clamp_dropped')


def rewrite(base, window, nodes, patients):
    """D52's request from D49's (`base`: {query, conditions, windows}) for a definition with an event bound; `patients`
    maps each patient id in `base['windows']` to its cohort record."""
    start, end, steps = sides(window)
    windows = {pid: base_window(start, end, patients[pid]) for pid in base['windows']}
    return rewrite_sides(base, start, end, steps, nodes, windows)


def rewrite_sides(base, start, end, steps, nodes, windows):
    """The rewrite from resolved sides (`sides`) and each patient's base window (`base_window`, or the worlds harness's
    own)."""
    q = base['query']
    d = _DEFECT
    sv, ev = 'step' in start, 'step' in end
    relations, shift = [], {}
    for r in q['relations']:
        k = start if r['from'] == 'START' and sv else end if r['from'] == 'END' and ev else None
        if k is None:
            relations.append(r)
            continue
        shift[r['id']] = k['offset']
        out = dict(r, **{'from': k['step']})
        for key in ('min', 'max'):
            if key in r:
                out[key] = r[key] + k['offset']
        relations.append(out)
    scope = 'any'
    for n in nodes:
        x, s = n['id'], q.get('scope', 'any')
        if s in ('from_start', 'within'):
            if d != 'clamp_dropped':
                relations.append(_after(f'index:scope:{x}', 'START', x, 0))
            lo = (0 if d == 'offset_dropped' else start['offset']) if sv else None
            hi = end['offset'] if s == 'within' and ev and d != 'within_open' else None
            if sv and hi is not None and start['step'] == end['step'] and lo <= hi:
                relations.append(_after(f'index:scope-start:{x}', start['step'], x, lo, hi))
            else:
                if sv:
                    relations.append(_after(f'index:scope-start:{x}', start['step'], x, lo))
                if hi is not None:
                    relations.append(_after(f'index:scope-end:{x}', end['step'], x, None, hi))
            if s == 'within' and not ev and d != 'within_open':
                relations.append(_after(f'index:scope-end:{x}', x, 'END', 0))
        elif s == 'after':
            if ev:
                k, frm = (start['offset'], start['step']) if d == 'after_as_from_start' and sv else (end['offset'], end['step'])
                relations.append(_after(f'index:scope-end:{x}', frm, x, k))
            else:
                relations.append(_after(f'index:scope-end:{x}', 'END', x, 0))
    if ev and d != 'eligibility_dropped':
        relations.append(_after('index:followed', end['step'], 'END', end['offset']))
        if 'constant' in start:
            relations.append(_after('index:ordered', 'START', end['step'], 1 - end['offset']))
        else:
            relations.append(_after('index:born', 'START', end['step'], -end['offset']))
    if sv and ev and start['step'] != end['step']:
        relations.append(_after('index:ordered', start['step'], end['step'], start['offset'] - end['offset'] + 1))
    if sv and 'constant' in end:
        relations.append(_after('index:ordered', start['step'], 'END', start['offset'] + (0 if d == 'end_not_strict' else 1)))
    relaxations = [dict(x, max=x['max'] + shift[x['relation']]) if x['kind'] == 'extend_max' and x['relation'] in shift
                   else x for x in q['relaxations']]
    query = dict(q, scope=scope, relations=relations, relaxations=relaxations,
                 steps=q['steps'] + [{'id': st['id'], 'concept': st['bound']['event'], 'occurrence': st['occurrence']}
                                     for st in steps])
    conditions = base.get('conditions')
    derived = [st for st in steps if st['derived']]
    if derived:
        conditions = dict(conditions, steps=dict(conditions['steps'])) if conditions else {'profile': CONDITIONS_1_1, 'steps': {}}
        for st in derived:
            conditions['steps'][st['id']] = {'occurrence': st['derived']}
    side = lambda x: {'step': x['step'], 'offset': x['offset']} if 'step' in x else None
    return {'query': query, 'conditions': conditions, 'windows': windows,
            'index': {'rule': RULE, 'start': side(start), 'end': side(end)}}


# The narrowing (`index_hull_v1`), without SQL.

def closure(ancestors, concept):
    """`concept` and its ancestors."""
    out, todo = set(), [concept]
    while todo:
        c = todo.pop()
        if c not in out:
            out.add(c)
            todo.extend(ancestors.get(c, ()))
    return out


def anchor_range(events, concept, ancestors, occurrence, derived=None, exclude_kinds=()):
    """(lo, hi, count) of a hidden step's possible time over one converted patient's events (`synthetic_ingest`'s
    output: {event_id, kind, concepts, start}), or (None, None, count) when there are too few: the order-statistic bound
    over the candidates' recorded start ranges."""
    # A derived occurrence ranks coded records only (D32): never a letter mention (D52 review, M1).
    skip = set(exclude_kinds) | ({'mention'} if derived else set())
    family = [e for e in events if e['kind'] not in skip
              and any(concept in closure(ancestors, c) for c in e['concepts'])]
    los = sorted(e['start'][0] for e in family)
    his = sorted(e['start'][1] for e in family)
    if occurrence == 'first':
        k, last = 1, False
    elif occurrence == 'last':
        k, last = 1, True
    else:
        (which, k), = derived.items()
        last = which == 'nth_last'
    if len(family) < k:
        return None, None, len(family)
    if last:
        return los[-k], his[-k], len(family)
    return los[k - 1], his[k - 1], len(family)


def narrow(base, index, ranges):
    """(window, reason): a base window (any unit) narrowed by the hidden steps' ranges ({step: (lo, hi, count)}, in the
    same unit, offsets alike); None with 'INDEX_ANCHOR_NOT_RECORDED' or 'INDEX_NO_POSSIBLE_PERIOD'."""
    if base is None:
        return None, None
    for side in ('start', 'end'):
        x = index.get(side)
        if x and ranges[x['step']][0] is None:
            return None, 'INDEX_ANCHOR_NOT_RECORDED'
    s, e = base['start'], base['end']
    if index.get('start'):
        s = max(s, ranges[index['start']['step']][0] + index['start']['offset'])
    if index.get('end'):
        e = min(e, ranges[index['end']['step']][1] + index['end']['offset'])
    if s > e:
        return None, 'INDEX_NO_POSSIBLE_PERIOD'
    return {'start': s, 'end': e}, None


# D52c: the simplification (`index_simplify_v1`), without SQL, written apart from `index_simplify.py`.

SIMPLIFY_RULE = 'index_simplify_v1'


def descendants(ancestors, concept):
    """Every concept whose closure holds `concept` (itself included)."""
    return {c for c in set(ancestors) | {concept} if concept in closure(ancestors, c)}


def simplify_facts(patients, ancestors, concept):
    """Over converted patients ({patient_id, events, excluded?}): the facts the simplification reads, by loops."""
    below = descendants(ancestors, concept)
    meets = set()
    for d in below:
        meets |= closure(ancestors, d)
    excluded, linked, multi = {}, set(), set()
    for p in patients:
        los = [x['start'][0] for x in p.get('excluded', []) if concept in closure(ancestors, x['concept'])]
        if los:
            excluded[p['patient_id']] = min(los)
        names = []
        for e in p['events']:
            names.append(e.get('start_var', e['event_id'] + '.s'))
            if 'end' in e:
                names.append(e.get('end_var', e['event_id'] + '.f'))
        if len(names) != len(set(names)):
            linked.add(p['patient_id'])
        if any(len(set(e['concepts'])) > 1 for e in p['events']):
            multi.add(p['patient_id'])
    return {'below': below, 'meets': meets, 'excluded': excluded, 'linked': linked, 'multi': multi}


def simplify(query, conditions, index, windows, ranges, facts_of):
    """(query, record, weak): the request simplified (c1, c2, c3; decision log, D52c). `windows` are the narrowed
    windows, `ranges` {step: {patient: (lo, hi, count)}} (anchor_range's), and `facts_of(concept)` gives
    `simplify_facts` for a hidden step's concept. Any unit, as long as windows, ranges and bounds share it."""
    q = json.loads(json.dumps(query))
    cond = (conditions or {}).get('steps') or {}
    relax = list(q.get('relaxations', [])) + list((conditions or {}).get('relaxations', []))
    hidden = []
    for side in ('start', 'end'):
        if index.get(side) and index[side]['step'] not in hidden:
            hidden.append(index[side]['step'])
    kinds = {s['id']: s for s in q['steps']}
    users = [s['id'] for s in q['steps'] if s['id'] not in hidden]
    names_relation = lambda rid: any(x.get('relation') == rid for x in relax)
    names_step = lambda sid: any(x.get('step') == sid for x in relax)
    widened = lambda sid: [kinds[sid]['concept']] + [x['concept'] for x in relax if x.get('step') == sid and 'concept' in x]
    record = {'rule': SIMPLIFY_RULE, 'dropped': [], 'merged': {}, 'existential': None, 'implied': []}
    for h in hidden:  # c1
        if kinds[h].get('occurrence') != 'first' or h in cond:
            continue
        below = facts_of(kinds[h]['concept'])['below']
        kept = []
        for r in q['relations']:
            x = r['to']
            drop = (r['kind'] == 'after' and r['from'] == h and x in users and r.get('max') is None
                    and (r.get('min') is None or r['min'] <= 0) and not names_relation(r['id']))
            if drop:
                c = cond.get(x, {})
                drop = all(k in ('unit', 'eq', 'gt', 'ge', 'lt', 'le', 'count') for k in c) \
                    and all(w in below for w in widened(x))
            if drop:
                record['dropped'].append(r['id'])
            else:
                kept.append(r)
        q['relations'] = kept
    for h in hidden:  # c2
        occ = kinds[h].get('occurrence')
        if occ not in ('first', 'last') or h in cond:
            continue
        match = [u for u in users if kinds[u]['concept'] == kinds[h]['concept'] and kinds[u].get('occurrence') == occ
                 and u not in cond and not names_step(u)]
        if not match:
            continue
        x = match[0]
        new, fine = [], True
        for r in q['relations']:
            a = x if r['from'] == h else r['from']
            b = x if r['to'] == h else r['to']
            if a == b:
                lo, hi = r.get('min'), r.get('max')
                if r['kind'] != 'after' or names_relation(r['id']) or (lo is not None and lo > 0) or (hi is not None and hi < 0):
                    fine = False
                continue
            new.append(dict(r, **{'from': a, 'to': b}))
        if fine:
            q['relations'] = new
            q['steps'] = [s for s in q['steps'] if s['id'] != h]
            record['merged'][h] = x
    weak = set()
    left = [h for h in hidden if h not in record['merged']]
    if len(hidden) == 1 and len(left) == 1 and kinds[left[0]].get('occurrence') == 'first' and left[0] not in cond \
            and q.get('scope', 'any') == 'any':
        h = left[0]
        f = facts_of(kinds[h]['concept'])
        checked, ys, fine = [], set(), True
        for r in q['relations']:
            if r['from'] != h and r['to'] != h:
                continue
            if r['kind'] != 'after' or names_relation(r['id']) or r.get('max') is not None:
                fine = False
            elif r['from'] == h and (r['to'] in users or r['to'] == 'END'):
                if r['to'] != 'END':
                    ys.add(r['to'])
            elif r['to'] == h and r['from'] in ('START', 'END') and r.get('min') is not None:
                checked.append(r)
            else:
                fine = False
        if fine:
            for pid, w in windows.items():
                if w is None:
                    continue
                lo = ranges[h].get(pid, (None, None, 0))[0]
                if lo is None:
                    fine = False
                    break
                if pid in f['excluded']:
                    lo = min(lo, f['excluded'][pid])
                for r in checked:
                    if lo < (w['start'] if r['from'] == 'START' else w['end']) + r['min']:
                        fine = False
                if not fine:
                    break
        if fine:
            q['relations'] = [r for r in q['relations'] if r not in checked]
            for s in q['steps']:
                if s['id'] == h:
                    s['occurrence'] = 'any'
            record['existential'] = h
            record['implied'] = [r['id'] for r in checked]
            windowed = {pid for pid, w in windows.items() if w is not None}
            if any(c in f['meets'] for y in ys for c in widened(y)):
                weak = windowed
            else:
                weak = windowed & (f['linked'] | f['multi'])
    return q, record, weak
