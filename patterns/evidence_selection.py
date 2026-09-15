"""Explicit support-level revision selection for bounded interval source snapshots."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from . import bounded_cohort as cohort
from . import bounded_intervals as bt
from . import exact_intervals as ei

PROFILE = 'evidence-selection-1.0'
SCHEMA = ei.ROOT / 'schemas/evidence-selection.schema.json'
ROW_KINDS = ('variables', 'events', 'constraints')
EPOCH = '1970-01-01T00:00:00Z'


def timestamp(value):
    return ei.normalize(value, EPOCH)


def copied(value):
    return json.loads(ei.canonical(value))


def validate(archive, request):
    schema = json.loads(SCHEMA.read_text())
    for value, contract, label in ((archive, schema, 'ARCHIVE'),
                                    (request, schema['$defs']['request'], 'SELECTION_REQUEST')):
        ei.require(not list(Draft202012Validator(contract).iter_errors(value)), 'INVALID_' + label + '_SCHEMA')
    archive, request = copied(archive), copied(request)
    def identifiers(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('id', 'archive_id', 'dataset_id', 'source_id', 'patient_id', 'episode_id',
                           'record_id', 'clock_id', 'start_var', 'end_var', 'left_var', 'right_var',
                           'assertion_id', 'supersedes') and item is not None:
                    ei.identifier(item)
                identifiers(item)
        elif isinstance(value, list):
            for item in value:
                identifiers(item)
    identifiers(archive)
    for patient in request['patient_cutoffs']:
        ei.identifier(patient)
    for source in request['admissible_source_ids']:
        ei.identifier(source)
    assertions, entries = bt.unique(archive['assertions'], 'id'), bt.unique(archive['entries'], 'id')
    clocks = bt.unique(archive['clocks'], 'clock_id')
    published = timestamp(archive['published_at'])
    cutoffs = {p: timestamp(t) for p, t in request['patient_cutoffs'].items()}
    ei.require(set(cutoffs) == {e['patient_id'] for e in entries.values()}, 'CUTOFF_PATIENT_SCOPE')
    ei.require(all(t <= published for t in cutoffs.values()), 'CUTOFF_AFTER_ARCHIVE')
    ei.require(set(request['admissible_source_ids']) <= {e['source_id'] for e in entries.values()}, 'UNKNOWN_ADMISSIBLE_SOURCE')
    for c in clocks.values():
        timestamp(c['origin'])
        ei.require(c['scope'] == 'global' or (c['scope'].startswith('patient:')
                   and c['scope'][8:] in cutoffs), 'INVALID_CLOCK_SCOPE')
    for a in assertions.values():
        ei.require(any(a['bundle'][k] for k in ROW_KINDS), 'EMPTY_ASSERTION_BUNDLE')
        for kind in ROW_KINDS:
            bt.unique(a['bundle'][kind], 'id')
            for row in a['bundle'][kind]:
                for key, val in row.items():
                    if key.endswith('_us'):
                        ei.checked_us(val)
                if kind != 'constraints':
                    ei.require(bt.scope(row) == bt.scope(a), 'ASSERTION_SCOPE_MISMATCH')
                if kind == 'variables':
                    ei.require(row['clock_id'] in clocks, 'UNKNOWN_CLOCK')
                    ei.require(row['lower_us'] <= row['upper_us'], 'REVERSED_VARIABLE_BOUNDS')
                    ei.require(clocks[row['clock_id']]['scope'] in ('global', 'patient:' + a['patient_id']),
                               'CLOCK_PATIENT_SCOPE_MISMATCH')
    supported = set()
    for entry in entries.values():
        archived = timestamp(entry['archived_at'])
        ei.require(archived <= published, 'ENTRY_AFTER_ARCHIVE_PUBLICATION')
        if entry['source_recorded_at'] is not None:
            timestamp(entry['source_recorded_at'])
        available = entry['source_available_at']
        if available is not None:
            ei.require(timestamp(available) <= archived, 'AVAILABILITY_AFTER_ARCHIVING')
        aid = entry['assertion_id']
        if entry['operation'] == 'assert':
            ei.require(aid in assertions, 'UNKNOWN_ASSERTION')
            ei.require(bt.scope(entry) == bt.scope(assertions[aid]), 'SUPPORT_SCOPE_MISMATCH')
            supported.add(aid)
        else:
            ei.require(aid is None and entry['supersedes'] is not None, 'INVALID_WITHDRAWAL')
        parent = entry['supersedes']
        if parent is not None:
            ei.require(parent in entries and parent != entry['id'], 'INVALID_REVISION_TARGET')
            old = entries[parent]
            ei.require(old['source_id'] == entry['source_id'] and bt.scope(old) == bt.scope(entry),
                       'CROSS_SOURCE_OR_SCOPE_REVISION')
            if available is not None and old['source_available_at'] is not None:
                ei.require(timestamp(old['source_available_at']) <= timestamp(available), 'REVISION_AVAILABILITY_ORDER')
    ei.require(supported == set(assertions), 'UNSUPPORTED_ARCHIVE_ASSERTION')
    # Iterative ancestry traversal avoids recursion limits on long source histories.
    done = set()
    for start in entries:
        path, current = set(), start
        while current is not None and current not in done:
            ei.require(current not in path, 'REVISION_CYCLE')
            path.add(current)
            current = entries[current]['supersedes']
        done.update(path)
    return archive, request, assertions, entries, cutoffs


def select(archive, request):
    """Return a detached selection report; blocked selections never yield a source."""
    archive, request, assertions, entries, cutoffs = validate(archive, request)
    artifacts = ('patterns/evidence_selection.py', 'schemas/evidence-selection.schema.json',
                 'patterns/bounded_intervals.py', 'schemas/bounded-interval.schema.json',
                 'patterns/exact_intervals.py', 'patterns/pro_solid.py', 'patterns/temporal_stn.py',
                 'ontology/vendor/sulo-0.2.14.ttl', 'ontology/pro-solid-profile.ttl',
                 'ontology/exact-interval-profile.ttl', 'ontology/bounded-interval-profile.ttl',
                 'patterns/requirements.lock.txt')
    context = {'profile': PROFILE, 'archive_sha256': ei.digest(ei.canonical(archive)),
               'request': request, 'mapping_policy_id': archive['mapping_policy_id'],
               'mapping_policy_evidence': 'caller_declaration_not_verified_against_original_sources',
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in artifacts}}
    context_id = ei.digest(ei.canonical(context))
    report = {'profile': PROFILE, 'context_id': context_id, 'context': context,
              'archive': archive, 'decisions': [], 'blockers': [], 'active_support_ids': [],
              'selected_assertion_ids': [], 'row_supports': {}, 'source': None,
              'selection_complete': True, 'coverage_basis': 'caller_declared_archive_history_only',
              'replay_coverage': archive['history_coverage'].upper(),
              'later_evidence_used': False}
    blockers, decisions, eligible = report['blockers'], {}, set()
    if archive['history_coverage'] != 'complete':
        blockers.append({'reason': 'INCOMPLETE_ARCHIVE_HISTORY', 'declared': archive['history_coverage']})
    for eid, entry in sorted(entries.items()):
        available = entry['source_available_at']
        if entry['source_id'] not in request['admissible_source_ids']:
            state = 'EXCLUDED_SOURCE'
        elif available is None:
            state = 'UNKNOWN_AVAILABILITY'
            if report['replay_coverage'] != 'UNAVAILABLE':
                report['replay_coverage'] = 'PARTIAL'
            blockers.append({'reason': state, 'entry_id': eid})
        elif request['mode'] == 'source_as_known' and timestamp(available) > cutoffs[entry['patient_id']]:
            state = 'AFTER_CUTOFF'
        else:
            eligible.add(eid)
            state = 'ELIGIBLE'
            if timestamp(available) > cutoffs[entry['patient_id']]:
                report['later_evidence_used'] = True
        decisions[eid] = {'entry_id': eid, 'state': state}
    children = defaultdict(list)
    for eid in sorted(eligible):
        parent = entries[eid]['supersedes']
        if parent is not None:
            if parent not in eligible:
                blockers.append({'reason': 'INELIGIBLE_REVISION_ANCESTOR', 'entry_id': eid, 'target': parent})
            children[parent].append(eid)
    for parent, successors in sorted(children.items()):
        if len(successors) > 1:
            blockers.append({'reason': 'REVISION_FORK', 'target': parent, 'successors': successors})
    supports = defaultdict(list)
    for eid in sorted(eligible):
        if eid in children:
            decisions[eid].update(state='SUPERSEDED', superseded_by=children[eid])
        elif entries[eid]['operation'] == 'withdraw':
            decisions[eid]['state'] = 'WITHDRAWAL'
        else:
            decisions[eid]['state'] = 'ACTIVE'
            supports[entries[eid]['assertion_id']].append(eid)
    report['decisions'] = list(decisions.values())
    report['active_support_ids'] = sorted(e for ids in supports.values() for e in ids)
    report['selected_assertion_ids'] = sorted(supports)
    variants = defaultdict(dict)
    for aid, support_ids in sorted(supports.items()):
        a = assertions[aid]
        for kind in ROW_KINDS:
            for row in a['bundle'][kind]:
                key = kind + ':' + row['id']
                # Identical complete normalized rows coalesce; no approximate/value-based merge.
                encoded = ei.canonical(row)
                variant = variants[key].setdefault(encoded, {'row': row, 'support': []})
                variant['support'].append({'assertion_id': aid, 'support_ids': support_ids,
                                           'patient_id': a['patient_id'], 'episode_id': a['episode_id']})
    selected = {k: [] for k in ROW_KINDS}
    for key, choices in sorted(variants.items()):
        if len(choices) != 1:
            blockers.append({'reason': 'CONFLICTING_ROW', 'row': key, 'variants': list(choices.values())})
            continue
        item = next(iter(choices.values()))
        report['row_supports'][key] = item['support']
        selected[key.split(':')[0]].append(item['row'])
    # Constraint-only bundles must belong to the same patient/episode as their operands.
    variables = {v['id']: v for v in selected['variables']}
    for c in selected['constraints']:
        refs = [variables.get(c[side]) for side in ('left_var', 'right_var')]
        if all(v is not None for v in refs):
            for support in report['row_supports']['constraints:' + c['id']]:
                if any(bt.scope(v) != bt.scope(support) for v in refs):
                    blockers.append({'reason': 'CONSTRAINT_ASSERTION_SCOPE', 'constraint_id': c['id'],
                                     'assertion_id': support['assertion_id']})
    if blockers:
        report['status'] = 'BLOCKED_EVIDENCE'
    elif not any(selected.values()):
        report['status'] = 'EMPTY_SELECTED_EVIDENCE'
    else:
        source = {'profile': bt.PROFILE_ID, 'dataset_id': archive['dataset_id'],
                  'snapshot_id': 'selected_' + context_id, 'clocks': archive['clocks'], **selected}
        try:
            bt.compile_source(source)
        except bt.InconsistentSource as error:
            report.update(status='TEMPORAL_INCONSISTENCY', validation=error.details)
        except ei.ContractError as error:
            report.update(status='INVALID_SELECTED_SOURCE', validation={'reason': str(error)})
        else:
            report.update(status='READY', source=source)
    return report


def execute(archive, request, query):
    """Run the existing matcher only on a READY selection, retaining the full audit."""
    bt.validate(query, query=True)
    selection = select(archive, request)
    matching = None
    if selection['status'] == 'READY':
        matching = cohort.execute(bt.prepare(selection['source']), query)
    context = {'profile': 'evidence-selection-run-1.0', 'selection_context_id': selection['context_id'],
               'query': copied(query), 'matching_context_id': matching['context_id'] if matching else None,
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest()
                             for p in ('patterns/bounded_cohort.py', 'patterns/bounded_reference.py')}}
    return {'profile': context['profile'], 'context_id': ei.digest(ei.canonical(context)), 'context': context,
            'status': selection['status'], 'selection': selection, 'matching': matching}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    example = ei.ROOT / 'examples/evidence-selection'
    parser.add_argument('--archive', type=Path, default=example / 'archive.json')
    parser.add_argument('--request', type=Path, default=example / 'request.json')
    parser.add_argument('--query', type=Path, default=example / 'query.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/evidence-selection-run')
    args = parser.parse_args()
    try:
        result = execute(*(json.loads(p.read_text()) for p in (args.archive, args.request, args.query)))
    except (ei.ContractError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'context_id': result['context_id'], 'output': str(args.output)}))
    if result['status'] not in ('READY', 'EMPTY_SELECTED_EVIDENCE'):
        parser.exit(2)


if __name__ == '__main__':
    main()
