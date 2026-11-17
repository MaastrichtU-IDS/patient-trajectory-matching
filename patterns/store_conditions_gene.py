"""Gene steps in the execution store's match request: profile `execution-store-conditions-gene-1.0` (D38).

A step may carry `gene: {report: first_not_negative, timed_by: recorded_at}`: it binds each coded genetic result of the
step's gene whose reports hold one that is not negative, at the *first* such report, as a point at that report's
`recorded_at`, across reclassification. It is the trajectory page's gene step (`shared/core.js`, `makeItem`;
`trajectory/app.js`, `candidates`) in the store. The contract is the decision log's D38 and `GENE_RULES` below.

Why a module of its own (D36's owner policy on build reuse, D34). The scaling report's build record hashes every module
a build loads, `patterns/execution_store.py` and the converter `patterns/synthetic_ingest.py` among them. This module
is loaded by no build, so both stay byte-identical and `--reuse-builds` can reuse the kept builds. It holds:

- *A converter wrapper* (`convert_patient`, `iter_patients`, `synthetic_provenance`, `synthetic_ingest`). The converter
  keeps a genetic result's coding but not its classification, and keeps only a record's latest version (D20), so a
  negative result looks like a pathogenic one and a reclassified variant's first report is gone. The wrapper calls the
  converter's own `convert_patient` unchanged and adds to each genetic result's evidence `gene_reports`: the reports of
  its record and of every record it supersedes (record id, `recorded_at` on the store's clock, classification,
  status), read from the source records the converter read. It adds and removes no event and changes no time, concept,
  coverage or excluded record. The reports are in `event_evidence`, so inside the snapshot's identity; the label
  `gene_report_kinds` and the rule's text are in its provenance. The proper fix is the converter keeping them (an option
  like `series`); it waits for the next change to `synthetic_ingest.py` (D38, option B).
- *The request* (`Request`): the 1.2 language of `store_conditions_relative` plus gene steps, each compiled into a
  derived tag (`derived-step:<step>/own`), as D32 compiles Nth steps.
- *The findings* (`_gene_findings`, `finding`): gene steps read `event_evidence` for the events of the label's kinds of
  the step's gene, a departure from D26's "matching never reads evidence", for gene-report events only (D38).
- *Matching*: per patient, one derived point per finding (`derive_patient`); a record whose classification leaves the
  finding undetermined is an excluded record of the gene step (D18), applied at match time; candidates, pruning, D18
  reach, the context and `match`. Where `store_conditions_relative` or `execution_store` has a function this needs with
  a gene branch, the module carries a copy with that branch (their sources are pinned by digest in the tests). Every
  request without the gene profile is `store_conditions_relative`'s own call, unchanged.
- *The joint profile* (D42), `execution-store-conditions-gene-coded-1.0`: the gene profile plus D39's `exclude_kinds`,
  answered as the gene request would be on the snapshot without the events of those kinds (`GENE_CODED_RULE`). Gene
  steps read the reports of the label's kinds, which may not be left out, so they are unchanged; every other step reads
  kept events only, with D39's coverage rule. `match` calls D39's hooks in `store_conditions_coded` (`derived_inputs`,
  `refined`, `_comparability`) with `request.exclude`, which is empty for the gene profile, rather than a third copy of
  `match`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os

from . import execution_store as es
from . import store_conditions_coded as cc
from . import store_conditions_relative as rc
from . import synthetic_ingest as si
from . import trajectory_graph as tg
from ._resources import ROOT

MODULE_PATH = 'patterns/store_conditions_gene.py'
GENE_PROFILE = 'execution-store-conditions-gene-1.0'
GENE_CODED_PROFILE = 'execution-store-conditions-gene-coded-1.0'  # D42: the gene profile with exclude_kinds
GENE_PROFILES = (GENE_PROFILE, GENE_CODED_PROFILE)
GENE_SPEC = {'report': 'first_not_negative', 'timed_by': 'recorded_at'}  # the only form accepted now
GENE_LABEL = 'gene_report_kinds'  # snapshot label: the event kinds whose evidence carries `gene_reports`
GENE_KINDS = ('genetic_result',)  # the synthetic source's gene reports
NEGATIVE = ('no relevant variant', 'benign', 'likely benign')
NOT_NEGATIVE = ('pathogenic', 'likely pathogenic', 'uncertain significance', 'homozygous deletion')
UNDETERMINED = 'GENE_REPORT_CLASSIFICATION_UNDETERMINED'
POINT_KIND = 'gene_report'  # how the parity helper names a derived gene point's kind
GENE_REPORT_RULE = ('gene_reports_v1: each genetic result event carries in its evidence `gene_reports`, the reports of '
                    'its record and of every record it supersedes, oldest first (record_id, recorded_at in '
                    'microseconds on the patient clock, classification as the source wrote it or null, status)')
GENE_RULES = {
    'reports': GENE_REPORT_RULE + '; read for coded events of the kinds the snapshot label gene_report_kinds lists, of '
                                  'the step\'s gene through the closure; letter mentions never bind a gene step',
    'finding': 'first_not_negative_v1: the reports in order of recorded_at, then supersession order; negative ones '
               '(no relevant variant, benign, likely benign) are passed over; the first that is pathogenic, likely '
               'pathogenic, uncertain significance or homozygous deletion is the finding; a missing or other '
               'classification before any finding leaves the record undetermined; all negative: no finding',
    'time': 'the finding is a point at its report\'s recorded_at, exactly, with its own variable; the sample\'s time '
            '(the event\'s start) is not read',
    'exclusions': 'an undetermined record is an excluded record of the gene step, its start from the earliest to the '
                  'latest recorded_at of its reports, reached by the query\'s scope (D18), applied at match time',
    'coverage': 'gene_recorders_v1: the store\'s coverage rule, a gene step recorded only by the sources holding an '
                'event of the label\'s kinds of its gene (never by a source of letter mentions alone), their coverage '
                'periods as the snapshot holds them (for the synthetic source, sample times)',
    'relaxations': 'none: a gene step has occurrence any, carries no other condition and no relaxation names it',
}
GENE_CODED_RULE = ('gene_exclude_kinds_v1: the request is answered as the same request as profile '
                   'execution-store-conditions-gene-1.0, without exclude_kinds, would be on the snapshot with every '
                   'event of an excluded kind removed (exclude_kinds_v1); the kinds the snapshot label '
                   'gene_report_kinds lists may not be excluded, so gene steps, their findings, undetermined records and '
                   'recorders are unchanged; every other step reads kept events only and is recorded by the sources '
                   'holding a kept event of its concepts; a left-out event is not an excluded record; the plan counts '
                   'each step\'s postings over every event, candidates are refined to kept events')
# Planted defects for the tests (D38 rules); None in every real run.
_D38_DEFECT = None
DEFECTS = ('negative_counted', 'reclassification_ignored', 'sample_time', 'vus_negative', 'mention_bound',
           'undetermined_ignored')
# Planted defects of the joint profile (D42 rules); None in every real run. `mention_read`: the joint path loads
# left-out events for the steps that are not gene steps; `coverage_letters`: every step of a joint request recorded by
# the store's own `source_concepts` over every event, a gene step by its gene concept (so letters record both).
_D42_DEFECT = None
D42_DEFECTS = ('mention_read', 'coverage_letters')


def is_gene_request(conditions):
    """Whether a match request's conditions are of the gene profile or the joint profile (D42), which this module alone
    answers."""
    return isinstance(conditions, dict) and conditions.get('profile') in GENE_PROFILES


# The converter wrapper.

def _report(record):
    return {'record_id': record['record_id'], 'recorded_at': si.micros(record['recorded_at']),
            'classification': (record.get('attributes') or {}).get('classification'), 'status': record.get('status')}


def gene_reports(patient, source, record_id):
    """The reports of a record and of every record it supersedes, oldest first (`GENE_REPORT_RULE`)."""
    records = {(r['source'], r['record_id']): r for r in patient['records']}
    chain, key, seen = [], (source, record_id), set()
    while key is not None:
        if key in seen:
            raise ValueError(f'GENE_REPORT_CHAIN_CYCLE:{record_id}')
        seen.add(key)
        record = records.get(key)
        if record is None:
            raise ValueError(f'GENE_REPORT_RECORD_MISSING:{key[0]}/{key[1]}')
        chain.append(_report(record))
        key = (record['source'], record['supersedes']) if record.get('supersedes') else None
    return chain[::-1]


def convert_patient(patient, codes, mention_time='at_or_before_letter', series=False):
    """`synthetic_ingest.convert_patient`, unchanged, with `gene_reports` added to each genetic result's evidence."""
    out, skipped = si.convert_patient(patient, codes, mention_time, series)
    for event in out['events']:
        if event['kind'] in GENE_KINDS:
            ev = event['evidence']
            event['evidence'] = {**ev, 'gene_reports': gene_reports(patient, ev['source'], ev['record_id'])}
    return out, skipped


def iter_patients(cohort, skipped=None, mention_time='at_or_before_letter', series=False):
    """`synthetic_ingest.iter_patients`, through the wrapper's `convert_patient`."""
    codes = si._codes(cohort)
    skipped = {} if skipped is None else skipped
    for patient in sorted(cohort['patients'], key=lambda p: p['patient_id']):
        converted, why = convert_patient(patient, codes, mention_time, series)
        for k, v in why.items():
            skipped[k] = skipped.get(k, 0) + v
        yield converted


def synthetic_provenance(cohort, skipped, mention_time='at_or_before_letter', series=False):
    """The converter's provenance with D36's period label (series on), the label `gene_report_kinds` and the rule."""
    out = rc.synthetic_provenance(cohort, skipped, mention_time, series)
    out['labels'] = {**(out.get('labels') or {}), GENE_LABEL: ','.join(GENE_KINDS)}
    out['gene_reports'] = GENE_REPORT_RULE
    return out


def synthetic_ingest(cohort, mention_time='at_or_before_letter', series=False):
    """The wrapper's ingest document (profile execution-store-ingest-2.0)."""
    header = si.synthetic_header(cohort)
    skipped = {}
    patients = list(iter_patients(cohort, skipped, mention_time, series))
    return {**header, 'provenance': synthetic_provenance(cohort, skipped, mention_time, series), 'patients': patients}


# Findings.

def finding(reports, start=None):
    """('found', (t, t)), ('none', None) or ('undetermined', (earliest, latest recorded_at)) of one record's reports
    (`GENE_RULES['finding']`). `start` is the event's start range, read only by a planted defect."""
    defect = _D38_DEFECT
    if not (isinstance(reports, list) and reports and all(
            isinstance(r, dict) and type(r.get('recorded_at')) is int for r in reports)):
        raise es.StoreError('GENE_REPORTS_MALFORMED', repr(reports)[:120])
    order = sorted(range(len(reports)), key=lambda i: (reports[i]['recorded_at'], i))
    if defect == 'reclassification_ignored':  # the record's own (latest) report alone
        order = [len(reports) - 1]
    for i in order:
        c = reports[i].get('classification')
        negative = c in NEGATIVE or (defect == 'vus_negative' and c == 'uncertain significance')
        if negative and not (defect == 'negative_counted' and c in NEGATIVE):
            continue
        if c in NOT_NEGATIVE or c in NEGATIVE:
            t = reports[i]['recorded_at']
            return 'found', (tuple(start) if defect == 'sample_time' else (t, t))
        times = [r['recorded_at'] for r in reports]
        return 'undetermined', (min(times), max(times))
    return 'none', None


def label_kinds(store):
    """The event kinds the snapshot declares gene reports (`gene_report_kinds`), refused by name when it declares none:
    a snapshot built without the wrapper holds no classification, and a gene step there would rest on nothing."""
    text = (store.manifest.get('labels') or {}).get(GENE_LABEL)
    kinds = tuple(k for k in (text or '').split(',') if k) if isinstance(text, str) else ()
    if not kinds:
        raise es.StoreError('GENE_REPORTS_NOT_DECLARED', GENE_LABEL)
    return kinds


def report_kinds(store, request):
    """`label_kinds`, refused by name when the request leaves one of them out (D42, `GENE_REPORT_KIND_EXCLUDED`): gene
    steps would then read events the request removed."""
    kinds = label_kinds(store)
    clash = sorted(set(kinds) & set(request.exclude))
    if clash:
        raise es.StoreError('GENE_REPORT_KIND_EXCLUDED', ', '.join(clash))
    return kinds


def findings_of(rows):
    """{patient_idx: {concept: {'found': {event id: (lo, hi)}, 'undetermined': [(lo, hi)]}}} from rows (patient_idx,
    concept, event id, start_lo, start_hi, kind, evidence object)."""
    out = {}
    for idx, concept, eid, lo, hi, kind, evidence in rows:
        mine = out.setdefault(idx, {}).setdefault(concept, {'found': {}, 'undetermined': []})
        reports = evidence.get('gene_reports') if isinstance(evidence, dict) else None
        if kind == es.MENTION_KIND and _D38_DEFECT == 'mention_bound':
            mine['found'][eid] = (lo, hi)
            continue
        if reports is None:
            raise es.StoreError('GENE_REPORTS_MISSING', eid)
        verdict, span = finding(reports, (lo, hi))
        if verdict == 'found':
            mine['found'][eid] = span
        elif verdict == 'undetermined':
            mine['undetermined'].append(span)
    return out


def findings_from_events(request, events, kind_of, evidence_of, kinds=GENE_KINDS):
    """One engine patient's entry of `_gene_findings`, from its events listed with their concepts as the closure gives
    them, `kind_of(event id)` and `evidence_of(event id)` (the small cases, which need no snapshot)."""
    concepts = sorted({spec['concept'] for spec in request.derived.values() if spec['kind'] == 'gene'})
    read = set(kinds) | ({es.MENTION_KIND} if _D38_DEFECT == 'mention_bound' else set())
    rows = [(0, c, e['id'], e['start'][0], e['start'][1], kind_of(e['id']), evidence_of(e['id']))
            for e in sorted(events, key=lambda e: e['id']) if kind_of(e['id']) in read
            for c in concepts if c in e['concepts']]
    return findings_of(rows).get(0, {})


def _gene_findings(store, request, idxs=None):
    """`findings_of` for the gene concepts of the request's gene steps, over these patients (all when None), read from
    `events`, the closure and `event_evidence` (D38)."""
    concepts = sorted({spec['concept'] for spec in request.derived.values() if spec['kind'] == 'gene'})
    if not concepts or (idxs is not None and not idxs):
        return {}
    kinds = list(label_kinds(store)) + ([es.MENTION_KIND] if _D38_DEFECT == 'mention_bound' else [])
    sql = f"""SELECT DISTINCT e.patient_idx, cc.ancestor, e.event_id, e.start_lo, e.start_hi, e.kind, ev.evidence
              FROM {{source}} JOIN event_concepts ec
                ON ec.bucket = e.bucket AND ec.patient_idx = e.patient_idx AND ec.event_id = e.event_id
              JOIN concept_closure cc ON cc.concept = ec.concept
              JOIN event_evidence ev
                ON ev.bucket = e.bucket AND ev.patient_idx = e.patient_idx AND ev.event_id = e.event_id
              WHERE e.kind IN {es._in(kinds)} AND cc.ancestor IN {es._in(concepts)}{{where}}
              ORDER BY e.patient_idx, cc.ancestor, e.event_id"""
    if idxs is None:
        rows = store.query(sql.format(source='events e', where=''), kinds + concepts)
    else:
        try:
            es._temp(store, '_m_gene', [('idx', 'INTEGER')], sorted(idxs))
            rows = store.query(sql.format(source='_m_gene g JOIN events e ON e.patient_idx = g.idx',
                                          where=' AND e.patient_idx BETWEEN ? AND ?'),
                               kinds + concepts + [min(idxs), max(idxs)])
        finally:
            es._drop(store, '_m_gene')
    return findings_of([(idx, c, eid, lo, hi, kind, json.loads(text)) for idx, c, eid, lo, hi, kind, text in rows])


def _gene_sources(store, request):
    """{gene tag: the sources holding an event of the label's kinds of its gene (through the closure)}: who records a
    gene step, for the coverage rule (D38 review, M1). A source whose only events of the gene are letter mentions does
    not record a gene step, which never binds a mention."""
    kinds = list(label_kinds(store))
    out = {}
    for name, spec in request.derived.items():
        if spec['kind'] != 'gene':
            continue
        out[name] = {r[0] for r in store.query(f"""SELECT DISTINCT e.source FROM events e
            JOIN event_concepts ec ON ec.bucket = e.bucket AND ec.patient_idx = e.patient_idx AND ec.event_id = e.event_id
            JOIN concept_closure cc ON cc.concept = ec.concept
            WHERE e.kind IN {es._in(kinds)} AND cc.ancestor = ?""", kinds + [spec['concept']])}
    return out


def _only(findings, idxs):
    """The entries of all-patient findings for these patients."""
    return {idx: findings[idx] for idx in idxs if idx in findings}


# The request.

class Request(rc.Request):
    """`store_conditions_relative.Request` (profile 1.2) with gene steps (D38). A gene step's entry is exactly
    `{gene: GENE_SPEC}`; its concept becomes the derived tag `derived-step:<step>/own`, whose spec is
    {kind: 'gene', concept: the gene concept, report, timed_by}; `kinds` maps it to ('gene', 'first_not_negative') and
    `genes` each gene step to its tag. The other entries are compiled exactly as a 1.2 request's.

    A joint request (D42, `GENE_CODED_PROFILE`) is the gene request without `exclude_kinds` (validated as D39's), with
    `exclude` the kinds left out and `profile` and `conditions` its own; it needs a gene step (`GENE_STEP_REQUIRED`).
    For a gene request `exclude` is empty."""

    def __init__(self, query, conditions):
        if isinstance(conditions, dict) and conditions.get('profile') == GENE_CODED_PROFILE:
            if not (set(conditions) <= {'profile', 'steps', 'relaxations', 'exclude_kinds'}
                    and {'steps', 'exclude_kinds'} <= set(conditions)):
                raise es.StoreError('INVALID_CONDITIONS', 'profile, steps, relaxations and exclude_kinds')
            kinds = cc.exclude_kinds(conditions['exclude_kinds'])
            self._compile(query, {**{k: v for k, v in conditions.items() if k != 'exclude_kinds'},
                                  'profile': GENE_PROFILE})
            if not self.genes:
                raise es.StoreError('GENE_STEP_REQUIRED', 'a joint request without a gene step is a 1.3 request')
            self.profile = GENE_CODED_PROFILE
            self.conditions = conditions
            self.exclude = kinds
            return
        self._compile(query, conditions)
        self.exclude = ()

    def _compile(self, query, conditions):
        """The gene profile's request (D38)."""
        if not (isinstance(conditions, dict) and conditions.get('profile') == GENE_PROFILE
                and set(conditions) <= {'profile', 'steps', 'relaxations'} and 'steps' in conditions):
            raise es.StoreError('INVALID_CONDITIONS', 'profile, steps and relaxations')
        tg.validate_query(query)
        steps = {s['id']: s for s in query['steps']}
        given = conditions['steps']
        if not (isinstance(given, dict) and given):
            raise es.StoreError('INVALID_CONDITIONS', 'steps')
        genes, rest = {}, {}
        for sid, raw in given.items():
            if not (isinstance(raw, dict) and 'gene' in raw):
                rest[sid] = raw
                continue
            if sid not in steps:
                raise es.StoreError('UNKNOWN_CONDITION_STEP', str(sid))
            if set(raw) != {'gene'}:
                raise es.StoreError('GENE_WITH_OTHER_CONDITION', sid)
            if raw['gene'] != GENE_SPEC:
                raise es.StoreError('INVALID_GENE', sid)
            if steps[sid].get('occurrence', 'any') != 'any':
                raise es.StoreError('OCCURRENCE_ON_GENE_STEP', sid)
            genes[sid] = dict(GENE_SPEC)
        relaxations = conditions.get('relaxations', [])
        if not isinstance(relaxations, list):
            raise es.StoreError('INVALID_CONDITIONS', 'relaxations')
        for x in list(query.get('relaxations', [])) + relaxations:
            if isinstance(x, dict) and x.get('step') in genes:
                raise es.StoreError('GENE_STEP_RELAXATION_UNSUPPORTED', str(x.get('id')))
        inner = {k: v for k, v in conditions.items() if k != 'steps'}
        inner['profile'] = rc.CONDITIONS_PROFILE_1_2
        if rest:
            super().__init__(query, {**inner, 'steps': rest})
        elif relaxations:
            raise es.StoreError('RELAXATION_STEP_NOT_CONDITIONED', str(relaxations[0].get('id')
                                                                         if isinstance(relaxations[0], dict) else ''))
        else:  # what `store_conditions_relative.Request` compiles for a request with no condition
            self.tags, self.derived, self.kinds, self.step_n = {}, {}, {}, {}
            self.query = tg.validate_query(copy.deepcopy(query))
            per_step = es._step_concepts(self.query)
            self.load = {sid: list(dict.fromkeys(cs)) for sid, cs in per_step.items()}
            self.base = {sid: list(cs) for sid, cs in self.load.items()}
            self.used, self.derived_used = {}, {}
        compiled = copy.deepcopy(self.query)
        self.genes = {}
        for step in compiled['steps']:
            sid = step['id']
            if sid not in genes:
                continue
            name = f'{es._DERIVED_PREFIX}{sid}/own'
            if name in self.derived:
                raise es.StoreError('DERIVED_TAG_NAME_COLLISION', name)
            spec = {'kind': 'gene', 'concept': step['concept'], **genes[sid]}
            self.derived[name] = spec
            self.kinds[sid] = ('gene', 'first_not_negative')
            self.step_n[name] = 1
            self.genes[sid] = name
            step['concept'] = name
            self.load[sid] = [spec['concept']]
            self.base[sid] = [spec['concept']]
            self.derived_used[name] = spec
        self.query = tg.validate_query(compiled)
        self.profile = GENE_PROFILE
        self.conditions = conditions

    def describe_derived(self):
        """`store_conditions_relative.Request.describe_derived`, with each gene step named and the gene rules."""
        out = super().describe_derived()
        for sid, kind in self.kinds.items():
            if kind[0] == 'gene':
                out['steps'][sid] = {'gene': kind[1]}
        out['gene_rules'] = GENE_RULES
        return out

    def describe_excluded(self):
        """The kinds a joint request leaves out, for the match output (D42)."""
        return {'kinds': list(self.exclude), 'rule': GENE_CODED_RULE}

    def without_genes(self):
        """A shallow copy whose `derived_used` leaves out the gene tags, for the derivation of the other tags."""
        view = copy.copy(self)
        view.derived_used = {name: spec for name, spec in self.derived_used.items() if spec['kind'] != 'gene'}
        return view


# Per patient.

def derive_patient(request, pid, window, events, mentions, readings=None, findings=None):
    """`store_conditions_relative.derive_patient` for the request's other derived tags, and, for each gene tag, one
    derived point per finding of its gene: id `<tag>@<event id>`, start `[t, t]`, in both passes. `findings` is the
    patient's entry of `_gene_findings`. Raises `DERIVED_EVENT_ID_TAKEN` when an event carries a point's id."""
    out = rc.derive_patient(request.without_genes(), pid, window, events, mentions, readings)
    ids = {e['id'] for e in events}
    points = []
    for name, spec in request.derived_used.items():
        if spec['kind'] != 'gene':
            continue
        out.families[name] = set()  # a gene point has its own variable, and is no recorded event
        found = ((findings or {}).get(spec['concept']) or {}).get('found', {})
        for eid in sorted(found):
            point = f'{name}@{eid}'
            if point in ids:
                raise es.StoreError('DERIVED_EVENT_ID_TAKEN', f'{pid}/{point}')
            points.append({'id': point, 'concepts': [name], 'start': list(found[eid])})
    if points:
        out.first = {**out.first, 'events': out.first['events'] + points}
        if out.second is not None:
            out.second = {**out.second, 'events': out.second['events'] + points}
    return out


# The store: candidates, pruning, chunks, reach, context and `match`.

def _candidates(store, pq, request, findings=None):
    """`store_conditions_relative._candidates`, a gene tag's patients being those with a finding of its gene
    (`findings`: the all-patient findings, read here when not given)."""
    query = pq.query
    per_step = es._step_concepts(query)
    base = request.base
    es._known(store, sorted({c for cs in base.values() for c in cs}))
    es._visible_to_exclusions(store, sorted({c for cs in base.values() for c in cs}))
    es._check_tag_names(store, request)
    tags, derived = request.tags, request.derived
    gene = findings
    plan = []
    for step, concepts in per_step.items():
        plain = [c for c in concepts if c not in tags and c not in derived]
        patients = {r[0] for r in store.query(
            f'SELECT DISTINCT patient_idx FROM concept_postings WHERE concept IN {es._in(plain)}', plain)} if plain else set()
        for c in concepts:
            if c in tags:
                patients |= es._tag_patients(store, tags[c])
            elif c in derived and derived[c]['kind'] == 'gene':
                if gene is None:
                    gene = _gene_findings(store, request)
                patients |= {idx for idx, by in gene.items() if (by.get(derived[c]['concept']) or {}).get('found')}
            elif c in derived:
                patients |= rc._derived_patients(store, c, request)
        plan.append({'step': step, 'concepts': concepts, 'patients': patients})
    plan.sort(key=lambda s: (len(s['patients']), s['step']))
    survivors = None
    for s in plan:
        survivors = set(s['patients']) if survivors is None else survivors & s['patients']
        if not survivors:
            break
    found = {'patient_idx': sorted(survivors or ()),
             'plan': [{'step': s['step'], 'concepts': s['concepts'], 'patients': len(s['patients'])} for s in plan]}
    # D42: a joint request's survivors refined to kept events (D39's hook; a gene tag needs nothing more).
    return cc.refined(store, found, request) if request.exclude else found


def candidates(store, query, conditions=None):
    """`store_conditions_relative.candidates`, for a gene or joint request too; any other request is that module's own
    call."""
    if not is_gene_request(conditions):
        return rc.candidates(store, query, conditions)
    request = Request(query, conditions)
    es._require_series(store, request)
    report_kinds(store, request)
    return _candidates(store, tg.PreparedQuery(request.query), request)


def _prune(store, pq, live, windows_by_idx, request, slice_size=None, tags=None, derived=None, findings=None):
    """`execution_store._prune`, with a gene tag's summaries: the range of the patient's finding times, a point ending
    when it starts. A gene tag's events are exactly its points, so the argument of `execution_store._impossible` holds
    for it unchanged; a patient with no finding has no summary, as a concept without a posting."""
    tags = tags or {}
    derived = derived or {}
    if not live:
        return set()
    if any(a >= b for a, b in zip(live, live[1:])):
        raise es.StoreError('PRUNE_CANDIDATES_NOT_SORTED', f'{len(live)} candidates')
    plan = es._prune_plan(pq)
    concepts = sorted({c for steps, _, _ in plan for c, _, _ in steps})
    ends = sorted({(c, e) for _, _, gaps in plan for x, xe, y, ye, _, _ in gaps for c, e in ((x, xe), (y, ye))})
    columns = 'p.patient_idx, p.min_start_lo, p.max_start_hi' + (', p.max_end_hi' if ends else '')
    slice_size = es.CHUNK_PATIENTS * es.SET_CHUNK_FACTOR if slice_size is None else slice_size
    pruned = set()
    for k in range(0, len(live), slice_size):
        part = live[k:k + slice_size]
        summaries = {}
        gene = None
        try:
            es._temp(store, '_m_live', [('idx', 'INTEGER')], part)

            def fetch(concept):
                if concept in summaries:
                    return summaries[concept]
                if concept in tags:
                    summaries[concept] = {idx: row if ends else row[:2]
                                          for idx, row in es._tag_summaries(store, tags[concept], part).items()}
                    return summaries[concept]
                cursor = store.db.execute(f"""SELECT {columns}
                    FROM concept_postings p JOIN _m_live l ON l.idx = p.patient_idx
                    WHERE p.concept = ? AND p.patient_idx BETWEEN ? AND ?""", [concept, part[0], part[-1]])
                mine = summaries[concept] = {}
                while True:
                    rows = cursor.fetchmany(store.fetch_rows)
                    if not rows:
                        break
                    mine.update({row[0]: row[1:] for row in rows})
                return mine
            for concept in concepts:
                if concept in derived and derived[concept]['kind'] == 'gene':  # D38
                    if gene is None:
                        gene = _gene_findings(store, request, part) if findings is None else _only(findings, part)
                    rows = {}
                    for idx, by in gene.items():
                        found = list(((by.get(derived[concept]['concept']) or {}).get('found') or {}).values())
                        if found:
                            lo, hi = min(f[0] for f in found), max(f[1] for f in found)
                            rows[idx] = (lo, hi, hi) if ends else (lo, hi)
                    summaries[concept] = rows
                elif concept in derived:
                    spec = derived[concept]
                    rows = fetch(spec['concept'])
                    if spec['kind'] == 'nth' and ends:  # a point ends when it starts
                        rows = {idx: (row[0], row[1], row[1]) for idx, row in rows.items()}
                    summaries[concept] = rows
                else:
                    fetch(concept)
        finally:
            es._drop(store, '_m_live')
        values = {(c, e): {idx: row[e] for idx, row in summaries[c].items()} for c, e in ends}
        if ends:
            summaries = {c: {idx: row[:2] for idx, row in rows.items()} for c, rows in summaries.items()}
        specs = [([(summaries[c], lows, highs) for c, lows, highs in steps],
                  [(summaries[x], summaries[y], lo, hi) for x, y, lo, hi in pairs],
                  [(values[x, xe], values[y, ye], k, sense) for x, xe, y, ye, k, sense in gaps])
                 for steps, pairs, gaps in plan]
        pruned.update(idx for idx in part if all(es._impossible(spec, idx, windows_by_idx[idx]) for spec in specs))
    return pruned


def _derived_chunk(store, request, idxs, pids, windows, linked, findings=None):
    """`store_conditions_relative._derived_chunk`, with the candidates' gene findings (read here when not given); a
    joint request's loads drop the left-out events and their series rows (D39's hook, `store_conditions_coded.
    derived_inputs`; D42). Findings are read apart, from the label's kinds, which are never left out."""
    kinds = request.exclude if _D42_DEFECT != 'mention_read' else ()
    events, mentions, readings = cc.derived_inputs(store, request, idxs, linked, kinds)
    if findings is None:
        findings = _gene_findings(store, request, idxs) if request.genes else {}
    return [(idx, derive_patient(request, pid, w, events.get(idx, []), mentions.get(idx, set()), readings.get(idx),
                                 findings.get(idx)))
            for idx, pid, w in zip(idxs, pids, windows)]


def _match_derived_chunk(store, pq, request, idxs, pids, windows, linked, findings=None):
    """[(patient_idx, result)] for one chunk (D32's two-pass rule, `store_conditions_relative.match_derived`)."""
    return [(idx, rc.match_derived(pq, request, derived))
            for idx, derived in _derived_chunk(store, request, idxs, pids, windows, linked, findings)]


def _derived_reach(request, query, touched, windows_by_idx):
    """`store_conditions_relative._derived_reach`; a gene step's reach is its scope, as a relative step's."""
    for idx, (admitted, every) in touched.items():
        w = windows_by_idx[idx]
        for sid, (kind, detail) in request.kinds.items():
            if sid not in every or kind in ('relative', 'gene'):
                continue
            if kind == 'nth':
                if es._D32_DEFECT != 'scoped_reach':
                    admitted[sid] = every[sid]
            elif detail != 'window':
                admitted[sid] = every[sid]
            else:
                hits = [(lo, hi) for lo, hi in every[sid] if lo <= w['end'] and hi >= w['start']]
                if hits:
                    admitted[sid] = hits
                else:
                    admitted.pop(sid, None)


def apply_undetermined(request, query, result, findings, window):
    """One engine patient's result with its undetermined gene records applied as the gene steps' excluded records
    (`execution_store.apply_exclusions`; a gene step is `any`, so no binding is displaced): the small cases' form of
    `_undetermined_reach` and the excluded-records rule, for a patient with no other excluded record."""
    if _D38_DEFECT == 'undetermined_ignored' or window is None:
        return result
    touched = {}
    _undetermined_reach(None, request, query, touched, {0}, {0: window}, {0: findings or {}})
    if 0 not in touched:
        return result
    hit, every = touched[0]
    return es.apply_exclusions(query, result, hit, {}, every, None)


def _undetermined_reach(store, request, query, touched, decided, windows_by_idx, findings=None):
    """Adds, in place, each decided patient's undetermined gene records to its gene steps' excluded starts (`GENE_RULES`
    'exclusions'): every one to the step's starts, those the scope admits to its admitted starts, as
    `execution_store._excluded_starts` lists a snapshot's excluded records."""
    if not request.genes or not decided or _D38_DEFECT == 'undetermined_ignored':
        return
    scope = query.get('scope', 'any')
    for idx, by in (_gene_findings(store, request) if findings is None else findings).items():
        if idx not in decided:
            continue
        w = windows_by_idx[idx]
        admitted, every = touched.get(idx, ({}, {}))
        changed = False
        for sid, name in request.genes.items():
            starts = (by.get(request.derived[name]['concept']) or {}).get('undetermined')
            if not starts:
                continue
            every[sid] = sorted(set(every.get(sid, [])) | {tuple(s) for s in starts})
            hits = [(lo, hi) for lo, hi in every[sid] if es.admits_start(scope, w, lo, hi)]
            if hits:
                admitted[sid] = hits
            changed = True
        if changed:
            touched[idx] = (admitted, every)


def match_context(store, query, windows, conditions=None):
    """`store_conditions_relative.match_context`; for a gene request, the derived, relative and gene rules' digests and
    the code of this module, the relative module and the coded module (whose hooks it runs since D42) beside the
    engine's artifacts; for a joint request, also the joint rule's digest."""
    if not is_gene_request(conditions):
        return rc.match_context(store, query, windows, conditions)
    context, _ = es.match_context(store, query, windows, conditions)
    context['artifacts'] = {**context['artifacts'],
                            rc.MODULE_PATH: hashlib.sha256((ROOT / rc.MODULE_PATH).read_bytes()).hexdigest(),
                            MODULE_PATH: hashlib.sha256((ROOT / MODULE_PATH).read_bytes()).hexdigest(),
                            cc.MODULE_PATH: hashlib.sha256((ROOT / cc.MODULE_PATH).read_bytes()).hexdigest()}
    context.update(derived_rules=es._digest(es.DERIVED_RULES), relative_rules=es._digest(rc.RELATIVE_RULES),
                   gene_rules=es._digest(GENE_RULES))
    if conditions.get('profile') == GENE_CODED_PROFILE:
        context['exclude_rule'] = es._digest(GENE_CODED_RULE)
    return context, es._digest(context)


_W = {}  # per worker process: the prepared gene request it last ran (the store is `execution_store._W['store']`)


def _worker_unit(unit):
    """`store_conditions_relative._worker_unit` for a gene request, in the store's own worker processes."""
    key, query, conditions, idxs, pids, windows, linked, findings, fault = unit
    try:
        if fault == 'exit':
            os._exit(3)
        if fault == 'raise':
            raise RuntimeError('injected failure')
        if _W.get('key') != key:
            request = Request(query, conditions)
            _W.update(key=key, pq=tg.PreparedQuery(request.query), request=request)
        return ('ok', _match_derived_chunk(es._W['store'], _W['pq'], _W['request'], idxs, pids, windows, linked,
                                           findings), es._peak_rss())
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc)[:300])


def _run(pool, units):
    """`store_conditions_relative._run`, each unit run by `_worker_unit` above in the pool's own workers."""
    pool.warm()
    futures = []
    try:
        for unit in units:
            futures.append(pool._pool().submit(_worker_unit, (*unit, pool.fault)))
    except Exception as exc:
        pool.close()
        raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
    out = []
    try:
        for future in futures:
            try:
                reply = future.result()
            except Exception as exc:  # BrokenProcessPool: a worker died
                pool.close()
                raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
            if reply[0] != 'ok':
                raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{reply[1]}: {reply[2]}')
            pool.worker_peak_rss = max(pool.worker_peak_rss, reply[2])
            out.append(reply[1])
    finally:
        for future in futures:
            future.cancel()
    return out


def match(store, query, windows, detail='full', chunk=es.CHUNK_PATIENTS, workers=1, conditions=None):
    """`store_conditions_relative.match`, for a gene request too (D38): gene points derived per patient from the
    findings, matched by the two-pass rule, undetermined records applied as excluded records of the gene steps; the
    output's `derived_steps` adds `gene_rules` and the context their digest and this module's. Every other request is
    `store_conditions_relative.match`, called as it is, so its answer is byte-identical. The code below is that
    function's for a request with derived steps (a gene request always has one), with this module's request,
    candidates, pruning, derivation, reach, context and worker units.

    A joint request (D42) runs the same code with `request.exclude` its kinds: D39's hooks drop the left-out events from
    the loads, refine the candidates and read coverage from kept events (`GENE_CODED_RULE`); the output adds
    `excluded_kinds`. For a gene request `exclude` is empty and the hooks are the store's own reads."""
    if not is_gene_request(conditions):
        return rc.match(store, query, windows, detail, chunk, workers, conditions)
    if not (type(workers) is int and workers >= 1):
        raise es.StoreError('INVALID_WORKERS', repr(workers))
    if detail not in es.DETAILS:
        raise es.StoreError('INVALID_DETAIL', repr(detail))
    request = Request(query, conditions)
    tags = request.used
    es._require_series(store, request)
    report_kinds(store, request)
    pq = tg.PreparedQuery(request.query)
    given, query = query, pq.query
    patients = store.query('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')
    linked = {idx for idx, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')}
    by_id = {pid: idx for idx, pid in patients}
    unknown = sorted(set(windows) - set(by_id))
    if unknown:
        raise es.StoreError('UNKNOWN_WINDOW_PATIENT', ', '.join(unknown[:5]))
    windows_by_idx = {}
    for idx, pid in patients:
        w = windows.get(pid)
        if w is not None:
            es._check_window(pid, w)
        windows_by_idx[idx] = w
    pid_of = dict(patients)
    findings = _gene_findings(store, request)  # read once, used by candidates, pruning, chunks and D18
    found = _candidates(store, pq, request, findings)
    per_step = request.load
    base = request.base
    live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
    results = {}
    parts = [live[k:k + chunk] for k in range(0, len(live), chunk)]
    if es._PRUNE:
        pruned = _prune(store, pq, live, windows_by_idx, request, tags=tags, derived=request.derived_used,
                        findings=findings)
        for idx in pruned:
            results[idx] = es._NONE(pid_of[idx])
        parts = [kept for part in parts if (kept := [i for i in part if i not in pruned])]
    if workers > 1 and parts:
        key = es._digest([given, conditions])
        units = [(key, given, conditions, part, [pid_of[i] for i in part], [windows_by_idx[i] for i in part],
                  [i for i in part if i in linked], _only(findings, part)) for part in parts]
        for unit in _run(es.get_pool(store, workers), units):
            results.update(unit)
    else:
        for part in parts:
            results.update(_match_derived_chunk(store, pq, request, part, [pid_of[i] for i in part],
                                                [windows_by_idx[i] for i in part], [i for i in part if i in linked],
                                                findings))
    filtered = [idx for idx, _ in patients if windows_by_idx[idx] is not None and idx not in results]
    filtered_result = lambda idx: {'patient_id': pid_of[idx], 'status': 'none', 'reason': 'FILTERED', 'complete': True}
    nones = sorted(filtered + [idx for idx, r in results.items() if r['status'] == 'none'])
    # D38 review, M1: a gene step is recorded by the sources holding its gene's reports, not by letters mentioning it.
    # D42: under a joint request the other steps are recorded by the sources of their kept events (D39's hook).
    coverage_steps = {sid: [request.genes[sid]] if sid in request.genes else cs for sid, cs in per_step.items()}
    kinds, recorders = request.exclude, _gene_sources(store, request)
    if _D42_DEFECT == 'coverage_letters' and request.exclude:
        coverage_steps, kinds, recorders = per_step, (), None
    for idx, reason, missing, unrecorded in cc._comparability(store, coverage_steps, nones, windows_by_idx, kinds,
                                                              chunk * es.SET_CHUNK_FACTOR, tags, recorders):
        results[idx] = {'patient_id': pid_of[idx], 'status': 'not_comparable', 'reason': reason,
                        'uncovered_steps': missing}
        if unrecorded:
            results[idx]['unrecorded_steps'] = unrecorded
    decided = {idx for idx, w in windows_by_idx.items()
               if w is not None and (results[idx]['status'] in es.DECIDED if idx in results else True)}
    touched = es._excluded_starts(store, query, base, decided, windows_by_idx)
    _undetermined_reach(store, request, query, touched, decided, windows_by_idx, findings)  # D38
    bound = [idx for idx in touched if idx in results and (
        results[idx].get('binding') or results[idx].get('relaxed_match', {}).get('binding')
        or results[idx].get('unverified_match', {}).get('binding'))]
    starts = es._bound_starts(store, bound)
    _derived_reach(request, query, touched, windows_by_idx)
    occurrence = {sid: 'first' if which == 'nth' else 'last'
                  for sid, (kind, which) in request.kinds.items() if kind == 'nth'}
    nth_bound = [idx for idx in bound if occurrence]
    for k in range(0, len(nth_bound), chunk):
        part = nth_bound[k:k + chunk]
        for idx, d in _derived_chunk(store, request, part, [pid_of[i] for i in part],
                                     [windows_by_idx[i] for i in part], [i for i in part if i in linked], findings):
            starts.setdefault(idx, {}).update(d.points)
    for idx, (hit, every) in touched.items():
        results[idx] = es.apply_exclusions(query, results[idx] if idx in results else filtered_result(idx),
                                           hit, starts.get(idx, {}), every, occurrence)
    counts = {}
    for r in results.values():
        counts[r['status']] = counts.get(r['status'], 0) + 1
    implicit_filtered = sum(1 for idx in filtered if idx not in results)
    windowless = sum(1 for w in windows_by_idx.values() if w is None)
    if implicit_filtered:
        counts['none'] = counts.get('none', 0) + implicit_filtered
    if windowless:
        counts['excluded'] = counts.get('excluded', 0) + windowless
    context, context_id = match_context(store, given, windows, conditions)
    out = {'profile': es.MATCH_PROFILE, 'engine': tg.PROFILE_ID, 'snapshot_id': store.snapshot_id,
           'verified': store.verified, 'coverage_rule': es.COVERAGE_RULE, 'exclusion_rule': es.EXCLUSION_RULE,
           'labels': store.manifest.get('labels'), 'context': context, 'context_id': context_id,
           'plan': found['plan'], 'candidates': len(found['patient_idx']), 'counts': dict(sorted(counts.items()))}
    if request.tags:
        out['value_conditions'] = request.describe()
    out['derived_steps'] = request.describe_derived()
    if request.exclude:
        out['excluded_kinds'] = request.describe_excluded()
    if detail == 'decided':
        out['detail'] = 'decided'
        out['implicit'] = {'FILTERED': implicit_filtered, 'NO_INDEX_WINDOW': windowless}
        out['results'] = [results[idx] for idx in sorted(results)]
    else:
        out['results'] = [results[idx] if idx in results else
                          filtered_result(idx) if windows_by_idx[idx] is not None else
                          {'patient_id': pid, 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'}
                          for idx, pid in patients]
    return out


# For the parity test (store_parity.py): a gene step's candidates as the engine sees them.

def gene_candidates(store, wanted, births, minute):
    """{patient: {key: [[t, t, None, None, point id, POINT_KIND], ...]}}, in minutes from birth, of the derived points
    of each key whose spec names `gene` (its key `step:<step>`), read by the module's own findings."""
    keys = {key: spec for key, spec in wanted.items() if spec.get('gene')}
    if not keys:
        return {}
    label_kinds(store)
    pids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
    out = {}
    for key, spec in keys.items():
        sid = key.split(':', 1)[1]
        name = f'{es._DERIVED_PREFIX}{sid}/own'
        probe = Request({'profile': tg.PROFILE_ID, 'steps': [{'id': sid, 'concept': spec['concept']}], 'relations': []},
                        {'profile': GENE_PROFILE, 'steps': {sid: {'gene': dict(GENE_SPEC)}}})
        for idx, by in sorted(_gene_findings(store, probe).items()):
            pid = pids[idx]
            for eid, (lo, hi) in sorted(((by.get(spec['concept']) or {}).get('found') or {}).items()):
                out.setdefault(pid, {}).setdefault(key, []).append(
                    [(lo - births[pid]) / minute, (hi - births[pid]) / minute, None, None, f'{name}@{eid}', POINT_KIND])
    return out
