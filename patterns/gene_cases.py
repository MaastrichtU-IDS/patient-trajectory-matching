"""Small seeded cases for gene steps in the store's match request (D38).

Shared by `test_gene_steps.py` and the ad hoc soundness runs the decision log reports. A case is an engine patient on a
small integer grid (so `trajectory_graph_worlds` can enumerate every choice of its times), with gene-report events of
gene G or H (each a sample time and a chain of one to three reports, classifications drawn from both closed
vocabularies, a missing one and an unknown one, recorded at or after the sample) and letter mentions of G, and a gene
request: a gene step D on G, related to the case's other steps or to the period, beside
- `plain`: up to two concept steps on x, y or z under any, first or last (`derived_cases.tiny_patient`'s events);
- `derived`: an Nth occurrence or a count (D32; `derived_cases.tiny_case`'s request and patient);
- `relative`: a rise or fall (D33; `derived_cases.relative_case`'s request, patient and readings): the Tree and Graph
  shape.

`store_result` runs the store's own per-patient path (`store_conditions_gene.Request`, `findings_from_events`,
`derive_patient`, `store_conditions_relative.match_derived`); `reference_result` the reference's
(`gene_reference.patient_result`); `check` the worlds checker, reading gene steps from the written rule: the true
findings (`gene_reference.report_finding`) as points carrying the step's tag. An undetermined record is the gene step's
excluded record on both sides (`store_conditions_gene.apply_undetermined`); the checker reads it independently (D38
review, should-fix 3): every unknown classification read as negative and as not negative, each reading's findings as
points; every claim a result makes must hold in every reading, and over the kept records (the reading in which an
undetermined record has no finding, D44 review) (a `none` the record could change is weakened to
`not_comparable` first, and claims nothing; a `possible` result or a match beside `unresolved` the record may make exact
says so, and claims only that its binding holds in some world, D44). Until D44 a weakened `possible` or `unresolved`
result was left to the reference.
"""
from __future__ import annotations

import copy
from itertools import product
import random

from . import derived_cases as dc
from . import execution_store as es
from . import gene_reference as gr
from . import store_conditions_gene as gc
from . import store_conditions_relative as rc
from . import trajectory_graph as tg
from . import trajectory_graph_worlds as tw

GENERATORS = ('plain', 'derived', 'relative')
CLASSES = ('pathogenic', 'pathogenic', 'uncertain significance', 'uncertain significance', 'likely pathogenic',
           'homozygous deletion', 'no relevant variant', 'no relevant variant', 'no relevant variant', 'benign',
           'likely benign', None, 'see report')
GENE = {'report': 'first_not_negative', 'timed_by': 'recorded_at'}


def gene_events(rng):
    """(events, {event id: reports}, mention ids): gene-report events of G or H and letter mentions of G."""
    events, reports, mentions = [], {}, set()
    for k in range(rng.choice((0, 1, 1, 2, 2, 3))):
        lo = rng.randint(0, 4)
        e = {'id': f'g{k}', 'concepts': [rng.choice(('G', 'G', 'G', 'H'))], 'start': [lo, lo + rng.choice((0, 0, 1))]}
        times = sorted(rng.randint(lo, lo + 4) for _ in range(rng.choice((1, 1, 2, 2, 3))))
        reports[e['id']] = [{'record_id': f'g{k}r{j}', 'recorded_at': t, 'classification': rng.choice(CLASSES),
                             'status': 'final' if j == 0 else 'amended'} for j, t in enumerate(times)]
        events.append(e)
    for k in range(rng.choice((0, 0, 1))):
        e = {'id': f'm{k}', 'concepts': ['G'], 'start': [0, rng.randint(0, 8)]}
        events.append(e)
        mentions.add(e['id'])
    return events, reports, mentions


def add_gene_step(rng, query, conditions):
    """The request with a gene step D on G, related to an earlier step or to the period's start, or to nothing."""
    query = copy.deepcopy(query)
    steps = [s['id'] for s in query['steps']]
    query['steps'].append({'id': 'D', 'concept': 'G', 'occurrence': 'any'})
    pick = rng.random()
    if pick < 0.75 and steps:
        a = rng.choice(steps)
        lo = rng.randint(-4, 2)
        relation = {'id': 'RD', 'kind': 'after', 'from': a, 'to': 'D', 'min': lo, 'max': lo + rng.randint(1, 6)}
        if rng.random() < 0.5:
            relation.update({'from': 'D', 'to': a})
        query['relations'].append(relation)
        if rng.random() < 0.3:
            query['relaxations'].append({'id': 'xRD', 'kind': 'extend_max', 'relation': 'RD',
                                         'max': relation['max'] + rng.randint(1, 3), 'cost': rng.choice(('0.5', '1'))})
    elif pick < 0.9:
        query['relations'].append({'id': 'RDS', 'kind': 'after', 'from': 'START', 'to': 'D', 'min': 0,
                                   'max': rng.randint(0, 5)})
    steps_ = dict((conditions or {}).get('steps') or {})
    steps_['D'] = {'gene': dict(GENE)}
    out = {'profile': gc.GENE_PROFILE, 'steps': steps_}
    if (conditions or {}).get('relaxations'):
        out['relaxations'] = conditions['relaxations']
    return query, out


def _plain(rng):
    patient, mentions = dc.tiny_patient(rng, rng.random() < 0.3)
    steps = []
    for i in range(rng.randint(0, 2)):
        steps.append({'id': 'AB'[i], 'concept': rng.choice(dc.CONCEPTS), 'occurrence': rng.choice(('any', 'any', 'first',
                                                                                                    'last'))})
    relations, relaxations = [], []
    for i in range(1, len(steps)):
        lo = rng.randint(-3, 2)
        relations.append({'id': f'R{i}', 'kind': 'after', 'from': steps[0]['id'], 'to': steps[i]['id'], 'min': lo,
                          'max': lo + rng.randint(0, 4)})
    for s in steps:
        if s['concept'] != 'z' and rng.random() < 0.25:
            relaxations.append({'id': f'w{s["id"]}', 'kind': 'widen_concept', 'step': s['id'], 'concept': 'z',
                                'cost': rng.choice(('0.5', '1'))})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '2')),
             'search_budget': rng.choice((40, 40, 2000))}
    return query, None, patient, mentions, None


def case(seed, generator):
    """(query, conditions, patient, mentions, gene reports, readings or None) for one seed; the request is valid."""
    rng = random.Random(f'gene/{generator}/{seed}')
    while True:
        if generator == 'plain':
            query, conditions, patient, mentions, readings = _plain(rng)
        elif generator == 'derived':
            query, conditions, patient, mentions = dc.tiny_case(rng.randrange(10 ** 9), rng.choice(dc.FEATURES),
                                                                rng.choice(('multi', 'coupled')))
            readings = None
        else:
            query, conditions, patient, mentions, readings = dc.relative_case(rng.randrange(10 ** 9),
                                                                             rng.choice(dc.RELATIVE_GENERATORS))
        extra, reports, said = gene_events(rng)
        patient = dict(patient, events=list(patient['events']) + extra)
        mentions = frozenset(mentions) | said
        query, conditions = add_gene_step(rng, query, conditions)
        try:
            tg.patient_times(patient)
            gc.Request(query, conditions)
        except (tg.TrajectoryError, es.StoreError):
            continue
        return query, conditions, patient, mentions, reports, readings


def _kinds(patient, reports, mentions):
    return {e['id']: 'genetic_result' if e['id'] in reports else es.MENTION_KIND if e['id'] in mentions else 'other'
            for e in patient['events']}


def store_result(query, conditions, patient, mentions, reports, readings=None):
    request = gc.Request(query, conditions)
    pq = tg.PreparedQuery(request.query)
    kinds = _kinds(patient, reports, mentions)
    findings = gc.findings_from_events(request, patient['events'], kinds.get,
                                       lambda eid: {'gene_reports': reports[eid]} if eid in reports else {})
    derived = gc.derive_patient(request, patient['patient_id'], patient['window'], patient['events'], mentions,
                                dc.store_readings(readings) if readings else None, findings)
    return gc.apply_undetermined(request, pq.query, rc.match_derived(pq, request, derived), findings, patient['window'])


def reference_result(query, conditions, patient, mentions, reports, readings=None):
    return gr.patient_result(query, conditions, patient, mentions, reports,
                             dc.checker_readings(readings) if readings else None)


def outcomes(reports):
    """The finding times (None: no finding) over every reading of each unknown classification as negative or not."""
    unknown = [i for i, r in enumerate(reports) if r.get('classification') not in gr.NEGATIVE | gr.NOT_NEGATIVE]
    found = set()
    for bits in product((False, True), repeat=len(unknown)):
        reading = [dict(r) for r in reports]
        for i, b in zip(unknown, bits):
            reading[i]['classification'] = 'pathogenic' if b else 'no relevant variant'
        verdict, at = gr.report_finding(reading)
        found.add(at if verdict == 'finding' else None)
    return sorted(found, key=lambda x: (x is None, x))


def check(result, query, conditions, patient, mentions, reports, readings=None, kept=None):
    """The worlds checker's violations: the gene step read as a plain step over the true findings' points, in every
    reading of the unknown classifications (the whole source, `whole=True`); and (D44 review, S1) over the records the
    store kept (`whole=False`): the reading in which an undetermined record has no finding, on `kept` (the patient
    without the whole source's other left-out records; the patient itself by default)."""
    tag = f'{es._DERIVED_PREFIX}D/own'
    records = [e for e in patient['events'] if e['id'] in reports and 'G' in e['concepts']]
    choices = [outcomes(reports[e['id']]) for e in records]
    worlds = list(product(*choices))
    wrong = []  # D44: every result is judged in full, a weakened one too, by the claims it still makes
    for chosen in worlds:
        points = [{'id': f'{tag}@{e["id"]}', 'concepts': [tag], 'start': [at, at]}
                  for e, at in zip(records, chosen) if at is not None]
        wrong += [f'{w} (reading {list(chosen)})' if len(worlds) > 1 else w
                  for w in _check_one(result, query, conditions, patient, mentions, readings, tag, points)]
    known = []
    for e in records:
        verdict, at = gr.report_finding(reports[e['id']])
        if verdict == 'finding':
            known.append({'id': f'{tag}@{e["id"]}', 'concepts': [tag], 'start': [at, at]})
    wrong += [f'{w} (kept records)' for w in _check_one(result, query, conditions, patient if kept is None else kept,
                                                       mentions, readings, tag, known, whole=False)]
    return wrong


def _check_one(result, query, conditions, patient, mentions, readings, tag, points, whole=True):
    truth = dict(patient, events=list(patient['events']) + points)
    q = copy.deepcopy(query)
    for s in q['steps']:
        if s['id'] == 'D':
            s['concept'] = tag
    rest = {sid: raw for sid, raw in conditions['steps'].items() if sid != 'D'}
    if rest:
        others = {'profile': rc.CONDITIONS_PROFILE_1_2, 'steps': rest}
        if conditions.get('relaxations'):
            others['relaxations'] = conditions['relaxations']
        return tw.check_derived(result, q, others, truth, mentions,
                                readings=dc.checker_readings(readings) if readings else None, whole=whole)
    binding = result.get('binding') or result.get('relaxed_match', {}).get('binding') or {}
    known = {e['id'] for e in truth['events']}
    if any(eid not in known for eid in binding.values()):
        return ['a binding names an event or point the truth does not hold']
    return tw.check(result, q, truth, whole=whole)  # whole: the undetermined records read every way (D44)
