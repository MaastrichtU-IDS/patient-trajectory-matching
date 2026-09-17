"""Readable descriptions of completed interval results, without rerunning inference.

Example arithmetic explains a witness or counterexample only. Whole-query
impossibility is attributed to the reported contradiction, never to each conjunct.
"""
from decimal import Decimal


ALLEN_LABELS = {
    'before': 'ends before {right} starts',
    'meets': 'ends exactly when {right} starts',
    'overlaps': 'starts before {right}, overlaps it, and ends before it ends',
    'starts': 'starts with {right} and ends earlier',
    'during': 'starts after {right} starts and ends before it ends',
    'finishes': 'starts after {right} starts and ends with it',
    'equals': 'has the same start and end as {right}',
    'after': 'starts after {right} ends',
    'met_by': 'starts exactly when {right} ends',
    'overlapped_by': 'starts during {right} and ends after it ends',
    'started_by': 'starts with {right} and ends later',
    'contains': 'starts before {right} starts and ends after it ends',
    'finished_by': 'starts before {right} starts and ends with it',
}


def time_text(value):
    """Minute display with exact microseconds when decimal minutes repeat."""
    minutes = Decimal(value) / Decimal(60000000)
    rounded = format(minutes, '.9f').rstrip('0').rstrip('.') or '0'
    if Decimal(rounded) * 60000000 != value:
        return f'about {rounded} min ({value} microseconds)'
    return rounded + ' min'


def describe_constraint(c):
    op = c['operator']
    if op == 'duration':
        return f"{c['slot']} duration is between {time_text(c['minimum_us'])} and {time_text(c['maximum_us'])}, inclusive"
    left, right = c['left'], c['right']
    if op == 'gap':
        return (f"{right} start minus {left} end is between {time_text(c['min_gap_us'])} "
                f"and {time_text(c['max_gap_us'])}, inclusive")
    if op == 'minimum_overlap':
        return f"{left} and {right} share at least {time_text(c['minimum_us'])}"
    return left + ' ' + ALLEN_LABELS[op].format(right=right)


def _example(c, slots, events, variables, timeline):
    if timeline is None:
        return None
    op = c['operator']
    a = events[slots[c['slot'] if op == 'duration' else c['left']]['event_id']]
    s, e = timeline[a['start_var']], timeline[a['end_var']]
    if op == 'duration':
        value = e - s
        return {'satisfied': c['minimum_us'] <= value <= c['maximum_us'],
                'value_us': value, 'detail': 'Duration on this example timeline: ' + time_text(value) + '.'}
    b = events[slots[c['right']]['event_id']]
    if variables[a['start_var']]['clock_id'] != variables[b['start_var']]['clock_id']:
        return None
    u, v = timeline[b['start_var']], timeline[b['end_var']]
    if op == 'gap':
        value = u - e
        satisfied = c['min_gap_us'] <= value <= c['max_gap_us']
        detail = 'Signed gap on this example timeline: ' + time_text(value) + '.'
    elif op == 'minimum_overlap':
        value = max(0, min(e, v) - max(s, u))
        satisfied = value >= c['minimum_us']
        detail = 'Shared time on this example timeline: ' + time_text(value) + '.'
    else:
        satisfied = {
            'before': e < u, 'meets': e == u, 'overlaps': s < u < e < v,
            'starts': s == u and e < v, 'during': u < s and e < v,
            'finishes': u < s and e == v, 'equals': s == u and e == v,
            'after': v < s, 'met_by': s == v, 'overlapped_by': u < s < v < e,
            'started_by': s == u and v < e, 'contains': s < u and v < e,
            'finished_by': s < u and e == v,
        }[op]
        return {'satisfied': satisfied,
                'detail': (f"Example timeline: {c['left']} [{time_text(s)}, {time_text(e)}); "
                           f"{c['right']} [{time_text(u)}, {time_text(v)}).")}
    return {'satisfied': satisfied, 'value_us': value, 'detail': detail}


SUMMARIES = {
    'CERTAIN': 'At least one fixed recorded event binding satisfies every constraint on every timeline allowed by the source.',
    'POSSIBLE': 'A recorded event binding matches on at least one allowed timeline, but no fixed binding matches on every allowed timeline. Timing uncertainty remains.',
    'INCOMPARABLE': 'No certain or possible binding was established. At least one candidate uses clocks that the source does not align; its cross-clock constraints cannot be evaluated.',
    'NO_RECORDED_MATCH': 'No recorded candidate binding satisfies the complete query. This does not establish that the clinical events never occurred.',
}


def _evaluation(source, query, result, patient_id):
    events = {e['id']: e for e in source['events']}
    variables = {v['id']: v for v in source['variables']}
    bindings = [b for t in result['trajectories'] if t['patient_id'] == patient_id for b in t['bindings']]
    statuses = {b['status'] for b in bindings}
    status = next((s for s in ('CERTAIN', 'POSSIBLE', 'INCOMPARABLE') if s in statuses), 'NO_RECORDED_MATCH')
    cards = []
    for binding in bindings:
        state = binding['status']
        if state == 'IMPOSSIBLE':
            summary = 'The source and the complete set of constraints contradict one another for this binding; individual constraints are not each proven impossible.'
        elif state == 'CERTAIN':
            summary = 'Every constraint is guaranteed for this fixed binding by the source bounds and relations.'
        elif state == 'POSSIBLE':
            summary = 'The witness matches all constraints. The counterexample is another source-allowed timeline that breaks the query.'
        else:
            summary = 'Cross-clock comparisons are unavailable for this binding. No relative timing is inferred between these clocks.'
        cards.append({'status': state, 'summary': summary, 'slots': binding['slots'],
                      'contradiction_constraints': [c['id'] for c in query['constraints']
                          if any(edge.rsplit(':', 1)[0] == 'query:' + c['id'] for edge in binding.get('negative_cycle', []))],
                      'constraints': [{'id': c['id'], 'description': describe_constraint(c),
                          'witness': _example(c, binding['slots'], events, variables, binding.get('possible_witness')),
                          'counterexample': _example(c, binding['slots'], events, variables, binding.get('counterexample'))}
                          for c in query['constraints']]})
    return {'status': status, 'summary': SUMMARIES[status], 'bindings': cards}


def explain_patient(source, query, relaxation, patient_id, policy=None):
    """Describe one patient's completed extended result and explicit option changes."""
    evaluations = relaxation.get('evaluations', [])
    if relaxation.get('search_complete') is not True or not evaluations or any(
            e['result'].get('search_complete') is not True for e in evaluations):
        raise ValueError('Explanations require complete query evaluations')
    original = _evaluation(source, query, evaluations[0]['result'], patient_id)
    before = {c['id']: c for c in query['constraints']}
    options = []
    for evaluation in evaluations[1:]:
        option = evaluation['option']
        changed_query = option['query']
        explanation = _evaluation(source, changed_query, evaluation['result'], patient_id)
        options.append({'id': option['id'], 'cost': option['cost'], **explanation,
                        'changes': [{'target': c['id'], 'before': describe_constraint(before[c['id']]),
                                     'after': describe_constraint(c)} for c in changed_query['constraints']
                                    if c != before[c['id']]]})
    excluded = relaxation.get('excluded_by_budget', [])
    note = ('Excluded by the policy budget or change-count limit: ' + ', '.join(excluded) + '. No result was inferred for these options.'
            if excluded else 'All requested options were evaluated.' if options else 'No relaxation option was requested.')
    return {'patient_id': patient_id, 'original': original, 'options': options, 'budget_note': note,
            'preserved': 'Source records, endpoint uncertainty, clocks, event selectors and Allen relations stay fixed. Only explicitly listed metric constraints change. Certainty does not make the underlying times exact.',
            'scope': 'Results describe recorded events in the admitted source snapshot. They do not establish treatment effectiveness or clinical eligibility.'}
