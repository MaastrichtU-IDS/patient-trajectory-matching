"""D54 (decision log): the generator's ids against main's generator, and what generating costs.

D54 makes the synthetic generator's truth event ids and data-quality case ids unique by construction (the lowest free
number on a collision) and drops a case's link to a record removed after the case was made. This tool checks, against
main's generator (read from git at `--base`, default 8ecbbcd), that nothing else moved:

    python tools/d54_census.py census [--seeds SPEC] [--output REPORT.json]
        Rule 2. On every cohort: this tree's has no duplicated id and no dangling link (2.1); the patients that differ
        from main's are exactly those with a duplicated truth or case id, or a case naming a missing record, on main
        (2.2); every difference is an id, a link to one, or the removal of a dangling record id or case, and each link
        names the same event as on main wherever main's id named one (2.3); the ids changed are as many as main's
        duplicates (2.4); the converter's output is byte-identical (2.5).
    python tools/d54_census.py timing [--seeds SPEC] [--output REPORT.json]
        Rule 6 (as amended before measuring): `serialise(build(seed))`, what the scaling build times as generation, for
        main's generator and this tree's alternately per seed (A B B A A B), the minimum of 3 runs per arm; the median
        over seeds of the per-seed ratio (this tree over main) at most 1.05.
    python tools/d54_census.py figures [--output REPORT.json]
        Rule 5. Each regenerated report against main's committed one (git at `--base`), leaf by leaf, every difference
        classified: a cohort digest, an artifact digest, a timing, one of the figures rule 5 enumerates as computed from
        truth or case ids (D46's and D47's main arms in their usefulness reports; the reference reports' planted defects
        that read the truth), or for the scaling report a build or run figure outside the ruled ones. Anything else, any
        `met`-like flag that differs, and any structural difference is `other`, which must be empty.

Synthetic data only: the report holds counts and generator patient ids, never a record.
"""
import argparse
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns.synthetic_ingest import synthetic_ingest  # noqa: E402

GENERATOR = 'demos/mockups/synthetic/generate.py'
BASE = '8ecbbcd'
PAGE_SEED = 20261103
# Rule 2's cohorts: the page's, the scaling pool (with test_generator.py's seeds) and every seed a regenerated report runs.
CENSUS_SEEDS = '20261103-20261311,4611-4650,4711-4750,4911-4940,5201-5330,6111-6150'
TIMING_SEEDS = '20261103-20261311'
MAX_TIMING_RATIO = 1.05
ARTIFACTS = ('tools/d54_census.py', GENERATOR, 'patterns/synthetic_ingest.py', 'patterns/series_values.py')


def seeds_of(text):
    out = []
    for part in text.split(','):
        lo, _, hi = part.partition('-')
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def artifacts():
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in ARTIFACTS}


def module_from(source, name):
    """The generator executed from `source` (bytes), as `tools/scaling_build_worker.py` loads it."""
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / GENERATOR)
    exec(compile(source, str(ROOT / GENERATOR), 'exec'), module.__dict__)
    return module


def generators(base):
    """{'main': main's generator, 'unique': main's generator with every truth event given a fresh id from a counter
    (`<pid>-U0001`, ...) so that its links name events unambiguously (the review's S1), 'here': this tree's, 'digests'}."""
    main = subprocess.run(['git', 'show', f'{base}:{GENERATOR}'], cwd=ROOT, capture_output=True, check=True).stdout
    here = (ROOT / GENERATOR).read_bytes()
    unique = module_from(main, 'd54_main_unique_generate')
    numbered = unique.Child.event

    def event(self, cid, day, **kw):
        ev = numbered(self, cid, day, **kw)
        self.d54_counter = getattr(self, 'd54_counter', 0) + 1
        ev['event_id'] = f'{self.pid}-U{self.d54_counter:04d}'
        return ev
    unique.Child.event = event
    return {'main': module_from(main, 'd54_main_generate'), 'unique': unique, 'here': module_from(here, 'd54_here_generate'),
            'digests': {'main_sha256': hashlib.sha256(main).hexdigest(), 'here_sha256': hashlib.sha256(here).hexdigest()}}


def cohort(generator, seed):
    return json.loads(json.dumps(generator.serialise(generator.build(seed), seed), ensure_ascii=False))


def duplicated(ids):
    return {k for k, v in collections.Counter(ids).items() if v > 1}


def defects(p):
    """A patient's id defects: duplicated truth ids, duplicated case ids, cases naming a record not in the chart, and
    links naming no truth event."""
    events = {e['event_id'] for e in p['truth']['events']}
    records = {r['record_id'] for r in p['records']}
    links = [r.get('truth_event_id') for r in p['records']] + [m.get('truth_event_id') for r in p['records']
                                                                for m in r.get('mentions', [])]
    links += [q['truth_event_id'] for q in p['quality']]
    return {'truth': duplicated(e['event_id'] for e in p['truth']['events']),
            'case': duplicated(q['case_id'] for q in p['quality']),
            'dangling_case_records': [q['case_id'] for q in p['quality'] if any(r not in records for r in q['record_ids'])],
            'dangling_truth_links': sum(1 for t in links if t is not None and t not in events)}


def without(d, *keys):
    return {k: v for k, v in d.items() if k not in keys}


def record_links(p):
    """Every record's and mention's truth link, in order."""
    return [link for r in p['records'] for link in [r.get('truth_event_id')] + [m.get('truth_event_id') for m in r.get('mentions', [])]]


def pruned_cases(p):
    """A patient's cases as this tree should give them: a missing record id dropped, a case left naming none dropped."""
    present = {r['record_id'] for r in p['records']}
    kept, dropped, removed = [], [], 0
    for q in p['quality']:
        ids = [rid for rid in q['record_ids'] if rid in present]
        if q['record_ids'] and not ids:
            dropped.append(q['case_id'])
            continue
        removed += len(q['record_ids']) - len(ids)
        kept.append(dict(q, record_ids=ids))
    return kept, dropped, removed


def compare_patient(old, new, unique):
    """(problems, counts) for one patient: main's `old` against this tree's `new` (rules 2.3 and 2.4), each problem as
    (rule, text). `unique` is the same patient from main's generator with unique truth ids: every link here must name the
    event, by position, that its counterpart there names (the strict check, the review's S1), and main's own link must
    carry the id main gave that event."""
    problems, counts = [], collections.Counter()
    pid = old['patient_id']

    def problem(text):
        problems.append(('2.3', f'{pid}: {text}'))
    if without(old, 'truth', 'records', 'quality') != without(new, 'truth', 'records', 'quality'):
        problem('a field outside truth, records and cases differs')
    a, b, u = old['truth']['events'], new['truth']['events'], unique['truth']['events']
    if without(old['truth'], 'events') != without(new['truth'], 'events') or len(a) != len(b) or any(
            without(x, 'event_id') != without(y, 'event_id') for x, y in zip(a, b)):
        problem('a truth event differs other than in its id')
        return problems, counts
    if len(u) != len(a) or any(without(x, 'event_id') != without(y, 'event_id') for x, y in zip(a, u)):
        problem('main\'s truth with unique ids differs from main\'s other than in ids')
        return problems, counts
    counts['truth_ids_changed'] = sum(x['event_id'] != y['event_id'] for x, y in zip(a, b))
    at_u = {e['event_id']: i for i, e in enumerate(u)}
    at_b = {e['event_id']: i for i, e in enumerate(b)}

    def link_ok(before, strict, after):
        if strict is None or after is None:
            return before is None and strict is None and after is None
        i = at_u.get(strict)
        return i is not None and a[i]['event_id'] == before and at_b.get(after) == i

    if len(old['records']) != len(new['records']) or [r['record_id'] for r in old['records']] != [
            r['record_id'] for r in unique['records']]:
        problem('the records differ in number or order')
        return problems, counts
    for r, s in zip(old['records'], new['records']):
        ms, ns = r.get('mentions', []), s.get('mentions', [])
        if without(r, 'truth_event_id', 'mentions') != without(s, 'truth_event_id', 'mentions') or len(ms) != len(ns) or any(
                without(m, 'truth_event_id') != without(n, 'truth_event_id') for m, n in zip(ms, ns)):
            problem(f'{r["record_id"]} differs other than in a truth link')
    links = (record_links(old), record_links(unique), record_links(new))
    if len(set(map(len, links))) != 1:
        problem('the record and mention links differ in number')
        return problems, counts
    for before, strict, after in zip(*links):
        counts['links_checked'] += 1
        counts['links_changed'] += before != after
        if not link_ok(before, strict, after):
            problem(f'a link names another event ({before} -> {after})')
    expected, dropped, removed = pruned_cases(old)
    expected_u = pruned_cases(unique)[0]
    counts['cases_dropped'], counts['dangling_record_ids_removed'] = len(dropped), removed
    if len(expected) != len(new['quality']) or len(expected_u) != len(expected):
        problem('the cases differ in number beyond the dangling ones')
        return problems, counts
    for q, w, s in zip(expected, expected_u, new['quality']):
        if without(q, 'case_id', 'truth_event_id') != without(s, 'case_id', 'truth_event_id'):
            problem(f'{q["case_id"]} differs other than in its id or truth link')
        counts['links_checked'] += 1
        counts['links_changed'] += q['truth_event_id'] != s['truth_event_id']
        if not link_ok(q['truth_event_id'], w['truth_event_id'], s['truth_event_id']):
            problem(f'{q["case_id"]} names another event ({q["truth_event_id"]} -> {s["truth_event_id"]})')
        counts['case_ids_changed'] += q['case_id'] != s['case_id']
    return problems, counts


def census(seeds, base):
    gens = generators(base)
    totals, per_seed, problems, changed_patients, dropped_cases = collections.Counter(), {}, [], {}, {}
    for seed in seeds:
        old, uniq, new = cohort(gens['main'], seed), cohort(gens['unique'], seed), cohort(gens['here'], seed)
        row = collections.Counter()
        if without(old, 'patients') != without(new, 'patients'):
            problems.append(('2.3', f'{seed}: a field outside the patients differs'))
        olds, news = {p['patient_id']: p for p in old['patients']}, {p['patient_id']: p for p in new['patients']}
        uniqs = {p['patient_id']: p for p in uniq['patients']}
        flagged, changed = set(), []
        if list(olds) != list(news) or list(olds) != list(uniqs):
            problems.append(('2.3', f'{seed}: the cohorts hold different patients'))
        else:
            for pid, p in olds.items():
                before, after = defects(p), defects(news[pid])
                row['patients'] += 1
                row['main_duplicated_truth_ids'] += len(before['truth'])
                row['main_duplicated_case_ids'] += len(before['case'])
                row['main_cases_naming_missing_records'] += len(before['dangling_case_records'])
                row['main_dangling_truth_links'] += before['dangling_truth_links']
                row['here_duplicated_truth_ids'] += len(after['truth'])
                row['here_duplicated_case_ids'] += len(after['case'])
                row['here_cases_naming_missing_records'] += len(after['dangling_case_records'])
                row['here_dangling_truth_links'] += after['dangling_truth_links']
                if before['truth'] or before['case'] or before['dangling_case_records']:
                    flagged.add(pid)
                # Every patient's links are checked strictly, changed or not.
                found, counts = compare_patient(p, news[pid], uniqs[pid])
                problems.extend((rule, f'{seed} {text}') for rule, text in found)
                row.update(counts)
                if p != news[pid]:
                    changed.append(pid)
                    if counts['cases_dropped']:
                        dropped_cases[str(seed)] = dropped_cases.get(str(seed), []) + [pid]
                # 2.4, per patient: an id changes only where main duplicated one (pairs: one change per duplicate).
                if counts['truth_ids_changed'] != len(before['truth']):
                    problems.append(('2.4', f'{seed} {pid}: {counts["truth_ids_changed"]} truth ids changed, {len(before["truth"])} duplicated'))
                if counts['case_ids_changed'] != len(before['case']):
                    problems.append(('2.4', f'{seed} {pid}: {counts["case_ids_changed"]} case ids changed, {len(before["case"])} duplicated'))
            if set(changed) != flagged:
                problems.append(('2.2', f'{seed}: changed {sorted(set(changed) - flagged)} not flagged; flagged {sorted(flagged - set(changed))} unchanged'))
        row['patients_changed'] = len(changed)
        row['patients_flagged_on_main'] = len(flagged)
        row['converter_output_identical'] = int(json.dumps(synthetic_ingest(old), sort_keys=True)
                                                == json.dumps(synthetic_ingest(new), sort_keys=True))
        if not row['converter_output_identical']:
            problems.append(('2.5', f'{seed}: the converter\'s output differs'))
        row['cohort_changed'] = int(old != new)
        totals.update(row)
        per_seed[str(seed)] = dict(row)
        changed_patients[str(seed)] = changed
    rules = collections.Counter(rule for rule, _ in problems)
    met = {
        '2.1 no duplicate and no dangling link here': not any(totals[k] for k in (
            'here_duplicated_truth_ids', 'here_duplicated_case_ids', 'here_cases_naming_missing_records',
            'here_dangling_truth_links')),
        '2.2 changed patients exactly those flagged on main': rules['2.2'] == 0,
        '2.3 every difference an id, a link naming the same event, or a dangling removal': rules['2.3'] == 0,
        '2.4 ids changed equal duplicates on main': rules['2.4'] == 0
        and totals['truth_ids_changed'] == totals['main_duplicated_truth_ids']
        and totals['case_ids_changed'] == totals['main_duplicated_case_ids'],
        '2.5 converter output identical': rules['2.5'] == 0 and totals['converter_output_identical'] == len(seeds),
    }
    page = per_seed.get(str(PAGE_SEED))
    return {
        'profile': 'd54-generator-census-1.1',
        'rule': ('D54 rule 2: on every cohort, against main\'s generator (git at the base): this tree has no duplicated id '
                 'and no dangling link (2.1); the patients that differ are exactly those with a duplicated truth or case id, '
                 'or a case naming a missing record, on main (2.2); every difference is an id, a link to one, or the removal '
                 'of a dangling record id or case, and every link names the same event as on main, by position against main\'s '
                 'generator with unique ids (2.3); ids changed equal main\'s duplicates (2.4); the converter\'s output is '
                 'byte-identical (2.5). All met only with no problem listed and every seed counted.'),
        'base': base, 'generator': gens['digests'], 'seeds': len(per_seed), 'seeds_requested': len(seeds), 'seed_spec': None,
        'totals': dict(totals), 'page_cohort': page, 'met': met,
        'all_met': all(met.values()) and not problems and len(per_seed) == len(seeds),
        'problems': [f'{rule}: {text}' for rule, text in problems[:50]], 'problems_total': len(problems),
        'changed_patients': {k: v for k, v in changed_patients.items() if v},
        'cases_dropped': dropped_cases, 'per_seed': per_seed,
        'artifacts': artifacts(), 'contains_patient_level_data': False, 'synthetic_patient_ids': 'generator ids only',
    }


def timing(seeds, base, runs=3):
    gens = generators(base)
    arms, generator_digests = {'A': gens['main'], 'B': gens['here']}, gens['digests']
    order = 'ABBAAB'[:2 * runs]
    ratios, per_seed = [], {}
    for seed in seeds:
        seen = {'A': [], 'B': []}
        for arm in order:
            started = time.perf_counter()
            arms[arm].serialise(arms[arm].build(seed), seed)
            seen[arm].append(time.perf_counter() - started)
        a, b = min(seen['A']), min(seen['B'])
        ratios.append(b / a)
        per_seed[str(seed)] = {'main_s': round(a, 5), 'here_s': round(b, 5), 'ratio': round(b / a, 4)}
    median = statistics.median(ratios)
    return {
        'profile': 'd54-generator-timing-1.0',
        'rule': (f'D54 rule 6 (amended before measuring): serialise(build(seed)) for main\'s generator and this tree\'s, '
                 f'alternately per seed ({order}), the minimum of {runs} runs per arm; the median over seeds of the per-seed '
                 f'ratio (this tree over main) at most {MAX_TIMING_RATIO}.'),
        'base': base, 'generator': generator_digests, 'seeds': len(seeds), 'runs_per_arm': runs, 'order': order,
        'median_ratio': round(median, 4), 'max_ratio': round(max(ratios), 4), 'min_ratio': round(min(ratios), 4),
        'main_total_s': round(sum(v['main_s'] for v in per_seed.values()), 3),
        'here_total_s': round(sum(v['here_s'] for v in per_seed.values()), 3),
        'met': median <= MAX_TIMING_RATIO, 'per_seed': per_seed,
        'artifacts': artifacts(), 'contains_patient_level_data': False,
    }


# Rule 5's reports (the 18 live ones binding the generator).
REPORTS = ('concept-measure-benchmark', 'd46-window-census-report', 'd46-window-reference-report', 'd46-window-usefulness-report',
           'd47-page-census-report', 'd47-page-reference-report', 'd47-page-usefulness-report', 'd49-flip-usefulness-report',
           'd52d-anchor-census-report', 'd52d-anchor-reference-report', 'd52d-anchor-usefulness-report',
           'd53-coded-start-census-report', 'd53-coded-start-trace-report', 'd53-coded-start-usefulness-report',
           'e0b-mention-time-soundness-report', 'e0b-mention-time-defects-report', 'e0b-mention-time-arms-report',
           'execution-store-scaling-report')
FLAGS = {'met', 'all_met', 'caught', 'ruled'}
TIMING = ('seconds', 'elapsed', 'wall', 'view_cost', 'generated_at', 'rss', 'per_repeat', 'median_ms', '_ms')
TRUTH_DEFECTS = {'truth', 'mention_truth', 'case_truth'}
# The scaling report: what must be equal (the store's content and the queries' answers); everything else there is a build
# or run figure, re-measured by a fresh build and reported.
SCALING_RULED = {'snapshot_id', 'tables', 'ingest_events', 'patients', 'distinct_patients', 'cohorts_generated', 'status',
                 'events_per_distinct_patient', 'pool_bytes', 'exhaustive_check', 'candidate_count', 'status_mix',
                 'incomplete_none_by_note', 'pruning_rate', 'candidates', 'counts', 'incomplete_none', 'seed',
                 'search_budget', 'size', 'queries_by_search_budget', 'generator_seeds_failed', 'content_agrees'}


def leaves(node, path=(), out=None):
    out = {} if out is None else out
    if isinstance(node, dict):
        if not node:
            out[path] = {}
        for k, v in node.items():
            leaves(v, path + (str(k),), out)
    elif isinstance(node, list):
        if not node:
            out[path] = []
        for i, v in enumerate(node):
            leaves(v, path + (f'[{i}]',), out)
    else:
        out[path] = node
    return out


def classify(report, path):
    """The class of a differing leaf of `report` at `path` (rule 5)."""
    last = path[-1]
    if last in FLAGS:
        return 'other'
    if last in ('cohort_sha256', 'dataset_sha256') or path[:2] == ('cohort', 'sha256'):
        return 'cohort_digest'
    if 'artifacts' in path or path[:1] == ('build_provenance',):
        return 'artifact_digest'
    if report == 'execution-store-scaling-report':
        if any(t in p for p in path for t in TIMING):
            return 'timing'
        if path[:1] == ('sizes',) and not any(p in SCALING_RULED for p in path[2:]):
            return 'build_or_run_figure'
        if path[:1] in (('commit',), ('environment',), ('build_origin',), ('generated_at',), ('total_seconds',)):
            return 'build_or_run_figure'
    if any(t in p for p in path for t in TIMING):
        return 'timing'
    if report in ('d46-window-usefulness-report', 'd47-page-usefulness-report') and any(
            p == 'main' or p.endswith('@main') or p.startswith('main_') for p in path):
        return 'truth_or_case_reading'
    if report in ('d46-window-reference-report', 'd47-page-reference-report') and path[0] == 'defects' \
            and path[1] in TRUTH_DEFECTS and last != 'caught':
        return 'truth_or_case_reading'
    return 'other'


def figures(base, reports=REPORTS):
    out, other_total, stale = {}, 0, []
    current = hashlib.sha256((ROOT / GENERATOR).read_bytes()).hexdigest()
    for name in reports:
        rel = f'verification/{name}.json'
        before = json.loads(subprocess.run(['git', 'show', f'{base}:{rel}'], cwd=ROOT, capture_output=True,
                                           check=True).stdout)
        after = json.loads((ROOT / rel).read_text())
        a, b = leaves(before), leaves(after)
        # Currency (the review's S2): every digest the regenerated report records for the generator is this tree's.
        recorded = sorted({v for path, v in b.items() if path and path[-1] == GENERATOR})
        if recorded != [current]:
            stale.append(name)
        classes, listed = collections.Counter(), collections.defaultdict(list)
        for path in sorted(set(a) | set(b)):
            if path in a and path in b and a[path] == b[path]:
                continue
            if path not in a or path not in b:
                kind = 'build_or_run_figure' if name == 'execution-store-scaling-report' and classify(name, path) == \
                    'build_or_run_figure' else ('structure' if classify(name, path) == 'other' else classify(name, path))
            else:
                kind = classify(name, path)
            classes[kind] += 1
            if kind in ('other', 'structure', 'truth_or_case_reading') and len(listed[kind]) < 40:
                listed[kind].append({'path': '/'.join(path), 'main': a.get(path, '<absent>'), 'here': b.get(path, '<absent>')})
        others = classes['other'] + classes['structure']
        other_total += others
        out[name] = {'leaves': len(b), 'generator_current': recorded == [current], 'differing': dict(classes), 'other': others,
                     'listed': dict(listed)}
    return {
        'profile': 'd54-figure-equality-1.0',
        'rule': ('D54 rule 5: every figure of each regenerated report equals main\'s committed one but cohort digests, '
                 'artifact digests, timings, the figures enumerated as computed from truth or case ids (reported with '
                 'their direction), and the scaling report\'s build and run figures outside the store\'s content and the '
                 'queries\' answers; every met-like flag equal; no other or structural difference; and every compared report '
                 'regenerated on this tree (each generator digest it records is this tree\'s).'),
        'base': base, 'generator_sha256': current, 'reports': out, 'other_total': other_total, 'not_current': stale,
        'met': other_total == 0 and not stale,
        'artifacts': {**artifacts(), **{f'verification/{n}.json': hashlib.sha256((ROOT / f'verification/{n}.json').read_bytes()).hexdigest()
                                        for n in reports}},
        'contains_patient_level_data': False, 'synthetic_patient_ids': 'generator ids only, in listed paths',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('mode', choices=('census', 'timing', 'figures'))
    parser.add_argument('--seeds')
    parser.add_argument('--base', default=BASE)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'census':
        spec = args.seeds or CENSUS_SEEDS
        report = census(seeds_of(spec), args.base)
        report['seed_spec'] = spec
        summary = (f'census: {"met" if report["all_met"] else "MISSED"}; {report["seeds"]} cohorts, '
                   f'{report["totals"].get("patients_changed", 0)} patients changed, {report["problems_total"]} problems')
    elif args.mode == 'figures':
        report = figures(args.base)
        summary = f'figures: {"met" if report["met"] else "MISSED"}; {report["other_total"]} other differences, {len(report["not_current"])} not current'
    else:
        spec = args.seeds or TIMING_SEEDS
        report = timing(seeds_of(spec), args.base)
        report['seed_spec'] = spec
        summary = f'timing: {"met" if report["met"] else "MISSED"}; median ratio {report["median_ratio"]}'
    text = json.dumps(report, indent=1, sort_keys=False) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    print(summary, file=sys.stderr)
    return 0 if report.get('all_met', report.get('met')) else 1


if __name__ == '__main__':
    sys.exit(main())
