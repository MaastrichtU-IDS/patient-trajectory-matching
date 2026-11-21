"""The server's simplification of an index-anchored request after its narrowing (`index_simplify_v1`; decision log, D52c).

    python demos/mockups/index_simplify.py simplify SNAPSHOT < narrowed.json   # each indexed case simplified
    python demos/mockups/index_simplify.py compare SNAPSHOT < simplified.json  # both forms matched, per patient (rule 3b)

D52 writes an event-placed index period as a hidden step (`@index_start`, `@index_end`; `trajectory/store_request.js`,
`index_anchor_v1`), and the server narrows each base window to the hull of the periods the store's records allow
(`index_hull.py`). D52b's measurement and D52c's design study found the added cost in the engine: a `first` hidden step
whose target is uncertain and that a user step can bind goes to D31's envelope search, and its relation to a related
user step can close a cycle that takes the search off the forest path. Three rewrites, each giving a query with the same
truth value as the request in every world (the decision log states each argument; the worlds harness,
`tools/d52_measure.py`, checks them), run in this order after the narrowing:

- **c1, implied order.** A relation `H -> X` (kind `after`, a minimum m <= 0 or none, no maximum, no relaxation naming
  it) from a hidden `first` step H (not derived) to a user step X every event of which carries H's concept (X's concept,
  and every concept a relaxation widens X to, is H's concept or below it in the store's closure; X has no occurrence or
  relative condition) is dropped. In every world the first K starts at or before every K event, and X binds a K event
  that H's candidates include (the request leaves the same kinds out of both), so `X - H >= 0 >= m` holds.
- **c2, merge.** A user step X with H's concept and occurrence (`first` or `last`), with no condition and no relaxation
  naming it, stands for H: H is removed, every relation is read from X, and a relation that becomes X -> X is dropped
  when it holds for any one time (kind `after`, minimum <= 0 <= maximum, no relaxation naming it); otherwise nothing is
  merged. In every world both steps bind the one true first (last) K event, by the same rule, so every relation has the
  same truth value. The answer is mapped back: H's binding and witness are X's, and a step list naming X names H.
- **c3, existential anchor.** With one hidden step H in the request, `first`, not derived and not merged, the query's
  scope `any`, and every relation on H either `H -> Y` (Y a user step or END; kind `after`, no maximum) or `A -> H` from
  an anchor (START or END; kind `after`, a minimum, no maximum), none named by a relaxation: each `A -> H` must hold for
  every K record of every windowed patient (the least recorded start of H's candidates and of the store's excluded
  records of H's concept, against the narrowed window), and is then dropped; H becomes an `any` step of its concept. In
  every world some K event satisfies every remaining bound on H, all from above, exactly when the first one does. The
  answer is mapped back: H's binding and witness are dropped (the step binds some K event, not the first; the page shows
  the hull). A `possible` certainty claims that no binding is exact; on the `any` form that is a claim about one K event
  for every world, which can fail where the first K is exact through different events in different worlds. That needs
  H's candidates' times to depend on a step H is bounded by: shared time variables (`linked`), an event with two direct
  concepts (`multi`), or a bounding step whose concepts meet H's family (`meets`). For those patients (`weak`) each
  `possible` claim of the answer carries ANCHOR_ORDER_MAY_MAKE_EXACT (a relaxed result's is also made incomplete),
  withdrawing the claim; elsewhere the claim stands (decision log, D52c).

Nothing here changes a store or engine file; no build loads this module. Synthetic data only.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

RULE = 'index_simplify_v1'
NOTE = 'ANCHOR_ORDER_MAY_MAKE_EXACT'
ANCHORS = ('START', 'END')
VALUE_KEYS = {'unit', 'eq', 'gt', 'ge', 'lt', 'le'}  # execution_store._VALUE_KEYS: a value condition keeps real events
STEP_LISTS = ('excluded_steps', 'uncovered_steps', 'unrecorded_steps', 'uncertain_steps')
BESIDE = ('relaxed_match', 'unverified_match')
CALLS = {'simplify': 0, 'facts_queries': 0, 'facts_hits': 0}
# Planted defects (tools/d52_measure.py defects only): each must make the worlds harness fail a case.
DEFECTS = ('drop_positive_min', 'drop_wider_concept', 'merge_positive_offset', 'exist_with_lower_bound',
           'exist_born_unchecked', 'exist_excluded_unchecked', 'exist_claims_unguarded')
_DEFECT = None
# Per snapshot: each validated concept's descendants (`below`) and the concepts whose families meet its own (`meets`),
# its excluded records' least start per patient (`excluded`), and the patients with shared variables or an event with
# two direct concepts (`linked`, `multi`). Keys are concepts the narrowing has validated (index_hull.validate), so the
# cache holds at most one entry per concept of the snapshot.
_FACTS = {'snapshot': None, 'concepts': {}, 'linked': None}


def _facts(store, concept=None):
    """(below, meets, excluded) for a hidden step's concept, and (linked, multi), read once per snapshot."""
    if _FACTS['snapshot'] != store.snapshot_id:
        _FACTS.update(snapshot=store.snapshot_id, concepts={}, linked=None)
    if _FACTS['linked'] is None:
        CALLS['facts_queries'] += 1
        linked = frozenset(p for p, in store.query('SELECT patient_id FROM patients WHERE shared_variables'))
        multi = frozenset(p for p, in store.query(
            '''SELECT DISTINCT p.patient_id FROM (SELECT patient_idx FROM event_concepts GROUP BY patient_idx, event_id
                                                  HAVING count(*) > 1) m JOIN patients p USING (patient_idx)'''))
        _FACTS['linked'] = (linked, multi)
    if concept is None:
        return None, _FACTS['linked']
    if concept not in _FACTS['concepts']:
        CALLS['facts_queries'] += 1
        below = frozenset(c for c, in store.query('SELECT concept FROM concept_closure WHERE ancestor = ?', [concept]))
        meets = frozenset(a for a, in store.query(
            '''SELECT DISTINCT b.ancestor FROM concept_closure a JOIN concept_closure b ON b.concept = a.concept
               WHERE a.ancestor = ?''', [concept]))
        excluded = dict(store.query(
            '''SELECT p.patient_id, min(x.start_lo) FROM excluded_records x JOIN concept_closure cc ON cc.concept = x.concept
               JOIN patients p USING (patient_idx) WHERE cc.ancestor = ? GROUP BY p.patient_id''', [concept]))
        _FACTS['concepts'][concept] = (below, meets, excluded)
    else:
        CALLS['facts_hits'] += 1
    return _FACTS['concepts'][concept], _FACTS['linked']


def prefill(store, concepts):
    """Read the facts for each concept the snapshot holds (the server's start; D52c's pre-registered pre-fill)."""
    known = {c for c, in store.query('SELECT DISTINCT concept FROM concept_closure')}
    _facts(store)
    for c in concepts:
        if c in known:
            _facts(store, c)


def clear_cache():
    _FACTS.update(snapshot=None, concepts={}, linked=None)


def _hidden(index):
    return [x['step'] for x in (index.get('start'), index.get('end')) if x] if index else []


def _concepts_of(query, conditions, sid):
    """A step's concept and every concept a relaxation widens it to (query and conditions relaxations)."""
    out = [s['concept'] for s in query['steps'] if s['id'] == sid]
    out += [x['concept'] for x in query.get('relaxations', []) if x.get('kind') == 'widen_concept' and x.get('step') == sid]
    out += [x['concept'] for x in (conditions or {}).get('relaxations', []) if x.get('step') == sid and 'concept' in x]
    return out


def _named(query, conditions, sid=None, rid=None):
    """Whether a relaxation names a relation `rid`, or a step `sid`."""
    for x in [*query.get('relaxations', []), *((conditions or {}).get('relaxations', []))]:
        if rid is not None and x.get('relation') == rid:
            return True
        if sid is not None and x.get('step') == sid:
            return True
    return False


def simplify(store, query, conditions, index, windows, found):
    """{query, conditions, record, weak}: the request (on the store's clock, as matched) simplified after its narrowing.

    `windows` are the narrowed windows ({patient id: {start, end} or None}) and `found` the narrowing's ranges
    (`index_hull.ranges`: {step: {patient id: (count, lo, hi)}}). `record` names what was done (`dropped`, c1; `merged`,
    c2; `existential` and `implied`, c3); `weak` the patients whose `possible` claims `apply` withdraws under c3. The
    request given is not changed."""
    CALLS['simplify'] += 1
    q = copy.deepcopy(query)
    steps = {s['id']: s for s in q['steps']}
    cond_steps = (conditions or {}).get('steps') or {}
    hidden = [h for h in dict.fromkeys(_hidden(index)) if h in steps]
    users = [s['id'] for s in q['steps'] if s['id'] not in hidden]
    record = {'rule': RULE, 'dropped': [], 'merged': {}, 'existential': None, 'implied': []}
    plain = lambda sid: sid not in cond_steps  # no condition: not derived, no value tag
    # c1: implied order.
    for h in hidden:
        if steps[h].get('occurrence') != 'first' or not plain(h):
            continue
        (below, _, _), _ = _facts(store, steps[h]['concept'])
        keep = []
        for r in q['relations']:
            x = r.get('to')
            ok = (r.get('kind') == 'after' and r.get('from') == h and x in users and 'max' not in r
                  and (r.get('min') is None or r['min'] <= 0 or _DEFECT == 'drop_positive_min')
                  and not _named(q, conditions, rid=r['id'])
                  and not ({'occurrence', 'relative'} & set(cond_steps.get(x, {})))
                  and set(cond_steps.get(x, {})) <= VALUE_KEYS | {'count'}
                  and (all(c in below for c in _concepts_of(q, conditions, x))
                       or (_DEFECT == 'drop_wider_concept' and steps[x]['concept'] in below)))
            if ok:
                record['dropped'].append(r['id'])
            else:
                keep.append(r)
        q['relations'] = keep
    # c2: merge a hidden step into an identical user step.
    for h in hidden:
        hs = steps[h]
        if hs.get('occurrence') not in ('first', 'last') or not plain(h):
            continue
        x = next((u for u in users if steps[u]['concept'] == hs['concept'] and steps[u].get('occurrence') == hs['occurrence']
                  and plain(u) and not _named(q, conditions, sid=u)), None)
        if x is None:
            continue
        renamed, ok = [], True
        for r in q['relations']:
            r2 = {**r, 'from': x if r['from'] == h else r['from'], 'to': x if r['to'] == h else r['to']}
            if r2['from'] == r2['to']:
                holds = (r.get('min') is None or r['min'] <= 0) and (r.get('max') is None or r['max'] >= 0)
                if r.get('kind') != 'after' or _named(q, conditions, rid=r['id']) or (
                        not holds and _DEFECT != 'merge_positive_offset'):
                    ok = False
                    break
                continue
            renamed.append(r2)
        if not ok:
            continue
        q['relations'] = renamed
        q['steps'] = [s for s in q['steps'] if s['id'] != h]
        record['merged'][h] = x
    # c3: the existential anchor.
    weak = frozenset()
    rest = [h for h in hidden if h not in record['merged']]
    if len(hidden) == 1 and rest and steps[rest[0]].get('occurrence') == 'first' and plain(rest[0]) \
            and q.get('scope', 'any') == 'any':
        h = rest[0]
        (below, meets, excluded), (linked, multi) = _facts(store, steps[h]['concept'])
        upper, implied, bounded, ok = [], [], set(), True
        for r in q['relations']:
            if h not in (r.get('from'), r.get('to')):
                continue
            if r.get('kind') != 'after' or _named(q, conditions, rid=r['id']):
                ok = False
            elif r['from'] == h and (r['to'] in users or r['to'] == 'END') and 'max' not in r:
                upper.append(r)
                if r['to'] in users:
                    bounded.add(r['to'])
            elif r['to'] == h and r['from'] in ANCHORS and 'max' not in r and r.get('min') is not None:
                implied.append(r)
            elif _DEFECT == 'exist_with_lower_bound':
                upper.append(r)
            else:
                ok = False
        if ok and _DEFECT != 'exist_born_unchecked':
            ranges = found.get(h, {})
            for pid, w in windows.items():
                if w is None:
                    continue
                lo = ranges.get(pid, (0, None, None))[1]
                if lo is None:
                    ok = False
                    break
                if _DEFECT != 'exist_excluded_unchecked':
                    lo = min(lo, excluded.get(pid, lo))
                if any(lo < w['start' if r['from'] == 'START' else 'end'] + r['min'] for r in implied):
                    ok = False
                    break
        if ok:
            gone = {r['id'] for r in implied}
            q['relations'] = [r for r in q['relations'] if r['id'] not in gone]
            for s in q['steps']:
                if s['id'] == h:
                    s['occurrence'] = 'any'
            record['existential'] = h
            record['implied'] = [r['id'] for r in implied]
            meet = any(c in meets for y in bounded for c in _concepts_of(q, conditions, y))
            windowed = {pid for pid, w in windows.items() if w is not None}
            weak = frozenset() if _DEFECT == 'exist_claims_unguarded' else \
                frozenset(windowed if meet else windowed & (linked | multi))
    return {'query': q, 'conditions': conditions, 'record': record, 'weak': weak}


def applied(record):
    return bool(record['dropped'] or record['merged'] or record['existential'])


def _with_note(m, note):
    notes = [n for n in (m.get('note') or '').split('; ') if n]
    return '; '.join(notes if note in notes else notes + [note])


def map_result(r, record, order, weak):
    """One store result (event ids and store times) as the request without the simplification would read it: under c2 the
    hidden step's binding and witness are its user step's, and a step list naming the user step names it too; under c3
    the hidden step's binding and witness are dropped, and for a `weak` patient every `possible` claim is withdrawn."""
    out = copy.deepcopy(r)
    rank = {s: i for i, s in enumerate(order)}
    merged, h3 = record['merged'], record['existential']
    for m in (out, *(out[k] for k in BESIDE if k in out)):
        for key in ('binding', 'witness'):
            if key not in m:
                continue
            d = dict(m[key])
            for h, x in merged.items():
                if x in d:
                    d[h] = d[x]
            if h3 is not None:
                d.pop(h3, None)
            m[key] = {s: d[s] for s in sorted(d, key=lambda s: rank.get(s, len(rank)))}
    for key in STEP_LISTS:
        if key in out and merged:
            names = list(out[key]) + [h for h, x in merged.items() if x in out[key] and h not in out[key]]
            out[key] = sorted(names, key=lambda s: rank.get(s, len(rank)))
    if h3 is not None and out.get('patient_id') in weak:
        if out['status'] == 'possible':
            out['note'] = _with_note(out, NOTE)
        if out['status'] == 'relaxed' and out.get('certainty') == 'possible':
            out['note'] = _with_note(out, NOTE)
            out['complete'] = False
        for k in BESIDE:
            if k in out and out[k].get('certainty') == 'possible':
                out[k]['note'] = _with_note(out[k], NOTE)
    return out


def apply(answer, simplified, query):
    """The store's answer to the simplified request with each result mapped back (`map_result`), given the request as
    sent (`query`, for its step order). Nothing else in the answer changes."""
    record = simplified['record']
    if not applied(record):
        return answer
    order = [s['id'] for s in query['steps']]
    return {**answer, 'results': [map_result(r, record, order, simplified['weak']) for r in answer['results']]}


def describe(simplified):
    """What the server reports: the record, and how many patients' possible claims are withdrawn."""
    return {**simplified['record'], 'weak_patients': len(simplified['weak'])}


# The identity check (rule 3b): the answer with the simplification against the answer without it, per patient.

IGNORED = ('binding', 'witness', 'method', 'search_steps')


def claim_key(r):
    """A result's claims: every field but the binding, the witness and the engine's own path and effort, recursively in the
    matches beside it."""
    out = {k: v for k, v in r.items() if k not in IGNORED}
    for k in BESIDE:
        if k in out:
            out[k] = {kk: vv for kk, vv in out[k].items() if kk not in IGNORED}
    return json.dumps(out, sort_keys=True)


def _notes(r):
    out = set((r.get('note') or '').split('; '))
    for k in BESIDE:
        out |= set((r.get(k, {}).get('note') or '').split('; '))
    return out


def classify(u, s, record, weak, excluded_of):
    """The classes that explain a changed result (`u` without the simplification, `s` with it, mapped back), each verified
    on its facts; an empty list leaves it unexplained.
    - `decided`: c1 or c3 applied, and the result without it was undecided where the one with it decides: `unresolved`
      (it claims nothing itself), or a `none` or `relaxed` whose relaxation search did not complete (`complete: false`,
      claiming only that the unrelaxed query is impossible), against a `none` or `relaxed` (a `relaxed` for a `relaxed`);
    - `undecided_both`: both `unresolved`, so only the matches beside them (each sound by the engine's rules) differ;
    - `admission`: c3 applied, the store holds excluded records of the anchor's concept for the patient, and a side names
      ADMISSION_EXCLUDED_RECORDS or excluded steps (an `any` step's excluded-records rule against a `first` step's, D18);
    - `claim_withdrawn`: c3 applied, the patient is weak, and the result with it carries ANCHOR_ORDER_MAY_MAKE_EXACT."""
    out = []
    order_or_exist = bool(record['dropped'] or record['existential'])
    open_search = u.get('complete') is False and s.get('complete') is not False and (
        (u['status'] == 'none' and s['status'] in ('none', 'relaxed')) or (u['status'] == 'relaxed' and s['status'] == 'relaxed'))
    if order_or_exist and ((u['status'] == 'unresolved' and s['status'] != 'unresolved') or open_search):
        out.append('decided')
    if u['status'] == 'unresolved' and s['status'] == 'unresolved' and order_or_exist:
        out.append('undecided_both')
    pid = u['patient_id']
    if record['existential'] and pid in excluded_of and any(
            r.get('reason') == 'ADMISSION_EXCLUDED_RECORDS' or 'excluded_steps' in r for r in (u, s)):
        out.append('admission')
    if record['existential'] and pid in weak and NOTE in _notes(s):
        out.append('claim_withdrawn')
    return out


def compare(store, query, conditions, windows, simplified, answer_u, answer_s):
    """Per patient: (changed, by class, each changed patient's classes, unexplained) between the answer without the simplification (`answer_u`) and the
    answer with it mapped back (`answer_s`), and how many bindings differ (reported only)."""
    record, weak = simplified['record'], simplified['weak']
    h = record['existential']
    excluded = {}
    if h is not None:
        concept = next(s['concept'] for s in query['steps'] if s['id'] == h)
        (_, _, excluded), _ = _facts(store, concept)
    by_s = {r['patient_id']: r for r in answer_s['results']}
    out = {'results': 0, 'changed': 0, 'by_class': {}, 'classes': {}, 'unexplained': [], 'bindings_differ': 0}
    for u in answer_u['results']:
        s = by_s[u['patient_id']]
        out['results'] += 1
        if json.dumps(u.get('binding'), sort_keys=True) != json.dumps(s.get('binding'), sort_keys=True):
            out['bindings_differ'] += 1
        if claim_key(u) == claim_key(s):
            continue
        out['changed'] += 1
        classes = classify(u, s, record, weak, excluded)
        key = ' + '.join(classes) or 'none'
        out['by_class'][key] = out['by_class'].get(key, 0) + 1
        out['classes'][u['patient_id']] = classes
        if not classes:
            out['unexplained'].append({'patient_id': u['patient_id'], 'without': json.loads(claim_key(u)), 'with': json.loads(claim_key(s))})
    return out


# The command line: the tools' path (the census, the usefulness rule, the parity test), on cases in minutes from birth.

def _micros_windows(case, births, minute):
    return {pid: None if w is None else {'start': births[pid] + w['start'] * minute, 'end': births[pid] + w['end'] * minute}
            for pid, w in case['windows'].items()}


def simplify_cases(snapshot, cases, cohort):
    """Each case narrowed by `index_hull.py narrow` (it carries `index_result`) with its query and conditions simplified;
    it gains `unsimplified` ({query, conditions}) and `simplify_result` (the record, the weak patients, the step order).
    The simplified query's bounds are in minutes, as the case's (the rewrite only drops relations, renames steps and
    changes an occurrence)."""
    import index_hull
    import store_parity
    from patterns import execution_store as es
    births = store_parity.birth_micros(cohort)
    minute = index_hull.MINUTE
    out = []
    with es.open_store(snapshot) as store:
        for case in cases:
            if not case.get('index_result'):
                out.append(case)
                continue
            query = store_parity.to_micros(case['query'])
            found = index_hull.ranges(store, query, case.get('conditions'), case['index'])
            s = simplify(store, query, case.get('conditions'), case['index'], _micros_windows(case, births, minute), found)
            q = copy.deepcopy(case['query'])
            ids = {r['id'] for r in s['query']['relations']}
            renamed = {r['id']: r for r in s['query']['relations']}
            q['relations'] = [{**r, 'from': renamed[r['id']]['from'], 'to': renamed[r['id']]['to']}
                              for r in q['relations'] if r['id'] in ids]
            occ = {st['id']: st.get('occurrence') for st in s['query']['steps']}
            q['steps'] = [{**st, **({'occurrence': occ[st['id']]} if 'occurrence' in st or occ[st['id']] else {})}
                          for st in q['steps'] if st['id'] in occ]
            assert store_parity.to_micros(q) == s['query'], 'the simplified query in minutes is the one matched'
            out.append({**case, 'query': q, 'conditions': s['conditions'],
                        'unsimplified': {'query': case['query'], 'conditions': case.get('conditions')},
                        'simplify_result': {**s['record'], 'weak': sorted(s['weak']),
                                            'order': [st['id'] for st in case['query']['steps']]}})
    return out


def compare_cases(snapshot, cases, cohort):
    """For each simplified case: both forms matched by `store_conditions.match`, the simplified answer mapped back, and the
    per-patient comparison (`compare`); also each form's results as `store_parity.py match` keeps them."""
    import index_hull
    import store_parity
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    births = store_parity.birth_micros(cohort)
    minute = index_hull.MINUTE
    keep = lambda r: {**{k: r[k] for k in ('status', 'certainty', 'relaxations', 'reason') if k in r},
                      **({'relaxations': r['relaxed_match']['relaxations']} if r['status'] == 'unresolved' and 'relaxed_match' in r else {})}
    out = {}
    with es.open_store(snapshot) as store:
        for case in cases:
            if 'simplify_result' not in case:
                continue
            windows = _micros_windows(case, births, minute)
            u_q, u_c = store_parity.to_micros(case['unsimplified']['query']), case['unsimplified']['conditions']
            sr = case['simplify_result']
            s = {'query': store_parity.to_micros(case['query']), 'conditions': case['conditions'],
                 'record': {k: sr[k] for k in ('rule', 'dropped', 'merged', 'existential', 'implied')}, 'weak': frozenset(sr['weak'])}
            try:
                a_u = sc.match(store, u_q, windows, conditions=store_parity.conditions_micros(u_c))
                a_s = apply(sc.match(store, s['query'], windows, conditions=store_parity.conditions_micros(s['conditions'])), s, u_q)
            except ValueError as error:
                out[case['name']] = {'error': getattr(error, 'code', None) or str(error).split(':')[0]}
                continue
            res = compare(store, u_q, u_c, windows, s, a_u, a_s)
            res['record'] = s['record']
            res['without'] = {r['patient_id']: keep(r) for r in a_u['results']}
            res['with'] = {r['patient_id']: keep(r) for r in a_s['results']}
            out[case['name']] = res
    return out


def main(argv):
    if argv[:1] in (['simplify'], ['compare']) and len(argv) == 2:
        data = json.load(sys.stdin)
        cohort = json.loads(Path(data['cohort']).read_text())
        if argv[0] == 'simplify':
            print(json.dumps({**data, 'cases': simplify_cases(Path(argv[1]), data['cases'], cohort)}))
        else:
            print(json.dumps(compare_cases(Path(argv[1]), data['cases'], cohort)))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
