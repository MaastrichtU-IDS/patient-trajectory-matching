"""Executable contract examples, not a general matcher or OWL reasoner."""
from pathlib import Path
from decimal import Decimal
import copy
import json

ROOT = Path(__file__).resolve().parent
US = 1000000


def decimal_text(value):
    if value is None:
        return None
    return format(value.normalize(), 'f')


def entails(observed, requested, taxonomy):
    pending, seen = [observed], set()
    while pending:
        concept = pending.pop()
        if concept == requested:
            return True
        if concept not in seen:
            seen.add(concept)
            pending.extend(taxonomy.get(concept, []))
    return False


def validate_pattern(pattern, reference):
    """The oracle supports this exemplar, with budgets varied for checks."""
    left, right = copy.deepcopy(pattern), copy.deepcopy(reference)
    left.pop('budget'); right.pop('budget')
    if left != right:
        raise ValueError('UNSUPPORTED_FEATURE: oracle supports the supplied exemplar only')
    budget = pattern['budget']
    if not 0 <= budget['max_relaxed_constraints'] <= 2:
        raise ValueError('INVALID_PATTERN: relaxation count')
    if Decimal(budget['max_total_cost']) < 0:
        raise ValueError('INVALID_PATTERN: negative cost budget')


def validate_events(events):
    ids = set()
    for e in events:
        if e['event_id'] in ids:
            raise ValueError('Duplicate event ID')
        ids.add(e['event_id'])
        t = e['time']
        if t['kind'] != 'point':
            raise ValueError('UNSUPPORTED_FEATURE: proper interval')
        if t['start_min_us'] != t['end_min_us'] or t['start_max_us'] != t['end_max_us']:
            raise ValueError('Point endpoints must be the same variable')
        lo, hi = t['start_min_us'], t['start_max_us']
        if lo is not None and hi is not None and lo > hi:
            raise ValueError('Empty feasible time set')
        if e['event_kind'] == 'measurement' and lo != hi:
            raise ValueError('UNSUPPORTED_FEATURE: uncertain measurement time')


def reachable_extension(pattern, budget):
    """How far past the exposure window this budget can actually reach, in microseconds.

    The temporal relaxation is priced continuously: overrunning by the whole
    `extra_seconds` costs `maximum_cost`, and a shorter overrun costs in
    proportion. A budget therefore buys a specific depth rather than the whole
    extension, and that is the depth the source search has to have covered.
    """
    extension = next(r for r in pattern['relaxations'] if r['op'] == 'extend_max_gap')
    if budget['max_relaxed_constraints'] < 1 or Decimal(budget['max_total_cost']) <= 0:
        return 0
    share = min(Decimal(1), Decimal(budget['max_total_cost']) / Decimal(extension['maximum_cost']))
    return int(Decimal(extension['extra_seconds']) * US * share)


def required_concepts(pattern, budget):
    """The concepts a search must have looked for before a negative answer means anything.

    The alternative concept is demanded only when the budget can actually pay for
    it. A search for the exposure concept alone is complete for an exact query and
    incomplete for the same query relaxed, which is the whole point.
    """
    alternative = next(r for r in pattern['relaxations'] if r['op'] == 'concept_alternative')
    needed = {pattern['events']['followup']['concept'], pattern['events']['exposure']['concept']}
    if (budget['max_relaxed_constraints'] >= 1
            and Decimal(budget['max_total_cost']) >= Decimal(alternative['cost'])):
        needed.add(alternative['concept'])
    return needed


def required_window(pattern, budget, events):
    """The span on the measurement clock that the search must have covered.

    Anchored on the measurements actually held, because those are the only
    follow-up candidates the matcher will consider. The read-back is the deeper
    of the two constraint windows, with the exposure window widened by whatever
    the budget can reach.
    """
    constraints = {c['id']: c for c in pattern['constraints']}
    back = max(constraints['lab_window']['seconds'] * US,
               constraints['exposure_window']['seconds'] * US + reachable_extension(pattern, budget))
    times = [e['time']['start_min_us'] for e in events if e['event_kind'] == 'measurement'
             and e['time']['start_min_us'] is not None]
    if not times:
        return None
    return min(times) - back, max(times)


def uncovered_demands(case, pattern, budget):
    """What this question needs that the declared search did not cover.

    Returns a sorted list of demands the coverage record does not meet; an empty
    list means a negative answer is a conclusion rather than an absence of
    looking. Raises when no warrant is supplied at all: a missing warrant is a
    defective case, not a permissive default.
    """
    search = case.get('source_search')
    if search is None:
        raise ValueError('INVALID_CASE: no source_search warrant. A bare '
                         'source_search_complete flag no longer establishes completeness.')
    basis = search.get('basis')
    if basis == 'stipulated':
        return []
    if basis != 'coverage':
        raise ValueError(f'INVALID_CASE: unknown source-search basis {basis!r}')
    gaps = []
    events = [e for e in case['events'] if e['patient_id'] == case['patient_id']
              and e['episode_id'] == case['episode_id']]
    clocks = {e['time']['clock_id'] for e in events} or {search['clock_id']}
    if clocks != {search['clock_id']}:
        gaps.append(f'clock {sorted(clocks - {search["clock_id"]})} outside the searched clock')
    window = required_window(pattern, budget, events)
    if window is not None:
        start, end = window
        if search['from_us'] is not None and search['from_us'] > start:
            gaps.append(f'{(search["from_us"] - start) / US / 86400:.2f} days before the '
                        f'searched window')
        if search['to_us'] is not None and search['to_us'] < end:
            gaps.append(f'{(end - search["to_us"]) / US / 86400:.2f} days after the '
                        f'searched window')
    missing = required_concepts(pattern, budget) - set(search['concepts'])
    gaps.extend(f'concept {concept} never searched for' for concept in sorted(missing))
    return sorted(gaps)


def without_source_search(case):
    """A copy of `case` whose warrant declares that nothing was searched.

    The demo exposes source completeness as a control. That control used to set a
    boolean; completeness is now derived, so it has to produce a coverage record
    instead, and this is the record that covers nothing at all. Keeping it here
    rather than in the demo means the three call sites cannot drift apart.
    """
    narrowed = copy.deepcopy(case)
    narrowed['source_search'] = {'basis': 'coverage',
                                 'clock_id': case['source_search']['clock_id'],
                                 'from_us': 0, 'to_us': 0, 'concepts': []}
    return narrowed


def evaluate(case, pattern, taxonomy):
    validate_events(case['events'])
    budget = case.get('budget_override') or pattern['budget']
    # Completeness is derived here rather than read off the case. The demands below
    # widen with the budget, so a search that was genuinely complete for the exact
    # question can be incomplete for the same question relaxed.
    uncovered = uncovered_demands(case, pattern, budget)
    if uncovered:
        return outcome('INDETERMINATE', 'UNRESOLVED', None, [], None,
                       ['INCOMPLETE_SOURCE_SEARCH'], uncovered=uncovered,
                       basis=case['source_search']['basis'])
    if case['age'] < pattern['scope']['minimum_age']:
        return outcome('FAIL', 'NONE', None, [], None, ['AGE_INELIGIBLE'])
    events = [e for e in case['events'] if e['patient_id'] == case['patient_id']
              and e['episode_id'] == case['episode_id']
              and e['experiencer'] == 'patient' and e['status'] == 'performed']
    constraints = {c['id']: c for c in pattern['constraints']}
    lab_window = constraints['lab_window']['seconds'] * US
    limit = constraints['exposure_window']['seconds'] * US
    threshold = Decimal(constraints['delta']['value'])
    required_unit = constraints['delta']['unit']
    alternative = next(r for r in pattern['relaxations'] if r['op'] == 'concept_alternative')
    extension = next(r for r in pattern['relaxations'] if r['op'] == 'extend_max_gap')
    extension_us = extension['extra_seconds'] * US
    labs = [e for e in events if e['event_kind'] == 'measurement'
            and entails(e['concept'], pattern['events']['followup']['concept'], taxonomy)]
    exposures = [e for e in events if e['event_kind'] == 'administration']
    exacts, relaxed = [], []
    unknown_data = False
    possible_exact = False
    reasons = set()
    for followup in labs:
        ft = followup['time']['start_min_us']
        if ft is None:
            unknown_data = True
            continue
        candidates = []
        for baseline in labs:
            if baseline['event_id'] == followup['event_id']:
                continue
            if baseline['time']['clock_id'] != followup['time']['clock_id']:
                unknown_data = True
                continue
            bt = baseline['time']['start_min_us']
            if bt is None:
                unknown_data = True
                continue
            if 0 < ft - bt <= lab_window:
                candidates.append(baseline)
        if not candidates:
            reasons.add('NO_OBSERVED_BINDING')
            continue
        if any(e['value'] is None or e['unit'] != required_unit or e['comparator'] != 'eq'
               for e in candidates + [followup]):
            unknown_data = True
            reasons.add('UNRESOLVED_MEASUREMENT')
            continue
        baseline = min(candidates, key=lambda e: (Decimal(e['value']),
                         e['time']['start_min_us'], e['event_id']))
        if Decimal(followup['value']) - Decimal(baseline['value']) < threshold:
            reasons.add('HARD_VALUE_FAILURE')
            continue
        for exposure in exposures:
            sem_exact = entails(exposure['concept'], pattern['events']['exposure']['concept'], taxonomy)
            sem_alt = entails(exposure['concept'], alternative['concept'], taxonomy)
            if not (sem_exact or sem_alt):
                reasons.add('NO_PERMITTED_CONCEPT')
                continue
            if exposure['time']['clock_id'] != followup['time']['clock_id']:
                unknown_data = True
                continue
            elo, ehi = exposure['time']['start_min_us'], exposure['time']['start_max_us']
            if elo is None or ehi is None:
                unknown_data = True
                continue
            gap_min, gap_max = ft-ehi, ft-elo
            binding = {'baseline': baseline['event_id'], 'exposure': exposure['event_id'],
                       'followup': followup['event_id']}
            tie = tuple(binding[k] for k in sorted(binding))
            if sem_exact and gap_max > 0 and gap_min <= limit:
                possible_exact = True
            exact = sem_exact and gap_min > 0 and gap_max <= limit
            if exact:
                exacts.append((tie, binding))
            if gap_min <= 0 or gap_max > limit + extension_us:
                reasons.add('HARD_TEMPORAL_FAILURE')
                continue
            semantic_cost = Decimal(0) if sem_exact else Decimal(alternative['cost'])
            temporal_cost = max(Decimal(0), Decimal(gap_max-limit)/Decimal(extension_us)) * Decimal(extension['maximum_cost'])
            components = {'exposure': semantic_cost, 'exposure_window': temporal_cost}
            altered = [k for k,v in components.items() if v > 0]
            total = sum(components.values(), Decimal(0))
            if total <= Decimal(budget['max_total_cost']) and len(altered) <= budget['max_relaxed_constraints']:
                relaxed.append((total, len(altered), tie, binding, altered, components))
            else:
                reasons.add('BUDGET_EXCEEDED')
    exact_status = 'PASS' if exacts else ('INDETERMINATE' if unknown_data or possible_exact else 'FAIL')
    basis = case['source_search']['basis']
    if exacts:
        binding = min(exacts)[1]
        return outcome('PASS','EXACT',Decimal(0),[],binding,[],
                       {'exposure':Decimal(0),'exposure_window':Decimal(0)},basis=basis)
    if relaxed:
        best = min(relaxed, key=lambda r:r[:3])
        return outcome(exact_status,'RELAXED',best[0],best[4],best[3],[],best[5],basis=basis)
    if unknown_data or possible_exact:
        return outcome(exact_status,'UNRESOLVED',None,[],None,sorted(reasons),basis=basis)
    return outcome(exact_status,'NONE',None,[],None,sorted(reasons or {'NO_OBSERVED_BINDING'}),
                   basis=basis)


def outcome(exact, accepted, cost, relaxed, binding, reasons, components=None,
            uncovered=None, basis=None):
    return {'exact_status':exact,'accepted_as':accepted,'total_cost':decimal_text(cost),
            'relaxed_constraint_ids':relaxed,'baseline_event_id':binding['baseline'] if binding else None,
            'binding':binding,'reason_codes':reasons,
            'components':{k:decimal_text(v) for k,v in (components or {}).items()},
            # A stipulated warrant is carried into the result so that a reader can tell
            # a checked completeness from an asserted one without opening the case.
            'completeness_basis':basis,'uncovered_demands':uncovered or [],
            'clinical_knowledge_status':'UNDETERMINED'}


def main():
    pattern = json.loads((ROOT/'examples/exemplar.pattern.json').read_text())
    taxonomy = json.loads((ROOT/'ontology/toy-taxonomy.json').read_text())
    cases = json.loads((ROOT/'examples/cases.json').read_text())
    validate_pattern(pattern, pattern)
    outputs = []
    for case in cases:
        actual = evaluate(case, pattern, taxonomy)
        for key,value in case['expected'].items():
            assert actual[key] == value, (case['case_id'],key,actual[key],value)
        if actual['total_cost'] is not None:
            assert sum((Decimal(v) for v in actual['components'].values()),Decimal(0)) == Decimal(actual['total_cost'])
            assert (actual['accepted_as'] == 'EXACT') == (Decimal(actual['total_cost']) == 0)
        outputs.append({'case_id':case['case_id'],'passed':True,'actual':actual})
    assert entails('ex:DrugAChild','ex:DrugA',taxonomy)
    assert not entails('ex:DrugA','ex:DrugAChild',taxonomy)
    assert not entails('ex:DrugB','ex:DrugA',taxonomy)
    # Complete-search budget monotonicity on every supplied case.
    for case in cases:
        last = False
        for amount in ['0','0.5','1','1.5','2']:
            c = copy.deepcopy(case)
            c['budget_override']={'max_relaxed_constraints':2,'max_total_cost':amount}
            accepted = evaluate(c,pattern,taxonomy)['accepted_as'] in ('EXACT','RELAXED')
            assert not last or accepted, (case['case_id'],'budget monotonicity')
            last = accepted
    # Context and record completeness have dedicated adversarial checks.
    c = copy.deepcopy(cases[0]); c['events'][1]['status']='not_given'
    assert evaluate(c,pattern,taxonomy)['accepted_as'] == 'NONE'
    c = copy.deepcopy(cases[0]); c['source_search']={'basis':'stipulated','reason':'probe'}
    assert evaluate(c,pattern,taxonomy)['completeness_basis'] == 'stipulated'
    c = copy.deepcopy(cases[0]); c['source_search']['concepts']=[]
    assert evaluate(c,pattern,taxonomy)['accepted_as'] == 'UNRESOLVED'
    c = copy.deepcopy(cases[0]); del c['source_search']
    try:
        evaluate(c,pattern,taxonomy)
        raise AssertionError('A case with no completeness warrant was evaluated')
    except ValueError:
        pass
    # The budget widens what must have been searched. A window that is complete for
    # the exact question is incomplete for the same question relaxed, and the relaxed
    # run must say so instead of reporting a confident negative.
    exact_cover = copy.deepcopy(cases[0])
    constraints = {c['id']: c for c in pattern['constraints']}
    labs = [e['time']['start_min_us'] for e in exact_cover['events']
            if e['event_kind'] == 'measurement']
    exact_cover['source_search'].update(
        from_us=min(labs) - constraints['exposure_window']['seconds']*US, to_us=max(labs))
    for amount, expected in (('0', False), ('2', True)):
        c = copy.deepcopy(exact_cover)
        c['budget_override'] = {'max_relaxed_constraints':2,'max_total_cost':amount}
        got = evaluate(c,pattern,taxonomy)
        assert bool(got['uncovered_demands']) == expected, (amount, got['uncovered_demands'])
    altered=copy.deepcopy(pattern);altered['events']['extra']={'kind':'other','concept':'ex:Noise'}
    try:
        validate_pattern(altered,pattern)
        raise AssertionError('Unsupported pattern accepted')
    except ValueError:
        pass
    report={'scope':'constructed three-slot exemplar only','cases_passed':len(outputs),
            'properties':['cost decomposition','zero-cost exact equivalence on supplied cases',
                          'subclass direction','budget monotonicity on supplied cases',
                          'not-given exclusion','incomplete-source propagation','unsupported-pattern rejection',
                          'completeness derived from declared coverage','budget widens the required coverage'],
            'results':outputs,'production_readiness_claim':False}
    (ROOT/'verification').mkdir(exist_ok=True)
    (ROOT/'verification/reference-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'cases_passed':len(outputs),'properties_passed':len(report['properties']),
                      'report':'verification/reference-report.json'}))


if __name__ == '__main__':
    main()
