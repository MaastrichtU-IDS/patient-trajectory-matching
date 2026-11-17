"""Small snapshots, seeded requests and the independent reference for joint requests (D42, profile
`execution-store-conditions-gene-coded-1.0`): gene steps under "coded only".

Shared by `test_gene_coded.py` and the ad hoc soundness runs the decision log reports.

**The reference** (`Reference`, `gene_conditions`) composes the two existing references, which share no code with the
store modules: it removes every event of an excluded kind, with its series rows, from the ingest document
(`coded_only_cases.without_kinds`, D39), then calls D38's reference (`gene_reference.gene_exhaustive_match`) with the
request read as the gene profile, without `exclude_kinds`. It knows nothing of the shared hooks, so it tests the joint
path's loaders, refinement and coverage against the definition itself (decision log, D42: "as the same request as
profile gene-1.0 would be answered on the snapshot without those events").

**The small cases** are tiny patients on an integer grid, 40 to a snapshot, answered through the dispatcher
(`store_conditions.match`) over a real snapshot. A patient is D39's (`coded_only_cases.tiny_patient`: coded events and
letter mentions of x and y, exposures, shared variables among them a mention narrowing a coded time, excluded records,
partial coverage, letters alone covering some patients) with gene-report events of G or H (a sample time and a chain of
one to three reports, classifications from both vocabularies and a missing and an unknown one, recorded at or after the
sample; D38's `gene_cases.CLASSES`), letter mentions of G, and a genetics source whose coverage is sometimes absent.
Since the review, a mention of G sometimes names the start of a genetic result or of a coded point event, and a step
that is not a gene step is sometimes on G itself. Two generators:
- `gene-coded-plain`: D39's plain request (one to three steps with any, first and last, relations, relaxations, every
  scope) with a gene step D on G (`gene_cases.add_gene_step`);
- `gene-coded-derived`: an Nth occurrence or a count (`derived_cases.tiny_request`) beside such steps, with the gene step.

**The worlds check** (`check_result`) reads the truth independently: every choice of times of the patient's kept
events, the gene step's true findings as points, in every reading of each unknown classification as negative and as not
negative (`gene_cases.check`, D38's reading). A binding naming an event that is not kept (nor a derived point) is a
violation in itself. One carve-out (D42, disclosed; the coordinator's review): for an `unresolved` result of a patient
with an undetermined gene record (`beside_undetermined`, counted in the status mix as `unresolved_beside_undetermined`),
every check runs, but the one report that the match beside it "does not reach its certainty" (`CARVED`) is dropped and
counted (`carved_out`). The excluded-records rule leaves an unresolved result as it is, so the certainty of the match
beside it is the engine's over the known records, which the record leaves open (D36's follow-up on `execution_store`'s
rule; the same answer as gene-1.0 on the snapshot without mentions).

**Planted defects** (`DEFECTS`): the D42 rules' four, each a switch of `store_conditions_gene`.
"""
from __future__ import annotations

from collections import Counter
import contextlib
import json
import random
from unittest.mock import patch

from . import coded_only_cases as co
from . import derived_cases as dc
from . import execution_store as es
from . import gene_cases as gk
from . import gene_reference as gr
from . import store_conditions as sc
from . import store_conditions_gene as gc

PROFILE = 'execution-store-conditions-gene-coded-1.0'
GENE_PROFILE = 'execution-store-conditions-gene-1.0'
GENERATORS = ('gene-coded-plain', 'gene-coded-derived')
KINDS = ['mention']
ANCESTORS = {**co.ANCESTORS, 'G': [], 'H': []}
LABELS = {'gene_report_kinds': 'genetic_result'}
# The D42 rules' planted defects: name -> (module, switch, value).
DEFECTS = {
    'mention_read': (gc, '_D42_DEFECT', 'mention_read'),  # a mention read by a step that is not a gene step
    'gene_reads_mentions': (gc, '_D38_DEFECT', 'mention_bound'),  # a gene step reading mentions
    'coverage_letters': (gc, '_D42_DEFECT', 'coverage_letters'),  # coverage counting letters
    'undetermined_ignored': (gc, '_D38_DEFECT', 'undetermined_ignored'),  # an undetermined classification ignored
}


@contextlib.contextmanager
def planted(defect):
    """The switch of one planted defect (None: none), for the duration."""
    with contextlib.ExitStack() as stack:
        if defect is not None:
            module, name, value = DEFECTS[defect]
            stack.enter_context(patch.object(module, name, value))
        yield


# The reference.

def gene_conditions(conditions):
    """A joint request's conditions as the reference reads them: the gene profile without `exclude_kinds`."""
    out = {k: v for k, v in conditions.items() if k != 'exclude_kinds'}
    out['profile'] = GENE_PROFILE
    return out


def joint(conditions):
    """A gene request's conditions as a joint request leaving letter mentions out."""
    return {**conditions, 'profile': PROFILE, 'exclude_kinds': list(KINDS)}


class Reference:
    """The independent reference over one ingest: `gene_reference.gene_exhaustive_match` on
    `coded_only_cases.without_kinds(ingest)`, with the request read by `gene_conditions`."""

    def __init__(self, ingest, kinds=KINDS):
        self.ingest = co.without_kinds(ingest, kinds)

    def match(self, query, windows, conditions):
        return gr.gene_exhaustive_match(self.ingest, query, windows, gene_conditions(conditions))


# Small snapshots.

def tiny_patient(rng, pid):
    """One tiny patient as an ingest record (see the module's docstring)."""
    p = co.tiny_patient(rng, pid)
    events = list(p['events'])
    for k in range(rng.choice((0, 1, 1, 2, 2, 3))):
        lo = rng.randint(0, 6)
        times = sorted(rng.randint(lo, lo + 4) for _ in range(rng.choice((1, 1, 2, 2, 3))))
        chain = [{'record_id': f'{pid}-g{k}r{j}', 'recorded_at': t, 'classification': rng.choice(gk.CLASSES),
                  'status': 'final' if j == 0 else 'amended'} for j, t in enumerate(times)]
        events.append({'event_id': f'g{k}', 'kind': 'genetic_result', 'source': 'genetics',
                       'concepts': [rng.choice(('G', 'G', 'G', 'H'))], 'start': [lo, lo + rng.choice((0, 0, 1))],
                       'recorded_at': times[-1], 'evidence': {'gene_reports': chain}})
    for k in range(rng.choice((0, 0, 1))):
        letter = rng.randint(2, 9)
        events.append({'event_id': f'n{k}', 'kind': 'mention', 'source': 'letters', 'concepts': ['G'],
                       'start': [max(0, letter - rng.randint(0, 4)), letter], 'evidence': {}})
    coverage = list(p['coverage'])
    if rng.random() < 0.75:
        coverage.append({'source': 'genetics', 'lo': rng.choice((0, 0, 3)), 'hi': 12, 'basis': 'declared'})
    # The review's additions (D42): a mention of G naming the start of a genetic result or of a coded point event over
    # part of its range (so it narrows that time while kept).
    mentions = [e for e in events if e['kind'] == 'mention' and 'G' in e['concepts'] and 'start_var' not in e]
    targets = [e for e in events if e['kind'] != 'mention' and 'start_var' not in e and 'end' not in e]
    if mentions and targets and rng.random() < 0.4:
        m, c = rng.choice(mentions), rng.choice(targets)
        a = rng.randint(*c['start'])
        m['start_var'], m['start'] = c['event_id'] + '.s', [a, rng.randint(a, c['start'][1])]
    return {**p, 'events': sorted(events, key=lambda e: e['event_id']),
            'coverage': sorted(coverage, key=lambda c: c['source'])}


def tiny_ingest(rng, name, patients):
    out = []
    while len(out) < patients:
        p = tiny_patient(rng, f'{name}-{len(out):03d}')
        try:
            es.validate_patient(p, ANCESTORS)
        except es.IngestError:
            continue
        out.append(p)
    return {'profile': es.INGEST_PROFILE, 'dataset_id': f'gene-coded-{name}',
            'closure': {'version': 'gene-coded-1', 'ancestors': ANCESTORS}, 'provenance': {'labels': dict(LABELS)},
            'patients': out}


def tiny_request(rng, generator):
    """(query, joint conditions) for a generator; valid for the store (refusals are tested elsewhere)."""
    while True:
        if generator == 'gene-coded-plain':
            query, _ = co.tiny_plain_request(rng)
            query, conditions = gk.add_gene_step(rng, query, None)
        else:
            query, conditions = dc.tiny_request(rng, rng.choice(dc.FEATURES), rng.random() < 0.5)
            query, conditions = gk.add_gene_step(rng, query, conditions)
        conditions = joint(conditions)
        if rng.random() < 0.2:  # the review's addition (D42): a step that is not a gene step on the gene G
            for step in query['steps']:
                if step['id'] != 'D' and step['concept'] == 'x' and rng.random() < 0.7:
                    step['concept'] = 'G'
        try:
            gc.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions


def kept_patient(p, window, kinds=KINDS):
    """(the engine patient of a tiny ingest record's kept events, concepts through the closure; {gene-report event id:
    its reports}): what the worlds check enumerates."""
    events, reports = [], {}
    for e in p['events']:
        if e['kind'] in kinds:
            continue
        concepts = sorted(set(e['concepts']) | {a for c in e['concepts'] for a in ANCESTORS[c]})
        out = {'id': e['event_id'], 'concepts': concepts, 'start': list(e['start'])}
        if 'end' in e:
            out['end'] = list(e['end'])
        out.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
        events.append(out)
        if e['kind'] == 'genetic_result':
            reports[e['event_id']] = e['evidence']['gene_reports']
    return {'patient_id': p['patient_id'], 'window': window, 'events': events}, reports


ELIGIBLE = 'unresolved_beside_undetermined'  # the status mix's count of results the carve-out may apply to
CARVED_OUT = 'carved_out'  # ... and of those whose one carved report was dropped
CARVED = 'the relaxed match beside unresolved does not reach its certainty'  # the worlds check's report it drops


def beside_undetermined(result, patient, reports):
    """Whether a result is `unresolved` for a patient with an undetermined record of the gene step's gene G. The
    excluded-records rule (`execution_store.apply_exclusions`, D18) leaves an unresolved result as it is: it already says
    it is incomplete, and the match beside it keeps the certainty the engine gave it over the known records. That
    certainty (`possible`: no binding is exact) is what the undetermined record leaves open, so for such a result the
    worlds check's report on it (`CARVED`) is dropped; every other check runs, and the reference judges the whole result
    (as `gene_cases.check` leaves a weakened `possible` to it)."""
    return result['status'] == 'unresolved' and any(
        gr.report_finding(reports[e['id']])[0] == 'undetermined'
        for e in patient['events'] if e['id'] in reports and 'G' in e['concepts'])


def check_result(result, query, conditions, patient, reports):
    """The worlds check of one store result against the patient's kept events and the gene step's true findings."""
    kept = {e['id'] for e in patient['events']}
    for m in (result, result.get('relaxed_match') or {}, result.get('unverified_match') or {}):
        foreign = sorted(eid for eid in (m.get('binding') or {}).values()
                         if eid not in kept and not eid.startswith(es._DERIVED_PREFIX))
        if foreign:
            return [f'binds events that are not kept: {foreign}']
    return gk.check(result, query, gene_conditions(conditions), patient, frozenset(), reports)


COUNT_REFINED = 'count_refined'  # in the status mix: results the dispatcher's count post-pass decided (D43b)


def refined_count(want, got):
    """Whether `got` differs from the reference's `want` only as the dispatcher's count post-pass (D43,
    `store_conditions_counts`) refines a result: the reference, which applies D32's two-pass rule and knows nothing of
    the post-pass, leaves it `unresolved` with STEP_CONDITION_UNCERTAIN. Such a result is checked by the worlds check
    (`worlds`), and against the oracle by `count_narrowing_cases`' snapshot reference."""
    return (want['status'] == 'unresolved' and want.get('reason') == 'STEP_CONDITION_UNCERTAIN'
            and json.dumps(got) != json.dumps(want))


def run_snapshot(root, seed, generator, patients=40, requests=25, defect=None, worlds=True, reference=True,
                 pruning=True, itself=False):
    """One small snapshot of `patients` tiny patients and `requests` seeded joint requests over it, each with its own
    windows: (violations, mismatches, status mix, cases). A case is one patient's result for one request. Each result is
    checked by the worlds check (`worlds`) and compared with the reference (`reference`; a result the dispatcher's count
    post-pass decided where the reference leaves it STEP_CONDITION_UNCERTAIN is counted as `count_refined` and left to
    the worlds check, D43b); the whole answer is compared
    with pruning off (`pruning`) and, with `itself`, with the gene profile's answer on the snapshot built without the
    left-out kinds (results and candidates)."""
    rng = random.Random(f'gene-coded/{generator}/{seed}')
    ingest = tiny_ingest(rng, f'{generator}-{seed}', patients)
    by_id = {p['patient_id']: p for p in ingest['patients']}
    violations, mismatches, mix, cases = [], [], Counter(), 0
    reference_of = Reference(ingest) if reference else None
    with contextlib.ExitStack() as stack:
        store = stack.enter_context(es.open_store(es.build(ingest, root / f'{generator}-{seed}')))
        bare = (stack.enter_context(es.open_store(es.build(co.without_kinds(ingest, KINDS),
                                                           root / f'{generator}-{seed}-bare'))) if itself else None)
        stack.enter_context(planted(defect))
        for k in range(requests):
            query, conditions = tiny_request(rng, generator)
            windows = co.tiny_windows(rng, ingest)
            out = sc.match(store, query, windows, conditions=conditions)
            if pruning:
                with patch.object(es, '_PRUNE', False):
                    off = sc.match(store, query, windows, conditions=conditions)
                if json.dumps(off) != json.dumps(out):
                    mismatches.append((seed, k, 'pruning'))
            if itself:
                plain = sc.match(bare, query, windows, conditions=gene_conditions(conditions))
                if (json.dumps(plain['results']) != json.dumps(out['results'])
                        or sc.candidates(bare, query, gene_conditions(conditions))['patient_idx']
                        != sc.candidates(store, query, conditions)['patient_idx']):
                    mismatches.append((seed, k, 'itself'))
            want = reference_of.match(query, windows, conditions) if reference else None
            for i, got in enumerate(out['results']):
                cases += 1
                mix[got['status']] += 1
                pid = got['patient_id']
                if worlds:
                    patient, reports = kept_patient(by_id[pid], windows[pid])
                    wrong = check_result(got, query, conditions, patient, reports)
                    if beside_undetermined(got, patient, reports):
                        mix[ELIGIBLE] += 1
                        kept = [w for w in wrong if not w.startswith(CARVED)]
                        if len(kept) < len(wrong):
                            mix[CARVED_OUT] += 1
                        wrong = kept
                    if wrong:
                        violations.append((seed, k, pid, wrong))
                if reference and json.dumps(co.unfiltered(got)) != json.dumps(want[i]):
                    if refined_count(want[i], got):
                        mix[COUNT_REFINED] += 1  # D43b: decided by the dispatcher's post-pass, checked by the worlds
                    else:
                        mismatches.append((seed, k, pid))
    return violations, mismatches, mix, cases


def run_small(root, generator, snapshots, first=0, **options):
    """`run_snapshot` over `snapshots` seeds from `first`: (violations, mismatches, status mix, cases)."""
    violations, mismatches, mix, cases = [], [], Counter(), 0
    for seed in range(first, first + snapshots):
        v, m, x, n = run_snapshot(root, seed, generator, **options)
        violations += v
        mismatches += m
        mix.update(x)
        cases += n
    return violations, mismatches, mix, cases
