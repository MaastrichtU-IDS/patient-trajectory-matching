"""Trajectory queries against a locally built MIMIC-IV demo store, answered with aggregate counts only.

Stage 1 of moving the mockups onto the execution store (decision D29): the store sits beside the
existing pages, not under them. `server.py` serves three endpoints from this module:

- `GET /api/store/status`: whether a store is configured, and if not, why and how to build one;
- `GET /api/store/catalogue`: the store's item classes and groupings, with the units recorded for
  each, as aggregate counts;
- `POST /api/store/match`: a trajectory query, with optional value conditions (D28) and
  relaxations, run by `execution_store.match`; the answer is counts by status, `not_comparable`
  reasons, relaxations used, candidate and pruned counts and timings.

Two data rules are enforced here, not in the page:

1. **Aggregate counts only** (D17 and the demo convention). The answer is built field by field
   from counts (`aggregate`), then checked against a closed schema (`check_aggregate`): only known
   keys, integer counts, codes from fixed vocabularies, and no string or integer equal to one of the
   store's patient identifiers. It never holds patient ids, times, values or per-patient rows.
2. **Only the open MIMIC-IV demo.** A snapshot whose manifest names any other dataset is refused
   before it is opened (`OPEN_DATASETS`), so a credentialed snapshot can never be served through
   these endpoints.

The store is built locally from `data/local-mimic/` by the existing tooling and is never committed.
Its concepts are source item classes (`d_items` itemids) and their dictionary categories, not
clinical concepts; labels, when a `d_items` file is supplied, are the public dictionary's terms.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import gzip
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

PROFILE = 'mockup-store-query-1.0'
# The only datasets these endpoints serve: the open MIMIC-IV demo as the store's MIMIC converter names it.
OPEN_DATASETS = frozenset({'mimic-iv-demo-2.2:source_item_class'})
ITEM_NS = 'https://example.org/trajectory/mimic-record-item/'
GROUP_NS = 'https://example.org/trajectory/mimic-record-item-category/'
INTERVAL_SOURCE = 'inputevents:continuous_segments'
STATUSES = ('exact', 'possible', 'relaxed', 'unresolved', 'none', 'not_comparable', 'excluded')
OCCURRENCES = ('any', 'first', 'last')
SCOPES = ('any', 'from_start', 'within', 'after')
WINDOWS = ('first_stay', 'first_stay_24h', 'all_stays')
TIME_UNITS = {'minute': 60_000_000, 'hour': 3_600_000_000, 'day': 86_400_000_000}
LOWER, UPPER = ('gt', 'ge'), ('lt', 'le')
BUDGETS = ('0', '0.5', '1', '1.5', '2')
STEP_IDS = tuple('ABCDEF')
MAX_RELATIONS = 10
SEARCH_BUDGET = 40_000
HOW_TO_BUILD = [
    'python -m pip install -r patterns/requirements-store.lock.txt',
    'python -m patterns.mimic_store_ingest --input-dir data/local-mimic/icu --dataset-id mimic-iv-demo-2.2 '
    '--chartevents --workers 8',
    'python demos/mockups/server.py --port 8091 --mimic-store verification/execution-store-mimic-run/store '
    '--mimic-d-items data/local-mimic/icu/d_items.csv.gz',
]
CODE = re.compile(r'[A-Z][A-Z0-9_]*')
RELAXATION_ID = re.compile(r'(concept|value):[A-F]|gap:R(10|[1-9])')


class QueryError(ValueError):
    """A request the endpoint refuses, by name (HTTP 400)."""


class Unavailable(RuntimeError):
    """No store can be served: not configured, unreadable, or refused (HTTP 503)."""


class AggregateRuleViolation(RuntimeError):
    """An answer that is not aggregate counts only; it is never sent (HTTP 500)."""


def require(condition, code):
    if not condition:
        raise QueryError(code)


# ---------------------------------------------------------------- the snapshot: locate, refuse, open

def locate_snapshot(path):
    """The snapshot directory at `path`: the snapshot itself, or a root holding exactly one snapshot."""
    path = Path(path)
    if (path / 'manifest.json').is_file():
        return path
    found = sorted(p for p in path.iterdir() if (p / 'manifest.json').is_file()) if path.is_dir() else []
    if len(found) != 1:
        raise Unavailable('SNAPSHOT_NOT_FOUND' if not found else 'SEVERAL_SNAPSHOTS_UNDER_PATH')
    return found[0]


def check_open_dataset(manifest):
    """Refuse any snapshot that is not the open MIMIC-IV demo, by its manifest's dataset id."""
    dataset = manifest.get('dataset_id') if isinstance(manifest, dict) else None
    if dataset not in OPEN_DATASETS:
        raise Unavailable('SNAPSHOT_NOT_OPEN_MIMIC_DEMO')
    return dataset


def read_d_items(path):
    """{itemid: label} from a d_items file (.csv or .csv.gz): the public dictionary's terms."""
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', newline='', encoding='utf-8') as handle:
        return {row['itemid']: row['label'] for row in csv.DictReader(handle) if row.get('itemid')}


class StoreService:
    """One configured snapshot, opened lazily and shared by the server's threads under a lock."""

    def __init__(self, path=None, d_items=None):
        self.path = None if path in (None, '') else Path(path)
        self.d_items = None if d_items in (None, '') else Path(d_items)
        self.lock = threading.Lock()
        self.store = None
        self.error = None
        self.opened = False

    # The store is opened once; a failure is remembered and reported, never retried per request.
    def _open(self):
        if self.opened:
            return
        self.opened = True
        if self.path is None:
            self.error = 'STORE_NOT_CONFIGURED'
            return
        try:
            snapshot = locate_snapshot(self.path)
            manifest = json.loads((snapshot / 'manifest.json').read_text())
            self.dataset = check_open_dataset(manifest)  # before DuckDB reads a single file
            try:
                from patterns import execution_store as es
                from patterns import trajectory_graph as tg
            except ImportError:
                raise Unavailable('STORE_SUPPORT_NOT_INSTALLED')
            self.es, self.tg = es, tg
            store = es.open_store(snapshot, verify='files')
            check_open_dataset(store.manifest)
            self.store = store
            self.labels = self._labels()
            self._load()
        except Unavailable as error:
            self.error = str(error)
        except (OSError, ValueError) as error:
            self.error = 'SNAPSHOT_UNREADABLE'
            self.detail = type(error).__name__

    def _labels(self):
        if self.d_items is None:
            return {}
        try:
            return read_d_items(self.d_items)
        except (OSError, ValueError, KeyError, csv.Error):
            return {}

    def _load(self):
        """Patient identifiers (for the aggregate check only), windows and the catalogue, computed once."""
        q = self.store.query
        self.patients = [pid for _, pid in q('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')]
        self.patient_ids = frozenset(self.patients)
        # Each patient's coverage periods: per ICU stay, from the inputevents source (one per stay), and all sources.
        stays, spans = {}, {}
        for pid, source, lo, hi in q('SELECT p.patient_id, c.source, c.lo, c.hi FROM coverage c '
                                     'JOIN patients p USING (patient_idx) ORDER BY 1, 3, 4, 2'):
            if source == INTERVAL_SOURCE:
                stays.setdefault(pid, []).append((lo, hi))
            old = spans.get(pid)
            spans[pid] = (lo, hi) if old is None else (min(old[0], lo), max(old[1], hi))
        day = TIME_UNITS['day']
        first = {pid: min(s) for pid, s in stays.items()}
        self.windows = {
            'first_stay': {pid: {'start': lo, 'end': hi} for pid, (lo, hi) in first.items()},
            'first_stay_24h': {pid: {'start': lo, 'end': lo + day} for pid, (lo, _) in first.items()},
            'all_stays': {pid: {'start': lo, 'end': hi} for pid, (lo, hi) in spans.items()},
        }
        sources = {}
        for source, concept in q('SELECT source, concept FROM source_concepts'):
            sources.setdefault(concept, set()).add(source)
        parents = {}
        for concept, ancestor in q("SELECT concept, ancestor FROM concept_closure WHERE basis = 'declared'"):
            if concept.startswith(ITEM_NS) and ancestor.startswith(GROUP_NS):
                parents[concept] = ancestor
        units = {}
        for concept, unit, rows, patients in q('SELECT item_class, unit, count(*), count(DISTINCT patient_idx) '
                                               'FROM series GROUP BY 1, 2'):
            units.setdefault(concept, {})[unit] = (rows, patients)
        entries = []
        for concept, patients, events in q('SELECT concept, count(*), sum(n) FROM concept_postings '
                                           'GROUP BY 1 ORDER BY 1'):
            if concept.startswith(ITEM_NS):
                itemid = concept[len(ITEM_NS):]
                group = parents.get(concept)
                entry = {'concept': concept, 'kind': 'item', 'itemid': itemid,
                         'label': self.labels.get(itemid) or f'item {itemid}',
                         'category': unquote(group[len(GROUP_NS):]) if group else None,
                         'grouping': group, 'own': units.get(concept, {})}
            elif concept.startswith(GROUP_NS):
                entry = {'concept': concept, 'kind': 'grouping', 'itemid': None,
                         'label': unquote(concept[len(GROUP_NS):]), 'category': unquote(concept[len(GROUP_NS):]),
                         'grouping': None, 'own': {}}
            else:
                continue  # structural classes are refused as query concepts on a store with excluded records
            entry.update(patients=int(patients), events=int(events), sources=sorted(sources.get(concept, ())))
            entries.append(entry)
        # A grouping's units are those of the item classes under it.
        for entry in entries:
            if entry['kind'] == 'grouping':
                merged = {}
                for item in entries:
                    if item['grouping'] == entry['concept']:
                        for unit, (rows, patients) in item['own'].items():
                            r, _ = merged.get(unit, (0, 0))
                            merged[unit] = (r + rows, None)  # patients across items are not additive
                entry['own'] = merged
            entry['interval'] = entry['sources'] == [INTERVAL_SOURCE]
        self.entries = {e['concept']: e for e in entries}

    # ---------------------------------------------------------------- endpoints

    def status(self):
        with self.lock:
            self._open()
            if self.store is None:
                return {'profile': PROFILE, 'configured': self.path is not None, 'available': False,
                        'error': self.error, 'how_to_build': HOW_TO_BUILD,
                        'open_datasets': sorted(OPEN_DATASETS)}
            m = self.store.manifest
            return {'profile': PROFILE, 'configured': True, 'available': True, 'dataset_id': m['dataset_id'],
                    'snapshot_id': m['snapshot_id'], 'store_profile': m['profile'], 'patients': len(self.patients),
                    'labels': m.get('labels') or {}, 'dictionary_labels': bool(self.labels),
                    'tables': {name: t['rows'] for name, t in sorted(m['tables'].items())},
                    'windows': {k: len(v) for k, v in self.windows.items()}}

    def catalogue(self):
        with self.lock:
            self._open()
            if self.store is None:
                raise Unavailable(self.error)
            out = []
            for e in self.entries.values():
                out.append({'concept': e['concept'], 'kind': e['kind'], 'itemid': e['itemid'], 'label': e['label'],
                            'category': e['category'], 'grouping': e['grouping'], 'patients': e['patients'],
                            'events': e['events'], 'interval': e['interval'],
                            'sources': e['sources'],
                            'units': [{'unit': u, 'rows': r, **({'patients': p} if p is not None else {})}
                                      for u, (r, p) in sorted(e['own'].items(), key=lambda kv: -kv[1][0])]})
            return {'profile': PROFILE, 'dataset_id': self.dataset, 'patients': len(self.patients),
                    'dictionary_labels': bool(self.labels), 'entries': out}

    def match(self, spec):
        with self.lock:
            self._open()
            if self.store is None:
                raise Unavailable(self.error)
            started = time.perf_counter()
            query, conditions, windows, declared = self.compile(spec)
            es = self.es
            try:
                t = time.perf_counter()
                out = es.match(self.store, query, windows, detail='decided', conditions=conditions)
                match_seconds = time.perf_counter() - t
                t = time.perf_counter()
                pruned = self._pruned(query, conditions, windows)
                prune_seconds = time.perf_counter() - t
            except (es.StoreError, self.tg.TrajectoryError) as error:
                raise QueryError(str(error).split(':')[0].strip() or 'REFUSED_BY_STORE')
            payload = aggregate(out, declared, pruned, sum(1 for w in windows.values() if w is not None),
                                {'match_seconds': match_seconds, 'pruning_count_seconds': prune_seconds,
                                 'total_seconds': time.perf_counter() - started})
            return check_aggregate(payload, self.patient_ids)

    def _pruned(self, query, conditions, windows):
        """How many candidates time pruning decided without loading their events: the store's own pruning function
        (`execution_store._prune`, D25, D26) run on the inputs `match` gave it. Read-only; it changes no result."""
        es, tg = self.es, self.tg
        request = None if conditions is None else es.Request(query, conditions)
        pq = tg.PreparedQuery(query if request is None else request.query)
        by_id = {pid: idx for idx, pid in enumerate(self.patients)}
        windows_by_idx = {by_id[pid]: windows.get(pid) for pid in self.patients}
        found = es._candidates(self.store, pq, request)
        live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
        return len(es._prune(self.store, pq, live, windows_by_idx, tags=request.used if request else None))

    # ---------------------------------------------------------------- the page's query, compiled for the store

    def compile(self, spec):
        """(query, conditions or None, windows, declared relaxation ids) from the page's request, refusing anything else."""
        require(isinstance(spec, dict) and set(spec) <= {'steps', 'relations', 'window', 'scope', 'budget'}
                and {'steps', 'window', 'scope'} <= set(spec), 'REQUEST_FIELDS')
        require(spec['window'] in WINDOWS, 'UNKNOWN_WINDOW')
        require(spec['scope'] in SCOPES, 'UNKNOWN_SCOPE')
        budget = spec.get('budget', '1')
        require(budget in BUDGETS, 'UNKNOWN_BUDGET')
        steps = spec['steps']
        require(isinstance(steps, list) and 1 <= len(steps) <= len(STEP_IDS), 'STEPS_MUST_BE_1_TO_6')
        out_steps, relaxations, conditions, value_relaxations, declared = [], [], {}, [], set()
        ids = set()
        for s in steps:
            require(isinstance(s, dict) and set(s) <= {'id', 'concept', 'occurrence', 'condition', 'widen_value',
                                                       'widen_concept'} and {'id', 'concept'} <= set(s), 'STEP_FIELDS')
            sid = s['id']
            require(sid in STEP_IDS and sid not in ids, 'STEP_ID')
            ids.add(sid)
            entry = self.entries.get(s['concept'])
            require(entry is not None, 'UNKNOWN_CONCEPT')
            occurrence = s.get('occurrence', 'any')
            require(occurrence in OCCURRENCES, 'UNKNOWN_OCCURRENCE')
            out_steps.append({'id': sid, 'concept': entry['concept'], 'occurrence': occurrence})
            if s.get('condition') is not None:
                conditions[sid] = condition(s['condition'], entry)
                if s.get('widen_value') is not None:
                    w = s['widen_value']
                    require(isinstance(w, dict) and set(w) == {'condition', 'cost'}, 'WIDEN_VALUE_FIELDS')
                    wider = condition(w['condition'], entry)
                    require(wider['unit'] == conditions[sid]['unit'], 'WIDEN_VALUE_UNIT')
                    rid = f'value:{sid}'
                    value_relaxations.append({'id': rid, 'kind': 'widen_value', 'step': sid, 'cost': cost(w['cost']),
                                              'condition': wider})
                    declared.add(rid)
            else:
                require(s.get('widen_value') is None, 'WIDEN_VALUE_NEEDS_A_CONDITION')
            if s.get('widen_concept') is not None:
                w = s['widen_concept']
                require(isinstance(w, dict) and set(w) == {'concept', 'cost'}, 'WIDEN_CONCEPT_FIELDS')
                wider = self.entries.get(w['concept'])
                require(wider is not None and wider['kind'] == 'grouping' and entry['grouping'] == wider['concept'],
                        'WIDEN_CONCEPT_MUST_BE_THE_ITEMS_GROUPING')
                rid = f'concept:{sid}'
                relaxations.append({'id': rid, 'kind': 'widen_concept', 'step': sid, 'concept': wider['concept'],
                                    'cost': cost(w['cost'])})
                declared.add(rid)
        relations = spec.get('relations', [])
        require(isinstance(relations, list) and len(relations) <= MAX_RELATIONS, 'RELATIONS')
        out_relations = []
        for k, r in enumerate(relations, 1):
            require(isinstance(r, dict) and r.get('kind') in ('after', 'during'), 'RELATION_KIND')
            require(r.get('from') in ids and r.get('to') in ids and r['from'] != r['to'], 'RELATION_STEPS')
            rid = f'R{k}'
            if r['kind'] == 'after':
                require(set(r) <= {'kind', 'from', 'to', 'min', 'max', 'extend_max'}, 'RELATION_FIELDS')
                rel = {'id': rid, 'kind': 'after', 'from': r['from'], 'to': r['to']}
                if r.get('min') is not None:
                    rel['min'] = duration(r['min'])
                if r.get('max') is not None:
                    rel['max'] = duration(r['max'])
                require(rel.get('min') is None or rel.get('max') is None or rel['min'] <= rel['max'], 'GAP_INVERTED')
                if r.get('extend_max') is not None:
                    x = r['extend_max']
                    require(isinstance(x, dict) and set(x) == {'max', 'cost'} and 'max' in rel, 'EXTEND_MAX_FIELDS')
                    wider = duration(x['max'])
                    require(wider > rel['max'], 'EXTEND_MAX_MUST_WIDEN')
                    relaxations.append({'id': f'gap:{rid}', 'kind': 'extend_max', 'relation': rid, 'max': wider,
                                        'cost': cost(x['cost'])})
                    declared.add(f'gap:{rid}')
            else:
                require(set(r) <= {'kind', 'from', 'to', 'grace', 'extend_grace'}, 'RELATION_FIELDS')
                frm = self.entries[next(s['concept'] for s in out_steps if s['id'] == r['from'])]
                require(frm['interval'], 'DURING_NEEDS_AN_INTERVAL_STEP')
                rel = {'id': rid, 'kind': 'during', 'from': r['from'], 'to': r['to'],
                       'grace': duration(r['grace']) if r.get('grace') is not None else 0}
                if r.get('extend_grace') is not None:
                    x = r['extend_grace']
                    require(isinstance(x, dict) and set(x) == {'grace', 'cost'}, 'EXTEND_GRACE_FIELDS')
                    wider = duration(x['grace'])
                    require(wider > rel['grace'], 'EXTEND_GRACE_MUST_WIDEN')
                    relaxations.append({'id': f'gap:{rid}', 'kind': 'extend_grace', 'relation': rid, 'grace': wider,
                                        'cost': cost(x['cost'])})
                    declared.add(f'gap:{rid}')
            out_relations.append(rel)
        query = {'profile': self.tg.PROFILE_ID, 'scope': spec['scope'], 'steps': out_steps,
                 'relations': out_relations, 'relaxations': relaxations, 'budget': budget,
                 'search_budget': SEARCH_BUDGET}
        conds = None
        if conditions:
            conds = {'profile': self.es.CONDITIONS_PROFILE, 'steps': conditions}
            if value_relaxations:
                conds['relaxations'] = value_relaxations
        windows = {pid: self.windows[spec['window']].get(pid) for pid in self.patients}
        return query, conds, windows, declared


def decimal_string(value):
    """A threshold as the page sent it: a string, never a JSON number, which would have passed through a float."""
    require(type(value) is str, 'THRESHOLD_MUST_BE_A_STRING')
    require(re.fullmatch(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?', value) is not None and len(value) <= 40,
            'THRESHOLD_NOT_A_PLAIN_DECIMAL')
    return value


def condition(raw, entry):
    """The store's condition (`execution-store-conditions-1.0`) from {unit, lower?: {op, value}, upper?: {op, value}}."""
    require(isinstance(raw, dict) and set(raw) <= {'unit', 'lower', 'upper'} and 'unit' in raw, 'CONDITION_FIELDS')
    require(raw['unit'] in entry['own'], 'UNIT_NOT_RECORDED_FOR_THIS_ITEM')
    out = {'unit': raw['unit']}
    for side, ops in (('lower', LOWER), ('upper', UPPER)):
        b = raw.get(side)
        if b is None:
            continue
        require(isinstance(b, dict) and set(b) == {'op', 'value'} and b['op'] in ops, 'CONDITION_BOUND')
        out[b['op']] = decimal_string(b['value'])
    require(len(out) > 1, 'CONDITION_NEEDS_A_BOUND')
    return out


def cost(value):
    require(type(value) is str and re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]+)?', value) is not None
            and Decimal(value) <= 2, 'COST')
    return value


def duration(raw):
    """Microseconds from {amount, unit}; the amount an integer or a plain decimal string, converted exactly."""
    require(isinstance(raw, dict) and set(raw) == {'amount', 'unit'} and raw['unit'] in TIME_UNITS, 'DURATION')
    amount = raw['amount']
    require((type(amount) is int) or (type(amount) is str and re.fullmatch(r'-?[0-9]+(\.[0-9]+)?', amount)),
            'DURATION_AMOUNT')
    try:
        micros = Decimal(str(amount)) * TIME_UNITS[raw['unit']]
    except InvalidOperation:
        raise QueryError('DURATION_AMOUNT')
    require(micros == micros.to_integral_value() and abs(micros) <= 10 ** 15, 'DURATION_AMOUNT')
    return int(micros)


# ---------------------------------------------------------------- the answer: aggregate counts, checked

def aggregate(out, declared, pruned, windows_placed, timing):
    """Counts only, built field by field from `execution_store.match`'s output; no result is copied."""
    counts = {s: 0 for s in STATUSES}
    for status, n in out['counts'].items():
        counts[status] = int(n)
    reasons, unresolved, notes, used, beside, certainty = {}, {}, {}, {}, {}, {}

    def bump(table, key, by=1):
        table[key] = table.get(key, 0) + by
    for r in out['results']:
        status = r['status']
        if status == 'not_comparable':
            bump(reasons, r['reason'])
        elif status == 'unresolved':
            bump(unresolved, r.get('reason', 'SEARCH_BUDGET_EXHAUSTED'))
            for rid in (r.get('relaxed_match') or {}).get('relaxations', []):
                bump(beside, rid)
        elif status == 'relaxed':
            bump(certainty, r['certainty'])
            for rid in r['relaxations']:
                bump(used, rid)
        if r.get('note'):
            for note in r['note'].split('; '):
                bump(notes, note)
    implicit = out.get('implicit', {})
    candidates = int(out['candidates'])
    # Relaxations by the identifiers the server gave them; the page labels them from what it sent.
    relaxations = [{'id': rid, 'relaxed': used.get(rid, 0), 'beside_unresolved': beside.get(rid, 0)}
                   for rid in sorted(set(declared) | set(used) | set(beside))]
    return {
        'profile': PROFILE,
        'patients': sum(counts.values()),
        'counts': counts,
        'not_comparable_reasons': dict(sorted(reasons.items())),
        'unresolved_reasons': dict(sorted(unresolved.items())),
        'notes': dict(sorted(notes.items())),
        'relaxed_certainty': dict(sorted(certainty.items())),
        'relaxations': relaxations,
        'windows': {'placed': int(windows_placed), 'not_placed': counts['excluded']},
        'candidates': candidates,
        'filtered': int(implicit.get('FILTERED', 0)),
        'pruned': int(pruned),
        'plan': [{'step': p['step'], 'patients': int(p['patients'])} for p in out['plan']],
        'timing': {k: round(v, 3) for k, v in timing.items()},
    }


SCHEMA = {
    'profile': str, 'patients': int, 'counts': 'status_counts', 'not_comparable_reasons': 'code_counts',
    'unresolved_reasons': 'code_counts', 'notes': 'code_counts', 'relaxed_certainty': 'certainty_counts',
    'relaxations': 'relaxations', 'windows': {'placed': int, 'not_placed': int}, 'candidates': int, 'filtered': int,
    'pruned': int, 'plan': 'plan', 'timing': {'match_seconds': float, 'pruning_count_seconds': float,
                                              'total_seconds': float},
}


def check_aggregate(payload, patient_ids):
    """Refuse anything but aggregate counts: the closed SCHEMA, counts as non-negative integers, and the only strings
    the profile and codes from fixed vocabularies (statuses, upper-case reason codes, the server's own relaxation
    identifiers), none of them equal to one of the store's patient identifiers. Nothing the page sent is echoed."""
    def fail(where):
        raise AggregateRuleViolation(where)

    def count(v, where):
        if not (type(v) is int and v >= 0):
            fail(where)

    def counts(d, keys, where):
        if not isinstance(d, dict):
            fail(where)
        for k, v in d.items():
            if not (isinstance(k, str) and keys(k)):
                fail(f'{where}.{k}')
            count(v, f'{where}.{k}')

    def walk(value, shape, where):
        if isinstance(shape, dict):
            if not (isinstance(value, dict) and set(value) == set(shape)):
                fail(where)
            for k, sub in shape.items():
                walk(value[k], sub, f'{where}.{k}')
        elif shape is int:
            count(value, where)
        elif shape is float:
            if not (type(value) in (int, float) and 0 <= value < 86_400):
                fail(where)
        elif shape is str:
            if value != PROFILE:
                fail(where)
        elif shape == 'status_counts':
            counts(value, lambda k: k in STATUSES, where)
            if set(value) != set(STATUSES):
                fail(where)
        elif shape == 'code_counts':
            counts(value, lambda k: CODE.fullmatch(k) is not None and k not in patient_ids, where)
        elif shape == 'certainty_counts':
            counts(value, lambda k: k in ('exact', 'possible'), where)
        elif shape == 'relaxations':
            if not isinstance(value, list):
                fail(where)
            for k, x in enumerate(value):
                if not (isinstance(x, dict) and set(x) == {'id', 'relaxed', 'beside_unresolved'}
                        and isinstance(x['id'], str) and RELAXATION_ID.fullmatch(x['id'])):
                    fail(f'{where}[{k}]')
                count(x['relaxed'], f'{where}[{k}].relaxed')
                count(x['beside_unresolved'], f'{where}[{k}].beside_unresolved')
        elif shape == 'plan':
            if not isinstance(value, list):
                fail(where)
            for k, x in enumerate(value):
                if not (isinstance(x, dict) and set(x) == {'step', 'patients'} and x['step'] in STEP_IDS):
                    fail(f'{where}[{k}]')
                count(x['patients'], f'{where}[{k}].patients')
        else:
            fail(where)
    walk(payload, SCHEMA, 'answer')
    return payload
