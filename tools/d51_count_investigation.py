#!/usr/bin/env python3
"""D51: is 1.1 Nth and count slower since f9e087e, or are count requests noisier than the controls normalise?

D48b's rebind of D44b's count verdict missed in one pair (1.1 Nth and count 1.078 control-normalised in pair 3 of 4, a
request that runs no D48 code). D51 settles it with a targeted paired run, under a decision rule committed before
measuring (decision log, D51):

- `run` times that request alone beside the same two controls (1.1 sequence, 1.3 coded only), this tree against the
  reference (f9e087e), with the count-latency tool unchanged (`tools/benchmark_count_requests.py --rules d44b
  --requests ... --workers 14 --repeats 8 --runs 3`, plus D48b's fixed changed set and bounded set for its reported
  control checks), then analyses the tool's report into `verification/d51-count-investigation.json`.
- `analyse` reads a report the tool wrote and does the same, without timing anything.

The analysis (`analyse_report`): for each ABBA pair at 14 workers, the count request's raw ratio (candidate median over
reference median, each the median of a process's 3 runs), the controls' ratios and their geometric mean, and the
normalised ratio (raw over the geometric mean, the tool's own `normalised_of`). The decision (`decide`) on the 8
normalised ratios:
- (i) no slowdown: the median at most 1.02 and at most 1 pair above 1.05;
- (ii) a real slowdown: the median above 1.05;
- (iii) otherwise inconclusive.
It applies only if the protocol was followed (8 pairs, 3 runs, 14 workers, the three requests, the reference resolved)
and each request's answers are one digest per side, equal to the reference's (the tool's `answers_check`); otherwise
there is no decision.

The variance diagnosis (`spread_of`, reported, not ruled): within each process, each request's run-to-run spread over
its 3 runs (the relative range, (max - min) / median, and the coefficient of variation); between processes, the standard
deviation over the 8 pairs of the natural log of each ratio (count raw, each control, the controls' geometric mean,
normalised), and the correlation of the count request's log ratio with the controls' log geometric mean (how much of
the count request's pair-to-pair movement the normalisation tracks).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

import benchmark_count_requests as bcr  # noqa: E402

PROFILE = 'd51-count-investigation-1.0'
REQUEST = '1.1 nth and count'
CONTROLS = tuple(bcr.NO_COUNT_REQUESTS)
REQUESTS = (REQUEST, *CONTROLS)
WORKERS = 14
PAIRS = 8
RUNS = 3
REFERENCE = 'f9e087e'
MEDIAN_NO_SLOWDOWN = 1.02  # (i): the median normalised ratio at most this
PAIR_BAR = 1.05  # (i): at most PAIRS_ALLOWED_OVER pairs above this
PAIRS_ALLOWED_OVER = 1
MEDIAN_SLOWDOWN = 1.05  # (ii): the median normalised ratio above this
OUTPUT = 'verification/d51-count-investigation.json'
ARTIFACTS = ('tools/d51_count_investigation.py', 'tools/benchmark_count_requests.py')


def decide(normalised):
    """D51's decision rule on the normalised ratios, pair by pair: 'i', 'ii' or 'iii' (see the module docstring)."""
    median = statistics.median(normalised)
    over = sum(r > PAIR_BAR for r in normalised)
    if median <= MEDIAN_NO_SLOWDOWN and over <= PAIRS_ALLOWED_OVER:
        outcome = 'i'
    elif median > MEDIAN_SLOWDOWN:
        outcome = 'ii'
    else:
        outcome = 'iii'
    return {'outcome': outcome, 'median_normalised': median, 'pairs_over_bar': over, 'pairs': len(normalised),
            'rule': {'i': f'median <= {MEDIAN_NO_SLOWDOWN} and at most {PAIRS_ALLOWED_OVER} pair above {PAIR_BAR}',
                     'ii': f'median > {MEDIAN_SLOWDOWN}', 'iii': 'otherwise'}}


def _within(seconds):
    median = statistics.median(seconds)
    return {'relative_range': (max(seconds) - min(seconds)) / median,
            'cv': statistics.stdev(seconds) / statistics.mean(seconds) if len(seconds) > 1 else 0.0}


def _sd_log(ratios):
    return statistics.stdev([math.log(r) for r in ratios]) if len(ratios) > 1 else 0.0


def _correlation(xs, ys):
    if len(xs) < 2 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    return statistics.correlation(xs, ys)


def spread_of(at, normalised, gmean):
    """The variance diagnosis on the 14-worker statistics `at` (`statistics_of(...)[str(WORKERS)]['requests']`)."""
    within = {}
    for name in REQUESTS:
        rows = [{'repeat': p['repeat'], 'side': side, **_within(p[f'{side}_seconds'])}
                for p in at[name]['pairs'] for side in ('reference', 'candidate')]
        within[name] = {
            'processes': rows,
            **{f'{k}_{stat}': fn([r[k] for r in rows]) for k in ('relative_range', 'cv')
               for stat, fn in (('median', statistics.median), ('max', max))},
            'by_side': {side: {f'relative_range_{stat}': fn([r['relative_range'] for r in rows if r['side'] == side])
                               for stat, fn in (('median', statistics.median), ('max', max))}
                        for side in ('reference', 'candidate')}}
    raw = at[REQUEST]['ratio_by_repeat']
    between = {'sd_log_ratio': {**{name: _sd_log(at[name]['ratio_by_repeat']) for name in REQUESTS},
                                'controls_geometric_mean': _sd_log(gmean), 'normalised': _sd_log(normalised)},
               'correlation_count_with_controls': _correlation([math.log(r) for r in raw],
                                                               [math.log(g) for g in gmean])}
    return {'within_process': within, 'between_pairs': between,
            'note': ('within: each request\'s 3 runs in one process (8 pairs x 2 sides = 16 processes); between: over '
                     'the 8 pairs, the standard deviation of the natural log of each ratio')}


def protocol_of(report):
    return {'pairs': report['repeats'] == PAIRS, 'runs': report['runs_per_request'] == RUNS,
            'workers': report['workers'] == [WORKERS], 'requests': sorted(report['requests']) == sorted(REQUESTS),
            'reference_resolved': bool(report['reference'].get('git_commit'))}


def analyse_report(report):
    """D51's analysis of a count-latency report (see the module docstring)."""
    stats = report['by_workers']
    at = stats[str(WORKERS)]['requests']
    by, gmean = bcr.normalised_of(stats, list(at), WORKERS)
    normalised = by[REQUEST]
    pairs = [{'repeat': p['repeat'], 'order': p['order'], 'raw': p['ratio'],
              'controls': {name: at[name]['ratio_by_repeat'][i] for name in CONTROLS},
              'controls_geometric_mean': gmean[i], 'normalised': normalised[i],
              'reference_median_seconds': p['reference_median_seconds'],
              'candidate_median_seconds': p['candidate_median_seconds']}
             for i, p in enumerate(at[REQUEST]['pairs'])]
    protocol = protocol_of(report)
    protocol['met'] = all(protocol.values())
    answers = report['answers']['check']
    answers_met = answers['met'] and all(r.get('nothing_moved', False) for r in answers['requests'].values())
    decision = decide(normalised) if protocol['met'] and answers_met else {'outcome': None,
                                                                          'why': 'protocol or answers not met'}
    return {'request': REQUEST, 'controls': list(CONTROLS), 'workers': WORKERS, 'pairs': pairs,
            'normalised_by_pair': normalised, 'raw_by_pair': at[REQUEST]['ratio_by_repeat'],
            'controls_geometric_mean_by_pair': gmean,
            'raw_median': statistics.median(at[REQUEST]['ratio_by_repeat']),
            'ratio_by_order': {first: [p['normalised'] for p in pairs if p['order'][0] == first]
                               for first in ('reference', 'candidate')},
            'protocol': protocol, 'answers_met': answers_met, 'decision': decision,
            'variance': spread_of(at, normalised, gmean)}


def write(report, output, command=None):
    out = {'profile': PROFILE, 'question': ('is there a real slowdown in 1.1 Nth and count since f9e087e, or are count '
                                            'requests in the post-pass pool noisier than the controls normalise?'),
           'reference': report['reference'], 'size': report['size'], 'snapshot_id': report['snapshot_id'],
           'command': command, 'analysis': analyse_report(report),
           'count_latency_report': report,
           'count_latency_report_note': ("the count-latency tool's own report, embedded; its `verdict` is D44b's rule "
                                         'on a subset of requests (its protocol check wants all six), not D51\'s'),
           'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS},
           'contains_patient_level_data': False}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=1) + '\n')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run', help='the paired run, then the analysis')
    r.add_argument('--snapshot', required=True, help='SIZE=SNAPSHOT_DIRECTORY')
    r.add_argument('--reference', default=REFERENCE)
    r.add_argument('--raw', type=Path, required=True, help="where the count-latency tool writes its report")
    r.add_argument('--changed-set', nargs='+', required=True)
    r.add_argument('--changed-set-label')
    r.add_argument('--bounded-set', nargs='+')
    r.add_argument('--output', type=Path, default=ROOT / OUTPUT)
    a = sub.add_parser('analyse', help="the analysis of a count-latency report already written")
    a.add_argument('raw', type=Path)
    a.add_argument('--output', type=Path, default=ROOT / OUTPUT)
    args = parser.parse_args(argv)
    command = None
    if args.command == 'run':
        command = [sys.executable, str(ROOT / 'tools' / 'benchmark_count_requests.py'), '--rules', 'd44b',
                   '--snapshot', args.snapshot, '--reference', args.reference, '--requests', *REQUESTS,
                   '--workers', str(WORKERS), '--repeats', str(PAIRS), '--runs', str(RUNS), '--output', str(args.raw),
                   '--changed-set', *args.changed_set]
        if args.changed_set_label:
            command += ['--changed-set-label', args.changed_set_label]
        if args.bounded_set:
            command += ['--bounded-set', *args.bounded_set]
        subprocess.run(command, cwd=ROOT, check=True)
    shown = [str(c).replace(str(ROOT), '.').replace(str(Path.home()), '~') for c in command] if command else None
    out = write(json.loads(Path(args.raw).read_text()), args.output, command=shown)
    print(json.dumps(out['analysis']['decision']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
