"""The sampled graph check for chartevents route (b): a deterministic sample of claim stores through
the measurement graph route, with a row-equality differential against the bulk rows (D16; draft D27).

Route (a) for a measurement claim store is the repository's measurement graph route:
- the store, as the importer builds it, validated (`measurement_claims.validate_store`);
- encoded as a closed claim-description graph (`claim_rdf.encode`, measurement fields) and given the
  importer's isolation certificate (`claim_isolation.check(..., local=True, measurement=True)`);
- accepted by a caller-declared accept-all analysis policy (not clinical review), bound to the store's
  digest and the fixed empty measurement semantic policy;
- selected by `measurement_claims.select` from the graph, which decodes it, so the policy's store digest
  only matches if the graph round trip gave the store back exactly; each selected record carries its
  clock, its time variable's offsets from the clock origin and its claim support (claim digest).

The row a record gives (`record_fields`, then the same `store_row` as route (b)) must equal the bulk row
field for field, for every claim of a sampled store. So bulk rows are tied to the graph by claim
identity and checked on a sample; they are not each validated through the graph.

There is no bounded snapshot for points (the bounded profile requires start + 1 <= end), so the SULO
adapter's `from_claims` and `ingest_rows` do not apply to measurement claims; the graph route ends at
`measurement_claims.select`. The decision note discusses what a point route through the adapter would add.

Sample (`Sampler`): every store has a rank, the sha256 of the sample salt, the dataset and its key
(patient, stay, part). A store is sampled if (1) its rank falls below `rate` (a Bernoulli draw fixed by
the hash), (2) it is among the `per_batch` lowest-ranked stores of its batch (the store bucket of its
patient, so every bucket of the two-level input digest is backed by checked stores), or (3) it is the
lowest-ranked store holding some stratum (`mimic_chartevents.strata`). Each rule is a function of the
whole set of stores, never of ingest order, shard count or part size, and is computed streaming in
memory bounded by the number of strata and batches.
"""
from __future__ import annotations

import hashlib
import heapq
from collections import Counter

from . import claim_isolation
from . import claim_projection as cp
from . import claim_rdf as cr
from . import execution_store_reference as ref
from . import measurement_claims as claims
from . import mimic_chartevents as chart

PROFILE = 'mimic-chartevents-graph-check-1.0'
SALT = 'chartevents-graph-sample-1'
RATE = 0.01
PER_BATCH = 2
POLICY_REASON = 'Caller-declared accept-all analysis policy over pending source records; no clinical review.'
FILES = tuple(sorted(set(('patterns/mimic_chartevents_check.py', 'patterns/mimic_chartevents.py')
                         + claims.FILES + chart.FILES)))
ROUTE_STEPS = ('measurement_claims.validate_store', 'claim_rdf.encode:measurement_fields',
               'claim_isolation.check:local+measurement', 'accept_all:' + chart.POLICY_ID,
               'measurement_claims.select:from_graph', 'mimic_chartevents.store_row')


class CheckError(ValueError):
    """The sampled check refuses; the message is a stable code."""


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
    certificate = claim_isolation.check(graph, local=True, measurement=True)
    view = claims.select(graph, accept_all(store))
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
    return rows, {'selection_context_id': view['context_id'], 'isolation_status': certificate.get('status'),
                  'axioms': certificate.get('logical_axioms_checked')}


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


def check_patient(run, pid, rows, selected, recorded_digests):
    """Route (a) on this patient's sampled stores, against bulk rows rebuilt from the same rows.

    `recorded_digests` maps each store key to the digest of the bulk rows the ingest emitted, so the
    rows compared are shown to be the rows that went to the store. Returns per-store outcomes.
    """
    out = []
    for key, item in sorted(run.claim_stores(pid, rows, set(selected)).items()):
        bulk = chart.bulk_rows(item['chunk'], item['origin'], pid, run.dataset_id)
        same_rows = chart.rows_digest(bulk) == recorded_digests.get(key)
        graph, facts = graph_rows(item['store'], item['provenance'])
        equal, fields = compare(bulk, graph)
        out.append({'key': key, 'claims': len(item['chunk']), 'equal': equal and same_rows,
                    'bulk_rows_match_ingest': same_rows, 'differing_fields': fields, 'reasons': selected[key],
                    'isolation_status': facts['isolation_status']})
    require(len(out) == len(selected), 'SAMPLED_STORE_NOT_FOUND')
    return out


def summarise(outcomes, sampler):
    differing = Counter(f for o in outcomes for f in o['differing_fields'])
    return {'profile': PROFILE, 'route_steps': list(ROUTE_STEPS), 'sample': sampler.summary(),
            'stores_checked': len(outcomes), 'claims_checked': sum(o['claims'] for o in outcomes),
            'stores_equal': sum(o['equal'] for o in outcomes),
            'stores_with_bulk_digest_mismatch': sum(not o['bulk_rows_match_ingest'] for o in outcomes),
            'mismatched_stores': sum(not o['equal'] for o in outcomes),
            'differing_fields': dict(sorted(differing.items())),
            'isolation_statuses': dict(sorted(Counter(str(o['isolation_status']) for o in outcomes).items())),
            'statement': ('Bulk rows are built from describe() claim identity and tied to the graph by it; a '
                          'deterministic sample of claim stores was sent through the measurement graph route and '
                          'its rows compared field for field. Rows outside the sample were not each validated '
                          'through the graph.')}
