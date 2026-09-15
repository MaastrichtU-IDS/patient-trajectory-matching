"""Fixed-witness possible/certain cohort matching over bounded integer timelines."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
from itertools import product
import json
from pathlib import Path

from . import bounded_intervals as bt
from . import exact_intervals as ei
from .temporal_stn import Edge, Network


def query_edges(binding, constraints, variables):
    edges, incomparable = [], []
    for c in constraints:
        left, right = binding[c['left']], binding[c['right']]
        if variables[left['start_var']]['clock_id'] != variables[right['start_var']]['clock_id']:
            incomparable.append(c['id'])
            continue
        s, e, u, v = left['start_var'], left['end_var'], right['start_var'], right['end_var']
        if c['operator'] == 'before':
            atoms = [(e, u, -1)]
        elif c['operator'] == 'meets':
            atoms = [(e, u, 0), (u, e, 0)]
        elif c['operator'] == 'overlaps':
            atoms = [(s, u, -1), (u, e, -1), (e, v, -1)]
        else:
            atoms = [(e, u, -c['min_gap_us']), (u, e, c['max_gap_us'])]
        edges.extend(Edge('query:' + c['id'] + ':' + str(i), a, b, limit)
                     for i, (a, b, limit) in enumerate(atoms))
    return edges, incomparable


def classify(source_network, edges, incomparable):
    proofs, unentailed = [], []
    for edge in edges:
        bound, path = source_network.bound(edge.left, edge.right)
        if bound is not None and bound <= edge.upper:
            proofs.append({'query_edge_id': edge.id, 'entailed_upper_us': bound, 'source_path': path})
        else:
            unentailed.append(edge)
    # Reuse source closure when every comparable query atom is already entailed.
    combined = (Network(source_network.names, list(source_network.edges) + edges)
                if unentailed else source_network)
    result = {'query_edges': [asdict(e) for e in edges], 'incomparable_constraints': incomparable}
    if not combined.feasible:
        return {**result, 'status': 'IMPOSSIBLE', 'possible': False, 'certain': False,
                'negative_cycle': combined.negative_cycle}
    if incomparable:
        return {**result, 'status': 'INCOMPARABLE', 'possible': None, 'certain': None,
                'reason_codes': ['CLOCK_MISMATCH'], 'comparable_part_witness': combined.witness()}
    result.update(possible=True, possible_witness=combined.witness())
    if not unentailed:
        return {**result, 'status': 'CERTAIN', 'certain': True, 'entailment_proofs': proofs}
    edge = unentailed[0]
    negation = Edge('counterexample:' + edge.id, edge.right, edge.left, -edge.upper - 1)
    # Certainty is tested against SOURCE alone, never source narrowed by Q.
    counter = Network(source_network.names, list(source_network.edges) + [negation])
    assert counter.feasible
    return {**result, 'status': 'POSSIBLE', 'certain': False,
            'counterexample': counter.witness(), 'violated_query_edge_id': edge.id,
            'counterexample_edge': asdict(negation)}


def execute(snapshot, query):
    bt.validate(query, query=True)
    types = {kind: bt.selected_classes({'event_kind': kind}) for kind in bt.KINDS}
    return _execute_supported(snapshot, query, {e['id']: types[e['event_kind']]
                                               for e in snapshot.events.values()})


def _execute_supported(snapshot, query, supported_classes):
    """Internal join over validated, complete class support supplied by an entry point.

    Public callers use execute or semantic_support.execute; this function does
    not validate semantic evidence and is not a serialized support-table API.
    """
    groups = defaultdict(list)
    for event in snapshot.events.values():
        groups[bt.scope(event)].append(event)
    trajectories = []
    for scope, events in sorted(groups.items()):
        candidates = {s['id']: sorted((e for e in events if s['class_iri'] in supported_classes[e['id']]), key=lambda e: e['id'])
                      for s in query['slots']}
        names = sorted(candidates)
        bindings = []
        source = snapshot.networks[scope]
        for values in product(*(candidates[name] for name in names)):
            if len({e['id'] for e in values}) != len(values):
                continue
            binding = dict(zip(names, values))
            edges, incomparable = query_edges(binding, query['constraints'], snapshot.variables)
            answer = classify(source, edges, incomparable)
            answer['slots'] = {name: {'event_id': event['id'], 'evidence_id': snapshot.evidence[event['id']]['evidence_id'],
                                     'selected_class_iri': next(s['class_iri'] for s in query['slots'] if s['id'] == name)}
                               for name, event in binding.items()}
            bindings.append(answer)
        statuses = {b['status'] for b in bindings}
        status = next((out for inside, out in [('CERTAIN', 'CERTAIN_MATCH'), ('POSSIBLE', 'POSSIBLE_MATCH'),
                                              ('INCOMPARABLE', 'INCOMPARABLE')] if inside in statuses), 'NO_RECORDED_MATCH')
        trajectories.append({'patient_id': scope[0], 'episode_id': scope[1], 'status': status,
                             'source_edges': [asdict(e) for e in source.edges], 'bindings': bindings})
    context = {'profile': bt.QUERY_PROFILE, 'source_context_id': snapshot.context_id, 'query': query,
               'artifacts': {p: hashlib.sha256((ei.ROOT / p).read_bytes()).hexdigest() for p in (
                   'patterns/bounded_cohort.py', 'patterns/bounded_reference.py')}}
    return {'profile': bt.QUERY_PROFILE, 'context_id': ei.digest(ei.canonical(context)), 'context': context,
            'search_complete': True, 'scope': 'represented_patient_episodes',
            'certainty_semantics': 'exists_named_binding_forall_feasible_source_timelines',
            'certain_patient_ids': sorted({t['patient_id'] for t in trajectories if t['status'] == 'CERTAIN_MATCH'}),
            'possible_patient_ids': sorted({t['patient_id'] for t in trajectories if t['status'] in ('CERTAIN_MATCH', 'POSSIBLE_MATCH')}),
            'trajectories': trajectories, 'evidence': {'source_context': snapshot.context, 'source': snapshot.source,
                                                     'bindings': snapshot.evidence, 'edges': snapshot.edge_evidence}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    example = ei.ROOT / 'examples/bounded-interval'
    parser.add_argument('--source', type=Path, default=example / 'source.json')
    parser.add_argument('--query', type=Path, default=example / 'query.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/bounded-interval-run')
    args = parser.parse_args()
    try:
        query = bt.validate(json.loads(args.query.read_text()), query=True)
        snapshot = bt.prepare(json.loads(args.source.read_text()))
        result = execute(snapshot, query)
    except bt.InconsistentSource as error:
        parser.exit(2, json.dumps(error.details) + '\n')
    except (ei.ContractError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'graph.ttl').write_text(ei.turtle_text(snapshot.graph))
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'certain_patient_ids': result['certain_patient_ids'],
                      'possible_patient_ids': result['possible_patient_ids'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
