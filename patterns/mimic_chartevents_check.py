"""Graph-route checks for chartevents route (b): a sample (or a census) of claim stores through the
measurement graph route, with a row-equality differential against the bulk rows (D16, D27).

Route (a) for a measurement claim store is the repository's measurement graph route:
- the store, as the importer builds it, validated (`measurement_claims.validate_store`);
- encoded as a closed claim-description graph (`claim_rdf.encode`, measurement fields);
- accepted by a caller-declared accept-all analysis policy (not clinical review), bound to the store's
  digest and the fixed empty measurement semantic policy;
- selected by `measurement_claims.select` from the graph. It decodes the graph (so the policy's store
  digest only matches if the round trip gave the store back exactly) and runs the importer's isolation
  certificate (`claim_isolation.check(..., local=True, measurement=True)`) on the decoded store; each
  selected record carries its clock, its time variable's offsets from the clock origin and its claim
  support (claim digest). The isolation result is read from the selection, not computed twice.

**What this check exercises independently, and what it does not.** The route-(a) row is built by
`record_fields` from the selected record and then by the *same* `store_row` as route (b), with the same
`value_columns`, `provenance_of`, `micros` and item namespace, from the *same* `describe()` claim. So the
differential tests: `claim_fields` against `record_fields` (reading a claim dict against reading a
selected record); the RDF encode and decode round trip; validation, isolation and selection accepting
the store; the selection's own time offsets (`patient_local.coordinate` inside the selection) against
route (b)'s; and the selection's decimal rendering against `value_columns`. It does not test the shared
mapping: a defect in `micros`, the micro-unit value, the recorded time or the item namespace reaches
both routes alike and passes. `patterns/mimic_chartevents_raw_check.py` is the independent check of
that mapping, required over every chart row.

Sample (`Sampler`, the design for full MIMIC-IV): every store has a rank, the sha256 of the sample salt,
the dataset and its key (patient, stay, part). The key's part is D15's cut of a stay's admitted rows into
claim stores of 32, so changing that cut changes keys and the sample. A store is sampled if (1) its rank
falls below `rate` (a Bernoulli draw fixed by the hash), (2) it is among the `per_batch` lowest-ranked
stores of its batch (the execution-store bucket of its patient), or (3) it is the lowest-ranked store
holding some stratum (`mimic_chartevents.strata`). Each rule is a function of the whole set of stores,
never of ingest order, shard count or the store builder's part size (D23's `part_rows`). Memory: one
entry per stratum, `per_batch` per bucket, and the rate-drawn keys, which grow with rate times stores.

Census (`census`, the design for the demo): the round trip (`round_trip`) on every store, route (a) on
every store when asked, and the raw-row check over every row.
"""
from __future__ import annotations

import hashlib
import heapq
import json
from collections import Counter
from pathlib import Path

from . import claim_projection as cp
from . import claim_rdf as cr
from . import execution_store_reference as ref
from . import measurement_claims as claims
from . import mimic_chartevents as chart
from . import mimic_chartevents_raw_check as raw_check

PROFILE = 'mimic-chartevents-graph-check-1.1'
SALT = 'chartevents-graph-sample-1'
RATE = 0.01
PER_BATCH = 2
POLICY_REASON = 'Caller-declared accept-all analysis policy over pending source records; no clinical review.'
FILES = tuple(sorted(set(('patterns/mimic_chartevents_check.py', 'patterns/mimic_chartevents.py',
                          'patterns/mimic_chartevents_raw_check.py') + claims.FILES + chart.FILES)))
ROUTE_STEPS = ('measurement_claims.validate_store', 'claim_rdf.encode:measurement_fields',
               'accept_all:' + chart.POLICY_ID,
               'measurement_claims.select:from_graph(decode, isolation local+measurement)',
               'record_fields', 'mimic_chartevents.store_row (shared with route b)')
STATEMENT = ('Bulk rows are built from describe() claim identity and tied to the graph by it. Claim stores sent '
             'through the measurement graph route gave rows equal field for field; that comparison shares the row '
             'mapping with the bulk route, which the raw-row check verifies independently over every chart row. '
             'Rows of stores outside a sample were not each validated through the graph.')


class CheckError(ValueError):
    """The check refuses; the message is a stable code."""


def require(condition, code):
    if not condition:
        raise CheckError(code)


# ---------------------------------------------------------------- the sample

def rank(dataset_id, key, salt=SALT):
    pid, stay, part = key
    text = '\t'.join((salt, dataset_id, pid, stay, str(part)))
    return int.from_bytes(hashlib.sha256(text.encode('utf-8')).digest(), 'big')


def batch_of(key):
    """The store bucket of the store's patient (as the execution store assigns it)."""
    return ref.bucket_of(key[0])


class Sampler:
    """Streaming, order-free selection of the stores to send through route (a)."""

    def __init__(self, dataset_id, rate=RATE, per_batch=PER_BATCH, salt=SALT, stratify=True):
        require(0 <= rate <= 1 and per_batch >= 0, 'INVALID_SAMPLE_PARAMETERS')
        self.dataset_id, self.rate, self.per_batch, self.salt, self.stratify = dataset_id, rate, per_batch, salt, stratify
        self.threshold = int(rate * (1 << 256))
        self.drawn, self.batches, self.cover = set(), {}, {}
        self.offered = 0

    def offer(self, key, strata=()):
        r = rank(self.dataset_id, key, self.salt)
        self.offered += 1
        if r < self.threshold:
            self.drawn.add(key)
        if self.per_batch:
            heap = self.batches.setdefault(batch_of(key), [])
            item = (-r, key)  # a max-heap of the per_batch lowest ranks
            if len(heap) < self.per_batch:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
        if self.stratify:
            for s in strata:
                s = tuple(s)
                if s not in self.cover or (r, key) < self.cover[s]:
                    self.cover[s] = (r, key)

    def selected(self):
        """{store key: sorted reasons}: `rate`, `batch_minimum`, `stratum`."""
        out = {}
        for key in self.drawn:
            out.setdefault(key, set()).add('rate')
        for heap in self.batches.values():
            for _, key in heap:
                out.setdefault(key, set()).add('batch_minimum')
        for _, key in self.cover.values():
            out.setdefault(key, set()).add('stratum')
        return {k: sorted(v) for k, v in sorted(out.items())}

    def summary(self):
        chosen = self.selected()
        return {'stores_offered': self.offered, 'stores_sampled': len(chosen),
                'by_reason': dict(sorted(Counter(r for rs in chosen.values() for r in rs).items())),
                'strata': len(self.cover), 'batches': len(self.batches),
                'rule': {'salt': self.salt, 'rate': self.rate, 'per_batch': self.per_batch,
                         'batch': 'execution-store bucket of the patient', 'stratified': self.stratify}}


# ---------------------------------------------------------------- route (a)

def accept_all(store):
    """The caller-declared accept-all analysis policy over one measurement claim store."""
    policy = {'profile': cp.POLICY_PROFILE, 'id': chart.POLICY_ID, 'store_sha256': cr.digest(store),
              'semantic_policy_sha256': cr.digest(claims.SEMANTIC_POLICY),
              'decisions': [{'id': 'accept_' + c['id'], 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
                             'action': 'accept', 'supersedes': None, 'reason': POLICY_REASON}
                            for c in store['claims']]}
    cp.validate_policy(store, policy, claims.SEMANTIC_POLICY)
    return policy


def round_trip(store):
    """The cheap check for every store: validate, encode, decode, and the decoded store's digest equal."""
    claims.validate_store(store)
    decoded = cr.decode(cr.encode(store, fields=cr.MEASUREMENT_FIELDS), fields=cr.MEASUREMENT_FIELDS)
    return cr.digest(decoded) == cr.digest(store)


def record_fields(record):
    """Route (a): the fields a store row needs, read from one selected measurement record."""
    event, variable, clock = record['event'], record['time_variable'], record['clock']
    supports = record['event_supports']
    require(len(supports) == 1 and supports[0]['assertion_id'] == 'claim_' + event['id'], 'CLAIM_SUPPORT_MISMATCH')
    require(record['time_supports'] == supports, 'TIME_SUPPORT_MISMATCH')
    return {'patient_id': event['patient_id'], 'episode_id': event['episode_id'], 'event_id': event['id'],
            'record_id': event['record_id'], 'source_key': event['source_key'], 'record_status': event['status'],
            'event_kind': event['event_kind'], 'time_var': event['time_var'], 'item_id': event['item_id'],
            'value_lexical': event['value_lexical'], 'unit_lexical': event['unit_lexical'],
            'claim_id': supports[0]['assertion_id'], 'claim_sha256': supports[0]['claim_sha256'],
            'source_record': supports[0]['source_record_id'], 'clock_id': variable['clock_id'],
            'clock_origin': clock['origin'], 'clock_origin_us': chart.micros(clock['origin']),
            'start_offset_us': [record['lower_us'], record['upper_us']]}


def graph_rows(store, provenance):
    """One claim store through route (a); its store rows in claim order, and the route's facts."""
    claims.validate_store(store)
    graph = cr.encode(store, fields=cr.MEASUREMENT_FIELDS)
    view = claims.select(graph, accept_all(store))  # decodes the graph and runs the isolation certificate
    require(view['status'] == 'SELECTED_MEASUREMENT_RECORDS', 'GRAPH_ROUTE_NOT_SELECTED:' + view['status'])
    records = {r['event']['id']: r for r in view['records']}
    require(len(records) == len(store['claims']), 'GRAPH_ROUTE_RECORD_COUNT')
    rows = []
    for claim in store['claims']:
        record = records[claim['bundle']['events'][0]['id']]
        fields = record_fields(record)
        # The decimal the selection renders is the value the series row keeps.
        require(record['numeric_value'] == chart.value_columns(fields['value_lexical'])[0], 'GRAPH_DECIMAL_MISMATCH')
        rows.append(chart.store_row(fields, provenance[fields['claim_id']]))
    isolation = view['isolation'] or {}
    return rows, {'selection_context_id': view['context_id'], 'isolation_status': isolation.get('status'),
                  'axioms': isolation.get('logical_axioms_checked')}


def compare(bulk, graph):
    """Row-equality differential: (equal, names of differing fields). Values are never reported."""
    if len(bulk) != len(graph):
        return False, ['row_count']
    fields = set()
    for (be, bs), (ge, gs) in zip(bulk, graph):
        for name in sorted(set(be) | set(ge)):
            if name == 'evidence':
                fields |= {'evidence.' + k for k in set(be['evidence']) | set(ge['evidence'])
                           if be['evidence'].get(k) != ge['evidence'].get(k)}
            elif be.get(name) != ge.get(name):
                fields.add('event.' + name)
        fields |= {'series.' + k for k in set(bs) | set(gs) if bs.get(k) != gs.get(k)}
    return not fields, sorted(fields)


EVENT_COLUMNS = ('event_id', 'kind', 'source', 'clock_id', 'start_lo', 'start_hi', 'end_lo', 'end_hi', 'ongoing',
                 'start_var', 'end_var', 'proper', 'status', 'selection_state', 'recorded_at', 'available_at',
                 'claim_id', 'claim_sha256', 'source_table', 'record_sha256', 'file_sha256')
SERIES_COLUMNS = ('item_class', 'start_lo', 'start_hi', 'value_lexical', 'value_decimal', 'value_micro', 'unit')


def stored_form(event, series, clock_id):
    """One route row as the store holds it (D26 typed columns, evidence as canonical text, direct concepts, the D27
    series columns), so graph rows can be compared with the rows read back from a built store."""
    end = event.get('end') or [None, None]
    values = {'event_id': event['event_id'], 'kind': event['kind'], 'source': event['source'], 'clock_id': clock_id,
              'start_lo': event['start'][0], 'start_hi': event['start'][1], 'end_lo': end[0], 'end_hi': end[1],
              'ongoing': end[0] is not None and end[1] is None, 'start_var': event.get('start_var'),
              'end_var': event.get('end_var'), 'proper': bool(event.get('proper', False))}
    values.update({f: event.get(f) for f in EVENT_COLUMNS[12:]})
    evidence = json.dumps(event['evidence'], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    series_values = None if series is None else [series['item_class'], event['start'][0], event['start'][1]] + [
        series[c] for c in SERIES_COLUMNS[3:]]
    return [[values[c] for c in EVENT_COLUMNS], evidence, sorted(event['concepts']), series_values]


def store_forms(store, patient_id, event_ids):
    """{event_id: stored form} read back from a built store for one patient's events."""
    found = store.query('SELECT patient_idx, bucket FROM patients WHERE patient_id = ?', [patient_id])
    if not found:
        return {}
    (idx, bucket), = found
    wanted = sorted(set(event_ids))
    store.db.execute('CREATE OR REPLACE TEMP TABLE wanted_events (event_id VARCHAR)')
    store.db.executemany('INSERT INTO wanted_events VALUES (?)', [[e] for e in wanted])
    cols = ', '.join('e.' + c for c in EVENT_COLUMNS)
    rows = store.query(f"""SELECT {cols}, x.evidence, s.item_class, s.start_lo, s.start_hi, s.value_lexical,
            s.value_decimal, s.value_micro, s.unit, s.event_id IS NOT NULL
        FROM events e JOIN wanted_events w USING (event_id)
        JOIN event_evidence x ON x.bucket = e.bucket AND x.patient_idx = e.patient_idx AND x.event_id = e.event_id
        LEFT JOIN series s ON s.bucket = e.bucket AND s.patient_idx = e.patient_idx AND s.event_id = e.event_id
        WHERE e.bucket = ? AND e.patient_idx = ?""", [bucket, idx])
    concepts = {}
    for eid, concept in store.query("""SELECT c.event_id, c.concept FROM event_concepts c JOIN wanted_events w USING (event_id)
                                       WHERE c.bucket = ? AND c.patient_idx = ?""", [bucket, idx]):
        concepts.setdefault(eid, []).append(concept)
    store.db.execute('DROP TABLE wanted_events')
    n = len(EVENT_COLUMNS)
    return {r[0]: [list(r[:n]), r[n], sorted(concepts.get(r[0], [])), list(r[n + 1:n + 8]) if r[n + 8] else None]
            for r in rows}


def forms_digest(forms):
    """sha256 over stored forms in event order."""
    return hashlib.sha256(json.dumps(sorted(forms, key=lambda f: f[0][0]), ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def check_patient(run, pid, rows, selected, recorded_digests, graph=True, collect=False):
    """The round trip, and route (a) unless `graph` is false, on this patient's selected stores, against
    bulk rows rebuilt from the same rows.

    `recorded_digests` maps each store key to the digest of the bulk rows the converter emitted, so the
    rows compared are shown to be re-derivable: the same rows the converter produced. (That they are the
    rows the store holds is for integration to show, against the built store.)
    """
    out = []
    for key, item in sorted(run.claim_stores(pid, rows, set(selected)).items()):
        bulk = chart.bulk_rows(item['chunk'], item['origin'], pid, run.dataset_id)
        rederived = chart.rows_digest(bulk) == recorded_digests.get(key)
        outcome = {'key': key, 'claims': len(item['chunk']), 'round_trip': round_trip(item['store']),
                   'bulk_rows_rederived': rederived, 'reasons': selected[key], 'graph_checked': graph}
        if graph:
            rows_a, facts = graph_rows(item['store'], item['provenance'])
            equal, fields = compare(bulk, rows_a)
            outcome.update(equal=equal and rederived and outcome['round_trip'], differing_fields=fields,
                           isolation_status=facts['isolation_status'])
            if collect:  # for the built-store comparison (§3.6) and the route-(a) engine input of the differential
                outcome['stored_digest'] = forms_digest([stored_form(e, x, 'patient_' + pid) for e, x in rows_a])
                outcome['event_ids'] = [e['event_id'] for e, _ in rows_a]
                outcome['engine_events'] = [(e['event_id'], x['item_class'], e['start'][0], e['start'][1])
                                            for e, x in rows_a]
        else:
            outcome.update(equal=rederived and outcome['round_trip'], differing_fields=[], isolation_status=None)
        out.append(outcome)
    require(len(out) == len(selected), 'SAMPLED_STORE_NOT_FOUND')
    return out


_WORKER = {}


def _init_worker(input_dir, dataset_id):
    _WORKER['run'] = chart.Run(input_dir, dataset_id)


def _job(args):
    pid, rows, selected, digests, graph = args
    return check_patient(_WORKER['run'], pid, rows, selected, digests, graph=graph, collect=True)


def check_selected(run, selected, digests, work_dir, *, graph=True, workers=1, shards=16):
    """The round trip, and route (a) unless `graph` is false, on every selected store (a sample, or every store for a
    census), patient by patient, in `workers` processes; outcomes with each store's stored-form digest, its event
    ids and its route-(a) engine events."""
    from concurrent.futures import ProcessPoolExecutor
    if not selected:
        return []
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    paths = chart.spill(run.paths['chartevents'], run.manifest, work_dir, shards, {k[0] for k in selected})
    by_patient = {}
    for key, reasons in selected.items():
        by_patient.setdefault(key[0], {})[key] = reasons

    def jobs():
        for pid, rows in chart.patient_rows(paths, run.manifest['columns']):
            mine = by_patient.get(pid)
            if mine:
                yield pid, rows, mine, {k: digests.get(k) for k in mine}, graph

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(run.input_dir, run.dataset_id)) as pool:
            results = list(pool.map(_job, jobs(), chunksize=1))
    else:
        results = [check_patient(run, pid, rows, mine, d, graph=g, collect=True) for pid, rows, mine, d, g in jobs()]
    return [o for r in results for o in r]


def summarise(outcomes, sampler=None):
    differing = Counter(f for o in outcomes for f in o['differing_fields'])
    graphed = [o for o in outcomes if o['graph_checked']]
    return {'profile': PROFILE, 'route_steps': list(ROUTE_STEPS),
            'sample': sampler.summary() if sampler else 'census: every claim store',
            'stores_round_tripped': len(outcomes), 'round_trip_failures': sum(not o['round_trip'] for o in outcomes),
            'stores_graph_checked': len(graphed), 'claims_graph_checked': sum(o['claims'] for o in graphed),
            'stores_equal': sum(o['equal'] for o in outcomes),
            'stores_not_rederived': sum(not o['bulk_rows_rederived'] for o in outcomes),
            'mismatched_stores': sum(not o['equal'] for o in outcomes),
            'differing_fields': dict(sorted(differing.items())),
            'isolation_statuses': dict(sorted(Counter(str(o['isolation_status']) for o in graphed).items())),
            'statement': STATEMENT}


# ---------------------------------------------------------------- runs

def patient_subset(patients, n, salt=SALT):
    """The n patients of lowest sha256(salt, id): a deterministic subset for small runs."""
    return set(sorted(patients, key=lambda p: hashlib.sha256((salt + '\t' + p).encode('utf-8')).digest())[:n])


def _phase_one(run, work, shards, chosen, offer):
    totals, checker = Counter(), raw_check.RawRowCheck()
    for batch in run.batches(work, shards, chosen):
        totals.update(batch['counts'])
        totals['excluded_records'] += len(batch['patient'].get('excluded', []))
        totals['coverage_periods'] += len(batch['patient']['coverage'])
        checker.add(batch['patient'], batch['series'])
        for s in batch['stores']:
            offer(s)
    return totals, checker


def _counts(run, totals):
    return {'source_rows_in_file': run.manifest['rows'], 'counts': dict(sorted(totals.items())),
            'closure': {'item_classes': sum(b == chart.CONCEPT_BASIS for b in run.bases.values()),
                        'groupings': sum(b == chart.GROUP_BASIS for b in run.bases.values()),
                        'unknown_items': run.unknown_items}}


def sampled_run(input_dir, dataset_id, *, patients=None, rate=RATE, per_batch=PER_BATCH, shards=16, graph=True,
                census=False):
    """Route (b) over the chosen patients, the raw-row check over every one of their rows, and the
    round trip plus route (a) on the sample (or on every store with `census`). Aggregate counts only;
    no digest of row-level data is returned (D17)."""
    import tempfile
    import time
    timings, started = {}, time.perf_counter()
    run = chart.Run(input_dir, dataset_id)
    timings['pass_one_and_dimensions'] = time.perf_counter() - started
    chosen = None if patients is None else patient_subset(run.patients(), patients)
    sampler, digests, keys = Sampler(dataset_id, rate, per_batch), {}, []

    def offer(store):
        sampler.offer(store['key'], store['strata'])
        digests[store['key']] = store['rows_sha256']
        keys.append(store['key'])

    with tempfile.TemporaryDirectory() as work:
        t = time.perf_counter()
        totals, checker = _phase_one(run, work, shards, chosen, offer)
        timings['route_b_bulk'] = time.perf_counter() - t
        t = time.perf_counter()
        raw = checker.verify(run.paths['chartevents'], run.paths['d_items'])
        timings['raw_row_check'] = time.perf_counter() - t
        selected = {k: ['census'] for k in keys} if census else sampler.selected()
        outcomes = []
        if selected:
            t = time.perf_counter()
            (Path(work) / 'second').mkdir()
            paths = chart.spill(run.paths['chartevents'], run.manifest, Path(work) / 'second', shards,
                                {k[0] for k in selected})
            for pid, rows in chart.patient_rows(paths, run.manifest['columns']):
                mine = {k: v for k, v in selected.items() if k[0] == pid}
                if mine:
                    outcomes += check_patient(run, pid, rows, mine, digests, graph=graph)
            timings['route_a' if graph else 'round_trip_only'] = time.perf_counter() - t
    timings['total'] = time.perf_counter() - started
    return {'profile': PROFILE, 'dataset_id': dataset_id, 'patient_rows_included': False,
            'patients_requested': patients if patients is not None else 'all',
            'patients': len(checker.patients), **_counts(run, totals), 'raw_row_check': raw,
            'graph_check': summarise(outcomes, None if census else sampler),
            'timings_seconds': {k: round(v, 2) for k, v in timings.items()}}


def main(argv=None):
    """A sampled run, or a census, over local files; prints aggregate counts only."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--dataset-id', required=True)
    parser.add_argument('--patients', type=int, help='a deterministic subset of this many patients')
    parser.add_argument('--rate', type=float, default=RATE)
    parser.add_argument('--per-batch', type=int, default=PER_BATCH)
    parser.add_argument('--shards', type=int, default=16)
    parser.add_argument('--census', action='store_true', help='every claim store, not a sample')
    parser.add_argument('--no-graph', action='store_true', help='round trip only, without route (a)')
    args = parser.parse_args(argv)
    result = sampled_run(args.input_dir, args.dataset_id, patients=args.patients, rate=args.rate,
                         per_batch=args.per_batch, shards=args.shards, graph=not args.no_graph, census=args.census)
    print(json.dumps(result, indent=2))
    failed = result['graph_check']['mismatched_stores'] or not result['raw_row_check']['passed']
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
