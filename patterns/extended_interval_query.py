"""Allen, duration and overlap queries over the existing bounded-time source profile."""
import argparse
from collections import defaultdict
from dataclasses import asdict
from itertools import product
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from . import bounded_intervals as bt, bounded_cohort as cohort, exact_intervals as ei
from .temporal_stn import Edge

PROFILE = 'extended-interval-query-1.0'
SCHEMA = ei.ROOT / 'schemas/extended-interval-query.schema.json'
ALLEN = ('before', 'meets', 'overlaps', 'starts', 'during', 'finishes', 'equals',
         'after', 'met_by', 'overlapped_by', 'started_by', 'contains', 'finished_by')


def validate(query):
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(query)
    names = [s['id'] for s in query['slots']]
    ei.require(len(names) == len(set(names)), 'DUPLICATE_SLOT')
    ids = [c['id'] for c in query['constraints']]
    ei.require(len(ids) == len(set(ids)), 'DUPLICATE_CONSTRAINT')
    supported = set().union(*(bt.selected_classes({'event_kind': k}) for k in bt.KINDS))
    ei.require(all(s['class_iri'] in supported for s in query['slots']), 'UNSUPPORTED_SELECTOR')
    for c in query['constraints']:
        refs = [c['slot']] if c['operator'] == 'duration' else [c['left'], c['right']]
        ei.require(set(refs) <= set(names) and len(refs) == len(set(refs)), 'INVALID_SLOT_REFERENCE')
        for key, value in c.items():
            if key.endswith('_us'):
                ei.checked_us(value)
        if c['operator'] in ('duration', 'gap'):
            lo, hi = ('minimum_us', 'maximum_us') if c['operator'] == 'duration' else ('min_gap_us', 'max_gap_us')
            ei.require(c[lo] <= c[hi], 'REVERSED_BOUNDS')
    return query


def compile_edges(binding, constraints, variables):
    edges, incomparable = [], []
    for c in constraints:
        op = c['operator']
        a = binding[c['slot'] if op == 'duration' else c['left']]
        s, e = a['start_var'], a['end_var']
        if op == 'duration':
            atoms = [(s, e, -c['minimum_us']), (e, s, c['maximum_us'])]
        else:
            b = binding[c['right']]
            u, v = b['start_var'], b['end_var']
            if variables[s]['clock_id'] != variables[u]['clock_id']:
                incomparable.append(c['id'])
                continue
            inverse = {'after':'before', 'met_by':'meets', 'overlapped_by':'overlaps',
                       'started_by':'starts', 'contains':'during', 'finished_by':'finishes'}
            if op in inverse:
                op = inverse[op]
                s, e, u, v = u, v, s, e
            relations = {
                'before': [(e,u,-1)], 'meets': [(e,u,0),(u,e,0)],
                'overlaps': [(s,u,-1),(u,e,-1),(e,v,-1)],
                'starts': [(s,u,0),(u,s,0),(e,v,-1)],
                'during': [(u,s,-1),(e,v,-1)],
                'finishes': [(u,s,-1),(e,v,0),(v,e,0)],
                'equals': [(s,u,0),(u,s,0),(e,v,0),(v,e,0)]}
            if op == 'gap':
                atoms = [(e,u,-c['min_gap_us']),(u,e,c['max_gap_us'])]
            elif op == 'minimum_overlap':
                # min(e,v)-max(s,u)>=d iff every end-start pair is >=d.
                atoms = [(start,end,-c['minimum_us']) for start in (s,u) for end in (e,v)]
            else:
                atoms = relations[op]
        edges.extend(Edge('query:'+c['id']+':'+str(i), left, right, upper)
                     for i,(left,right,upper) in enumerate(atoms))
    return edges, incomparable


def execute(snapshot, query):
    validate(query)
    ei.require(snapshot.source['profile'] == bt.PROFILE_ID, 'UNSUPPORTED_SNAPSHOT_PROFILE')
    groups = defaultdict(list)
    for event in snapshot.events.values():
        groups[bt.scope(event)].append(event)
    trajectories = []
    for scope, events in sorted(groups.items()):
        candidates = {s['id']: sorted((e for e in events if s['class_iri'] in
                      bt.selected_classes({'event_kind': e['event_kind']})), key=lambda e:e['id'])
                      for s in query['slots']}
        names = sorted(candidates)
        bindings = []
        for values in product(*(candidates[n] for n in names)):
            if len({e['id'] for e in values}) != len(values):
                continue
            binding = dict(zip(names, values))
            edges, incomparable = compile_edges(binding, query['constraints'], snapshot.variables)
            result = cohort.classify(snapshot.networks[scope], edges, incomparable)
            result['slots'] = {n:{'event_id':e['id'], 'evidence_id':snapshot.evidence[e['id']]['evidence_id']}
                               for n,e in binding.items()}
            bindings.append(result)
        trajectories.append({'patient_id':scope[0], 'episode_id':scope[1],
                             'source_edges':[asdict(e) for e in snapshot.networks[scope].edges], 'bindings':bindings})
    context = {'profile':PROFILE, 'source_context_id':snapshot.context_id, 'query':query,
               'artifacts':{name:ei.digest((ei.ROOT/name).read_text()) for name in
                            ('patterns/extended_interval_query.py','schemas/extended-interval-query.schema.json',
                             'patterns/bounded_cohort.py','patterns/temporal_stn.py')}}
    return {'profile':PROFILE, 'context':context, 'context_id':ei.digest(ei.canonical(context)),
            'search_complete':True, 'scope':'represented_patient_episodes',
            'certainty_semantics':'exists_named_binding_forall_feasible_source_timelines',
            'certain_patient_ids': sorted({t['patient_id'] for t in trajectories if any(b['certain'] for b in t['bindings'])}),
            'possible_patient_ids': sorted({t['patient_id'] for t in trajectories if any(b['possible'] for b in t['bindings'])}),
            'trajectories':trajectories, 'evidence':{'source':snapshot.source, 'source_context':snapshot.context,
                                                  'bindings':snapshot.evidence, 'edges':snapshot.edge_evidence}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--query', type=Path, required=True)
    args = parser.parse_args()
    result = execute(bt.prepare(json.loads(args.source.read_text())), json.loads(args.query.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
