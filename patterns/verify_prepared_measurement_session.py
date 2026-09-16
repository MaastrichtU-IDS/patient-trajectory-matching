"""Check prepared measurement query equivalence and eliminated preparation operations."""
from copy import deepcopy
import json
from unittest.mock import patch
from . import prepared_measurement_session as p, source_mixed_query as source

EXAMPLE = p.ei.ROOT / 'examples/prepared-measurement-session'


def load_example(folder=EXAMPLE):
    values = {path.stem: json.loads(path.read_text()) for path in folder.glob('*.json')}
    prepared = source.prepare(folder, values['source-request'])
    source.validate(values['source-review'], 'review')
    p.ei.require(values['source-review']['preparation_context_id'] == prepared['context_id'], 'STALE_SYNTHETIC_SOURCE_REVIEW')
    return values, prepared


def arguments(values, prepared, index):
    re = values['source-review']['episodes'][index]
    return [values['request'], *(values[n] for n in p.source.mappings.mappings.NAMES), values['selector'],
            prepared['interval_import']['episodes'][index]['store'], re['interval_policy'],
            prepared['interval_import']['semantic_policy'], prepared['measurement_import']['episodes'][index]['store'],
            re['measurement_policy'], re['alignment'], values['source-request']['query']]


def queries(parent):
    answer = [deepcopy(parent) for _ in range(4)]
    answer[1]['baseline']['value_lexical'] = '59'
    answer[2]['baseline']['max_before_start_us'] = 5 * 60_000_000
    answer[3]['followup']['max_after_anchor_us'] = 60 * 60_000_000
    answer[3]['followup']['within_interval'] = True
    return answer


def verify():
    values, prepared = load_example(); query_list = queries(values['source-request']['query'])
    raw = source.raw_rows(prepared, EXAMPLE); evidence = []; memberships = [set() for _ in query_list]
    warm_counts = {'source_audits': 0, 'mapping_plans': 0, 'fresh_mixed_queries': 0, 'network_compilations': 0}
    for index, me in enumerate(prepared['measurement_import']['episodes']):
        ie = prepared['interval_import']['episodes'][index]
        if me['store'] is None or ie['store'] is None: continue
        args = arguments(values, prepared, index); session = p.Session(EXAMPLE, *args)
        # Measure actual call paths after admission, not claimed cache-hit counters.
        with patch.object(p.source, 'audit_store', wraps=p.source.audit_store) as audit, \
             patch.object(p.source.mappings, 'plan', wraps=p.source.mappings.plan) as plan, \
             patch.object(p.source.mappings.mixed, 'execute', wraps=p.source.mappings.mixed.execute) as mixed, \
             patch.object(p.source.mappings.mixed, '_compile', wraps=p.source.mappings.mixed._compile) as compile_network:
            results = [session.execute(q) for q in query_list]
            for key, spy in zip(warm_counts, (audit, plan, mixed, compile_network)): warm_counts[key] += spy.call_count
        for qi, (query, result) in enumerate(zip(query_list, results)):
            reference = p.source.execute(EXAMPLE, *args[:-1], query)
            p.ei.require(result['result'] == reference, 'PREPARED_SOURCE_QUERY_DIFFERENCE')
            qr = result['result']['query_result']; memberships[qi].update(qr['certain_patient_ids'])
            re = values['source-review']['episodes'][index]
            selected_i = source.selected(ie, re['interval_policy'], prepared['interval_import']['semantic_policy'])
            selected_m = source.selected(me, re['measurement_policy'], p.source.mappings.mixed.points.SEMANTIC_POLICY)
            rows_i = source.reference_rows(ie, selected_i, raw['inputevents'], 'input_')
            rows_m = source.reference_rows(me, selected_m, raw['chartevents'], 'chart_')
            for stratum in qr['strata']:
                sql = source.reference.execute(rows_i, rows_m, '1000', stratum['query'])
                p.ei.require(source.graph_bindings(stratum['result']) == sql, 'PREPARED_SOURCE_SQL_DIFFERENCE')
            evidence.append({'synthetic_episode_index': index, 'query_index': qi,
                             'session_context_id': session.snapshot()['session_id'],
                             'prepared_result_context_id': result['context_id'],
                             'source_query_context_id': reference['context_id']})
        session.close()
    p.ei.require(all(v == 0 for v in warm_counts.values()), 'PREPARATION_REPEATED_ON_WARM_QUERY')
    p.ei.require([sorted(x) for x in memberships] == [['1','2','3'], ['1'], [], ['1','2','3']], 'PREPARED_FIXTURE_MEMBERSHIP')
    paths = sorted(EXAMPLE.glob('*'))
    return {'profile': 'prepared-measurement-session-verification-1.0', 'status': 'VERIFIED',
            'synthetic_only': True, 'clinical_mapping_verified': False, 'public_demo_mapping_executed': False,
            'artifacts': {f: p.ei.digest((p.ei.ROOT / f).read_text()) for f in sorted(set(p.FILES + source.FILES +
                         ('patterns/verify_prepared_measurement_session.py',)))},
            'fixture_sha256': {str(path.relative_to(p.ei.ROOT)): p.ei.digest(path.read_text()) for path in paths},
            'warm_queries': len(evidence), 'warm_operation_counts': warm_counts,
            'source_byte_digests_checked_per_query': True, 'all_source_query_results_equal': True,
            'all_raw_csv_sql_bindings_equal': True, 'synthetic_memberships_by_query': [sorted(x) for x in memberships],
            'queries': query_list, 'evidence': evidence,
            'scope': 'Immutable supplied stores; source admission and policy selection shared with SQL control; no latency claim'}


if __name__ == '__main__':
    report = verify()
    (p.ei.ROOT / 'verification/prepared-measurement-session-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'warm_queries': report['warm_queries'], 'warm_operation_counts': report['warm_operation_counts']}))
