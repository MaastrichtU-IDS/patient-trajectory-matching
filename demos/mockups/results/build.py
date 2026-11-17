"""Gather the execution-store evidence for the mockups' results page.

    python demos/mockups/results/build.py           # writes demos/mockups/results/results.json
    python demos/mockups/results/build.py --check   # fails if results.json is stale

Every figure is read from a committed report in verification/; nothing is measured here.
The page shows only aggregate figures, which is all these reports contain.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'results.json'
REPORTS = {
    'scaling': 'verification/execution-store-scaling-report.json',
    'matcher': 'verification/matcher-comparison-report.json',
    'mimic': 'verification/execution-store-mimic-demo-report.json',
    'rises': 'verification/d36-rise-usefulness-report.json',
}
GIB = 1024 ** 3


def load(rel):
    data = (ROOT / rel).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def scaling(s):
    rows = []
    for z in s['sizes']:
        if z.get('status') != 'completed':
            continue
        q, v = z['queries'], z['profile']['verify_stages']
        rows.append({
            'patients': z['patients'],
            'events': z['ingest_events'],
            'build_seconds': round(z['build_seconds'], 1),
            'build_peak_gib': round(z['build_phase']['peak_rss_bytes'] / GIB, 2),
            'snapshot_gb': round(z['snapshot_bytes'] / 1e9, 2),
            'verify_content_seconds': round(v['content_seconds'], 1),
            'verify_peak_gib': round(v['process_peak_rss_bytes'] / GIB, 2),
            'open_seconds_warm': round(z['open_verify_seconds']['warm'], 2),
            'match_p50_seconds': round(q['match_seconds']['p50'], 2),
            'match_max_seconds': round(q['match_seconds']['max'], 1),
            'query_peak_gib': round(z['query_phase']['peak_rss_bytes'] / GIB, 2),
            'queries': q['queries'],
            'pruned_share_mean': round(q['pruning_rate']['mean'], 3),
            # D41: unresolved at the engine's default budget, and in the stress stratum (drawn budgets) where it ran
            'unresolved': q['status_mix'].get('unresolved', 0),
            'stress_unresolved': (z['stress_drawn_budgets']['status_mix'].get('unresolved', 0)
                                  if 'status_mix' in z.get('stress_drawn_budgets', {}) else None),
            # D34: build figures an earlier run measured, its snapshot reused here (the origin is named below)
            'build_reused': 'build_reused' in z,
        })
    origin = s.get('build_origin')
    return {'memory_limit': s['sizes'][-1]['memory_limit'],
            'distinct_patients': s['sizes'][-1].get('distinct_patients'),
            'generated_at': s.get('generated_at'),
            'budget_policy': policy(s['budget_policy']),
            'stress_policy': {**policy(s['stress_policy']), 'sizes': s['stress_policy']['sizes']},
            'build_origin': None if origin is None else {
                'first_built_at': origin['first_built'].get('generated_at'),
                'first_built_report': origin['first_built'].get('path'),
                'first_built_report_sha256': origin['first_built'].get('sha256'),
                'first_built_commit': (origin['first_built'].get('commit') or {}).get('head'),
                'first_built_benchmark_sha256': origin['first_built'].get('benchmark_sha256'),
                'machine': (origin['first_built'].get('environment') or {}).get('cpu')},
            'sizes': rows}


def policy(p):
    """A report's search budget policy (D41)."""
    return {'name': p['name'], 'search_budget': p['search_budget']}


def paired_run(name, pc, target):
    """One paired confirmation (D31, second attempt): the candidate's own figures per worker count, which govern the
    D21 target, with the paired ratios against the reference code."""
    arms = []
    for n, w in pc['by_workers'].items():
        t = w['candidate_target_check']
        arms.append({
            'workers': w['workers'],
            'p95_seconds': round(t['p95_seconds_all_runs'], 2),
            'max_seconds': round(t['max_seconds_all_runs'], 2),
            'p95_by_repeat': [round(x, 2) for x in t['p95_seconds_by_repeat']],
            'query_peak_gib': round(t['peak_rss_bytes'] / GIB, 2),
            'ratio_p95_by_repeat': [round(x, 3) for x in w['ratio_p95_by_repeat']],
            'ratio_p95_pooled': round(w['ratio_p95_pooled'], 3),
            'ratio_total_by_repeat': [round(x, 3) for x in w['ratio_total_by_repeat']],
            'reference_p95_seconds': round(w['reference_absolute']['p95_seconds_all_runs'], 2),
            'meets_target': bool(t['p95_met_all_runs'] and t['max_met_all_runs'] and t['memory_met']),
        })
    arms.sort(key=lambda a: a['workers'])
    return {'name': name, 'repeats': pc['repeats'], 'queries': pc['queries'], 'reference': pc['reference']['git_ref'],
            'candidate': pc['candidate']['git_ref'],
            'reference_policy': pc['reference']['budget_policy']['name'],
            'candidate_policy': pc['candidate']['budget_policy']['name'],
            'identical_queries': pc['byte_identity']['identical'], 'compared_queries': pc['byte_identity']['compared'],
            'mismatching_seeds': pc['byte_identity']['mismatching_seeds'],
            'arms': arms}


def matcher(m):
    c, target = m['confirmation'], m['target']
    arms = []
    for name, a in c['arms'].items():
        if not name.startswith('C_w'):
            continue
        t = c['target_check'][name]
        arms.append({
            'workers': a['workers'],
            'p95_seconds': round(t['p95_seconds_all_runs'], 2),
            'max_seconds': round(t['max_seconds_all_runs'], 2),
            'p95_by_repeat': [round(x, 2) for x in t['p95_seconds_by_repeat']],
            'query_peak_gib': round(a['query_process']['peak_rss_bytes'] / GIB, 2),
            'per_candidate_us': round(a['per_candidate_microseconds']['end_to_end'], 1),
            'meets_target': bool(t['p95_met_all_runs'] and t['max_met_all_runs'] and t['memory_met']),
        })
    arms.sort(key=lambda a: a['workers'])
    ident = c['byte_identity_C_against_B']
    # D41: the D21 status is read off the matcher report's paired run of two codes (D31's protocol), both at the
    # engine's default budget; the comparison of two budget policies on one code is shown apart and governs nothing.
    paired = paired_run('paired run, main against this tree', m['paired_confirmation'], target)
    if paired['reference_policy'] != paired['candidate_policy']:
        raise SystemExit('paired_confirmation compares budget policies; it must compare codes at one policy')
    budgets = paired_run('budget comparison, same code', m['budget_policy_comparison'], target)
    sequential = {a['workers']: a for a in arms}
    governing = [{'workers': a['workers'], 'p95_seconds': a['p95_seconds'], 'max_seconds': a['max_seconds'],
                  'query_peak_gib': a['query_peak_gib'], 'meets_target': a['meets_target'],
                  'sequential_p95_seconds': sequential[a['workers']]['p95_seconds'] if a['workers'] in sequential else None}
                 for a in paired['arms']]
    return {
        'budget_policy': policy(m['budget_policy']),
        'patients': c['size'], 'queries': c['queries'], 'repeats': c['repeats'],
        'target': {'p95_seconds': target['match_seconds_p95_max'], 'max_seconds': target['match_seconds_max_max'],
                   'memory_gib': round(target['query_process_peak_rss_bytes_below'] / GIB, 1),
                   'machine': target.get('machine')},
        'governing': {'protocol': 'paired-interleaved-1.0', 'run': paired['name'], 'by_workers': governing},
        'paired': paired,
        'budget_comparison': budgets,
        'sequential_arms': arms,
        'single_process_per_candidate_us': round(c['arms']['B']['per_candidate_microseconds']['end_to_end'], 1),
        'identical_results': {'compared': sum(v['compared'] for v in ident.values()),
                              'identical': sum(v['identical'] for v in ident.values())},
    }


def mimic(r):
    ch, d = r['chartevents'], r['differential']
    return {
        'patients': r['counts']['patients'],
        'icu_stays': r['counts']['icu_stays'],
        'inputevents': {'rows': r['counts']['source_rows'], 'admitted': r['counts']['admitted_rows'],
                        'claim_stores': r['route']['stores']},
        'chartevents': {'rows': ch['counts']['rows'], 'admitted': ch['counts']['events'],
                        'excluded': ch['counts']['excluded_records'], 'claim_stores': ch['counts']['claim_stores']},
        'store_tables': r['snapshot']['tables'],
        'census': {
            'raw_rows_checked': ch['raw_row_check']['rows'],
            'raw_row_check_passed': ch['raw_row_check']['passed'],
            'stores_round_tripped': ch['census']['stores_round_tripped'],
            'stores_graph_checked': ch['census']['stores_graph_checked'],
            'mismatched_stores': ch['census']['mismatched_stores'],
            'events_compared_with_built_store': ch['built_store_comparison']['events_compared'],
            'built_store_mismatches': ch['built_store_comparison']['stores_mismatched'],
        },
        'differential': {'queries': d['queries'], 'results': d['patient_results'], 'mismatches': d['mismatches'],
                         'status_mix': d['status_mix']},
    }


def rises(u, r):
    """D36: the rise templates' usefulness over the declared synthetic cohorts, and the MIMIC demo's relative
    differential."""
    def row(name, t):
        return {'name': name, 'candidates': t['candidates'], 'unresolved': t['unresolved'],
                'unresolved_split_off': t['unresolved_split_off'], 'statuses': t['statuses']}
    rel = r.get('relative_differential')
    return {
        'cohorts': len(u['seeds_used']), 'children': sum(s['children'] for s in u['per_seed'].values()),
        'seeds': [u['seeds_used'][0], u['seeds_used'][-1]],
        'templates': [row(n, t) for n, t in u['templates'].items()],
        'variants': [row(n, t) for n, t in u['variants'].items()],
        'mimic': None if rel is None else {
            'queries': rel['queries'], 'named': rel['named'], 'results_compared': rel['results_compared'],
            'mismatches': rel['mismatches'], 'pruning_on_off_differences': rel['pruning_on_off_differences'],
            'factor_relaxations_checked': rel['factor_relaxations_checked'],
            'containment_violations': rel['factor_relaxation_containment_violations'],
            'status_mix': rel['status_mix']},
    }


def build():
    loaded = {k: load(v) for k, v in REPORTS.items()}
    return {
        'generator': 'demos/mockups/results/build.py',
        'sources': {k: {'path': REPORTS[k], 'sha256': loaded[k][1]} for k in REPORTS},
        'scaling': scaling(loaded['scaling'][0]),
        'matching': matcher(loaded['matcher'][0]),
        'mimic_demo': mimic(loaded['mimic'][0]),
        'rises': rises(loaded['rises'][0], loaded['mimic'][0]),
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--check', action='store_true')
    a = p.parse_args(argv)
    text = json.dumps(build(), indent=1, sort_keys=True) + '\n'
    if a.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f'{OUT.relative_to(ROOT)} is stale; run demos/mockups/results/build.py', file=sys.stderr)
            return 1
        print(f'{OUT.relative_to(ROOT)} matches the reports')
        return 0
    OUT.write_text(text)
    print(f'wrote {OUT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
