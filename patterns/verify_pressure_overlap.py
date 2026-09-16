"""Reproduce separately reviewed demo queries and compare aggregate membership overlap."""
import argparse
from collections import Counter
from copy import deepcopy
from itertools import combinations
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import zlib
from . import reviewed_source_query as r, verify_indexed_source_windows as windows

p = r.p
NAMES = ('arterial', 'noninvasive', 'art')
LEVELS = ('patients', 'stays', 'segments')
PROFILE = 'reviewed-pressure-overlap-1.0'
FILES = tuple(sorted(set(r.FILES + ('patterns/verify_pressure_overlap.py', 'patterns/verify_indexed_source_windows.py',
    'data/clinical-source-demo-pin.json'))))


def read(path):
    return json.loads((p.ei.ROOT/path).read_text())


def _snapshot(report, result):
    """Internal handoff from fresh reviewed execution; no external result-file input."""
    p.ei.require(report['status'] == result['status'] == 'COMPLETED_PARTITIONED_QUERY' and
        report['search_complete_over_selected_records'] and result['search_complete_over_selected_records'], 'INCOMPLETE_STRATUM')
    p.ei.require(result['context_id'] == p.cr.digest(result['context']) == report['execution_context_id'], 'STRATUM_CONTEXT_MISMATCH')
    p.ei.require(report['source_selection_summary'] == result['source_selection_summary'], 'STRATUM_SOURCE_MISMATCH')
    p.ei.require(report['request'] == result['context']['plan_context']['selection_context']['request'], 'STRATUM_REQUEST_MISMATCH')
    anchors = result['anchors']; roster = result['roster']; batches = result['batches']
    p.ei.require(all(b['status'] == 'COMPLETED_BATCH' for b in batches), 'INCOMPLETE_STRATUM_BATCHES')
    p.ei.require(report['batch_outcomes'] == dict(Counter(b['status'] for b in batches)), 'STRATUM_BATCH_ACCOUNTING')
    p.ei.require(report['anchor_outcomes'] == dict(Counter(a['status'] for a in anchors)) and
        report['anchors_verified_against_unpartitioned_sql'] == len(anchors), 'STRATUM_ANCHOR_ACCOUNTING')
    p.ei.require(report['stay_outcomes'] == dict(Counter(s['status'] for s in roster)), 'STRATUM_STAY_ACCOUNTING')
    stays = {(s['patient_id'], s['episode_id']) for s in roster}
    segments = {(a['patient_id'], a['episode_id'], a['anchor_id']) for a in anchors}
    p.ei.require(len(stays) == len(roster) and len(segments) == len(anchors) and
        len({a['anchor_id'] for a in anchors}) == len(anchors), 'DUPLICATE_STRATUM_POPULATION')
    p.ei.require(all(s[:2] in stays for s in segments), 'ANCHOR_OUTSIDE_STAY_ROSTER')
    for stay in roster:
        own = [a for a in anchors if (a['patient_id'], a['episode_id']) == (stay['patient_id'], stay['episode_id'])]
        p.ei.require(sorted(stay['anchor_ids']) == sorted(a['anchor_id'] for a in own), 'STRATUM_STAY_ANCHORS')
        expected = ('NO_ADMITTED_ANCHOR' if not own else 'VERIFIED_RECORD_MATCH' if any(a['bindings'] for a in own)
                    else 'VERIFIED_NO_SELECTED_RECORD_MATCH')
        p.ei.require(stay['status'] == expected, 'STRATUM_STAY_STATUS')
    for anchor in anchors:
        p.ei.require(anchor['status'] == ('VERIFIED_RECORD_MATCH' if anchor['bindings'] else 'VERIFIED_NO_SELECTED_RECORD_MATCH')
            and anchor['bindings'] == anchor['reference_bindings'], 'UNVERIFIED_STRATUM_ANCHOR')
        p.ei.require(all(len(b) == 7 and tuple(b[:2]) == (anchor['patient_id'], anchor['episode_id']) for b in anchor['bindings']),
            'BINDING_OUTSIDE_ANCHOR_SCOPE')
    flattened = [row for anchor in anchors for row in anchor['bindings']]
    p.ei.require(result['bindings'] == flattened, 'STRATUM_BINDING_ACCOUNTING')
    matched_segments = {(a['patient_id'], a['episode_id'], a['anchor_id']) for a in anchors if a['bindings']}
    matched_stays = {s[:2] for s in matched_segments}; matched_patients = {(s[0],) for s in matched_segments}
    p.ei.require(result['certain_patient_ids'] == result['possible_patient_ids'] == sorted(x[0] for x in matched_patients),
        'STRATUM_PATIENT_ACCOUNTING')
    p.ei.require(report['metrics']['matched_patients'] == len(matched_patients), 'STRATUM_PATIENT_COUNT')
    return {'population': {'patients': {(s[0],) for s in stays}, 'stays': stays, 'segments': segments},
        'memberships': {'patients': matched_patients, 'stays': matched_stays, 'segments': matched_segments}}


def _counts(population, memberships):
    """Eight disjoint membership cells, including no selected-record match in any stratum."""
    p.ei.require(set(memberships) == set(NAMES) and all(s <= population for s in memberships.values()), 'MEMBERSHIP_OUTSIDE_POPULATION')
    cells = []
    for mask in range(8):
        selected = [name for i, name in enumerate(NAMES) if mask & (1 << i)]
        count = sum(all((item in memberships[name]) == (name in selected) for name in NAMES) for item in population)
        cells.append({'strata': selected, 'count': count})
    return {'population': len(population), 'matched_by_stratum': {n: len(memberships[n]) for n in NAMES},
        'matched_in_any_separate_stratum': len(population)-cells[0]['count'], 'matched_in_none': cells[0]['count'],
        'matched_in_all_three': cells[-1]['count'], 'exclusive_membership_cells': cells,
        'pairwise_intersections': [{'strata': [a,b], 'count': len(memberships[a] & memberships[b])} for a,b in combinations(NAMES,2)]}


def compare(executions):
    """Internal fresh (aggregate report, membership snapshot) pairs, keyed by the three strata."""
    p.ei.require(set(executions) == set(NAMES), 'MISSING_OR_EXTRA_STRATUM')
    common_request = common_files = common_population = None
    proofs = {}; observed = {}
    for name in NAMES:
        report, snapshot = executions[name]
        p.ei.require(report['status'] == 'COMPLETED_PARTITIONED_QUERY' and report['search_complete_over_selected_records'], 'INCOMPLETE_STRATUM')
        request = deepcopy(report['request']); request.pop('id')
        p.windows.validate(report['request'])
        baseline_items = request['query']['baseline'].pop('item_ids'); followup_items = request['query']['followup'].pop('item_ids')
        p.ei.require(len(baseline_items) == 1 and baseline_items == followup_items, 'MIXED_MEASUREMENT_ITEMS')
        observed[name] = baseline_items[0]
        files = report['source_selection_summary']['context']['source_files']
        if common_request is None:
            common_request, common_files, common_population = request, files, snapshot['population']
        p.ei.require(request == common_request, 'INCOMPARABLE_STRATUM_REQUESTS')
        p.ei.require(files == common_files, 'INCOMPARABLE_STRATUM_SOURCES')
        p.ei.require(snapshot['population'] == common_population, 'INCOMPARABLE_STRATUM_POPULATIONS')
        serialized = {kind: {level: sorted(snapshot[kind][level]) for level in LEVELS} for kind in ('population','memberships')}
        proofs[name] = {'query_report_sha256': p.cr.digest(report), 'execution_context_id': report['execution_context_id'],
            'membership_snapshot_sha256': p.cr.digest(serialized)}
    p.ei.require(len(set(observed.values())) == len(NAMES), 'REPEATED_MEASUREMENT_STRATUM')
    counts = {level: _counts(common_population[level], {n: executions[n][1]['memberships'][level] for n in NAMES}) for level in LEVELS}
    context = {'profile': PROFILE, 'stratum_items': observed, 'common_request_without_item': common_request,
        'source_files': common_files, 'strata': proofs, 'counts': counts,
        'artifacts': {f: p.ei.digest((p.ei.ROOT/f).read_text()) for f in FILES}}
    return {'profile': PROFILE, 'context': context, 'context_id': p.cr.digest(context), 'status': 'VERIFIED_SEPARATE_COHORT_OVERLAP',
        'all_three_queries_reproduced': True, 'pool_measurements': False, 'pooled_query_executed': False,
        'patient_rows_or_identifiers_included': False, 'clinical_mapping_verified': False,
        'causal_effect_estimated': False, 'full_mixed_owl_reasoning_verified': False,
        'interpretation': 'Set overlap of separately executed record queries at patient, stay and segment levels; no pooled measurement query or clinical effect estimate.'}


def _execute_stratum(folder, name):
    p.ei.require(name in NAMES, 'UNKNOWN_STRATUM')
    request = read(f'examples/indexed-source-windows/demo-{name}-request.json')
    declaration = read(f'data/{name}-source-fidelity-review.json')
    report, _, result = r.run(folder, request, declaration)
    windows.validate_summary(report['source_selection_summary'], 'demo-'+name)
    p.ei.require(report == read(f'verification/reviewed-{name}-demo-report.json'), 'DEMO_STRATUM_NOT_REPRODUCED:'+name)
    return report, _snapshot(report, result)


def verify(folder):
    return compare({name: _execute_stratum(folder, name) for name in NAMES})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=p.ei.ROOT/'verification/reviewed-source-query-run/pressure-overlap.json')
    args = parser.parse_args(argv); temporary = None
    try:
        protected = {*p.windows.inputs.source_paths(args.input_dir).values(), *p.windows.measurements.paths_for(args.input_dir).values()}
        protected.update(p.ei.ROOT/f for f in FILES)
        for name in NAMES:
            protected.update(p.ei.ROOT/f for f in (f'examples/indexed-source-windows/demo-{name}-request.json',
                f'data/{name}-source-fidelity-review.json', f'verification/reviewed-{name}-demo-report.json'))
        p.ei.require(args.output.resolve() not in {f.resolve() for f in protected}, 'OUTPUT_OVERWRITES_INPUT')
        report = verify(args.input_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(report, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, sqlite3.Error, RecursionError) as error:
        print(json.dumps({'status':'INVALID_PRESSURE_OVERLAP_INPUT','reason':str(error)}), file=sys.stderr); return 2
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status':report['status'], 'counts':report['context']['counts']})); return 0


if __name__ == '__main__':
    raise SystemExit(main())
