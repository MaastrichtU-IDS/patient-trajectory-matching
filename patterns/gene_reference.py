"""Gene steps (D38), written again from the decision log's D38 and `GENE_RULES`, for differentials against the store.

Nothing here imports `store_conditions_gene`: the request is read here, a record's finding is decided by this module's
own reading of its reports, and the rest is `execution_store_reference`'s exhaustive match (D32's and D33's derived
steps, coverage and the excluded-records rule), on the ingest document. Its pieces:

- `report_finding`: the reports ranked by (recorded_at, position); a classification outside both closed vocabularies
  stops the reading as undetermined; a negative one is passed over; the first one not negative is the finding;
- each finding becomes, for each gene step on its gene (through the closure), a point event carrying the step's tag
  (`derived-step:<step>/own`), id `<tag>@<event id>`, at `[t, t]`; an undetermined record becomes an excluded record
  that only that gene step reads (a pseudo-concept of its own), its start the span of its reports' times;
- the gene step's coverage is that of its gene concept, and its excluded records are those of its gene concept and its
  own undetermined ones, reached by the query's scope.
"""
from __future__ import annotations

import copy

from . import execution_store_reference as ref

PROFILE = 'execution-store-conditions-gene-1.0'
REPORT_KINDS_LABEL = 'gene_report_kinds'
NEGATIVE = frozenset({'no relevant variant', 'benign', 'likely benign'})
NOT_NEGATIVE = frozenset({'pathogenic', 'likely pathogenic', 'uncertain significance', 'homozygous deletion'})


def report_finding(reports):
    """('finding', t), ('negative', None) or ('undetermined', (lo, hi)) of one record's reports."""
    ranked = sorted(enumerate(reports), key=lambda pair: (pair[1]['recorded_at'], pair[0]))
    for _, report in ranked:
        verdict = report.get('classification')
        if verdict in NEGATIVE:
            continue
        if verdict in NOT_NEGATIVE:
            return 'finding', report['recorded_at']
        times = sorted(r['recorded_at'] for r in reports)
        return 'undetermined', (times[0], times[-1])
    return 'negative', None


def _split(query, conditions):
    """(gene steps {step: gene concept}, the query with each gene step's concept its tag, the 1.2 conditions of the
    other steps or None)."""
    genes = {sid for sid, raw in conditions['steps'].items() if isinstance(raw, dict) and 'gene' in raw}
    gene_of = {s['id']: s['concept'] for s in query['steps'] if s['id'] in genes}
    tagged = copy.deepcopy(query)
    for s in tagged['steps']:
        if s['id'] in genes:
            s['concept'] = f'{ref.DERIVED}{s["id"]}/own'
    rest = {sid: raw for sid, raw in conditions['steps'].items() if sid not in genes}
    others = {'profile': ref.RELATIVE_PROFILE, 'steps': rest}
    if 'relaxations' in conditions:
        others['relaxations'] = conditions['relaxations']
    return gene_of, tagged, others


def _pseudo(sid):
    return f'#gene-reference-undetermined/{sid}'


def gene_query(ingest, query, conditions):
    """The reference's reading of a gene request: a `DerivedQuery` over the tagged query, with each gene step's
    coverage and excluded records read by its gene concept (and its own pseudo-concept)."""
    gene_of, tagged, others = _split(query, conditions)
    dq = ref.DerivedQuery(ingest, tagged, others)
    for sid, concept in gene_of.items():
        dq.base[sid] = {concept, _pseudo(sid)}
        dq.coverage_loads[sid] = {concept}
    return dq, gene_of


def patient_points(patient, gene_of, ancestors, kinds):
    """(point events, excluded records) a patient's gene reports give the gene steps."""
    points, excluded = [], []
    for e in patient['events']:
        if e['kind'] not in kinds:
            continue
        concepts = ref.expanded(e['concepts'], ancestors)
        for sid, concept in sorted(gene_of.items()):
            if concept not in concepts:
                continue
            verdict, at = report_finding(e['evidence']['gene_reports'])
            if verdict == 'finding':
                points.append({'id': f'{ref.DERIVED}{sid}/own@{e["event_id"]}', 'concepts': [f'{ref.DERIVED}{sid}/own'],
                               'start': [at, at]})
            elif verdict == 'undetermined':
                excluded.append({'source': e['source'], 'concept': _pseudo(sid), 'start': list(at),
                                 'reason': 'GENE_REPORT_CLASSIFICATION_UNDETERMINED'})
    return points, excluded


def gene_exhaustive_match(ingest, query, windows, conditions):
    """Every patient's result for a gene request: the full, closure-expanded events (with value tags) and the gene
    points, the derived passes, then coverage on `none`, then the excluded-records rule."""
    ancestors = dict(ingest['closure']['ancestors'])
    dq, gene_of = gene_query(ingest, query, conditions)
    for sid in gene_of:
        ancestors[_pseudo(sid)] = []
    labels = (ingest.get('provenance') or {}).get('labels') or {}
    kinds = {k for k in labels.get(REPORT_KINDS_LABEL, '').split(',') if k}
    if not kinds:
        raise ValueError('GENE_REPORTS_NOT_DECLARED')
    recorders = {}
    for p in ingest['patients']:
        for e in p['events']:
            for c in ref.expanded(e['concepts'], ingest['closure']['ancestors']):
                recorders.setdefault(c, set()).add(e['source'])
    if dq.value is not None:
        recorders.update(dq.value.recorders)
    recorders.update(dq.unit_sources)
    period = [k for k in labels.get('period_reading_kinds', '').split(',') if k]
    results = []
    for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
        window = windows.get(p['patient_id'])
        events = []
        for e in p['events']:
            concepts = ref.expanded(e['concepts'], ingest['closure']['ancestors'])
            if dq.value is not None:
                concepts = concepts | dq.value.tags_of(p['patient_id'], e['event_id'])
            event = {'id': e['event_id'], 'concepts': sorted(concepts), 'start': list(e['start'])}
            if 'end' in e:
                event['end'] = list(e['end'])
            event.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
            events.append(event)
        points, undetermined = patient_points(p, gene_of, ingest['closure']['ancestors'], kinds)
        events += points
        mentions = {e['event_id'] for e in p['events'] if e['kind'] == 'mention'}
        readings = ref.patient_readings(p, ingest['closure']['ancestors'], period)
        result, nth_points = ref.derived_result(dq, p['patient_id'], window, events, mentions, readings)
        if result['status'] == 'none':
            result = ref.coverage_rule(dq.query, dq.coverage_loads, recorders, p['coverage'], window, result)
        if window is not None and result['status'] != 'not_comparable':
            seen = dict(p, excluded=list(p.get('excluded', [])) + undetermined)
            result = ref.derived_admission(dq, result, seen, events, ancestors, window, nth_points)
        results.append(result)
    return results


def patient_result(query, conditions, patient, mentions, gene_reports, readings=None):
    """One engine patient's result before coverage and the excluded-records rule (the small cases): `gene_reports`
    maps each gene-report event's id to its reports; an event's gene is read from its concepts as listed."""
    empty = {'closure': {'ancestors': {}}, 'patients': []}
    dq, gene_of = gene_query(empty, query, conditions)
    points = []
    for e in patient['events']:
        if e['id'] not in gene_reports:
            continue
        for sid, concept in sorted(gene_of.items()):
            if concept in e['concepts']:
                verdict, at = report_finding(gene_reports[e['id']])
                if verdict == 'finding':
                    points.append({'id': f'{ref.DERIVED}{sid}/own@{e["id"]}', 'concepts': [f'{ref.DERIVED}{sid}/own'],
                                   'start': [at, at]})
    events = list(patient['events']) + points
    return ref.derived_result(dq, patient['patient_id'], patient['window'], events, mentions, readings)[0]
