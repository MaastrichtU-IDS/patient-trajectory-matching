"""Versioned exact interval queries over one validated PRO/SOLID snapshot."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
import json
import hashlib
from pathlib import Path

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, RDF, URIRef, XSD

from . import exact_intervals as ei
from . import interval_cohort_reference as reference

PROFILE_ID = 'interval-cohort-1.0'
SCHEMA = ei.ROOT / 'schemas/interval-cohort.schema.json'
SELECTORS = {str(c) for c in (ei.EI.Infusion, ei.EI.SpecimenCollection, ei.EI.IntervalProcess, ei.S.Process)}


def build_graph(source):
    """Use the exact synthetic adapter with this profile's source locator."""
    graph = ei.build_graph(source)
    for node in graph.subjects(RDF.type, ei.EX.SourceLocation):
        graph.set((node, ei.S.hasValue, Literal('constructed://interval-cohort/source-rows.json',
                                               datatype=XSD.string, normalize=False)))
    return graph


def validate_query(query):
    errors = sorted(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(query),
                    key=lambda error: str(list(error.path)))
    ei.require(not errors, 'INVALID_QUERY_SCHEMA: ' + '; '.join(e.message for e in errors))
    names = [slot['id'] for slot in query['slots']]
    ei.require(len(names) == len(set(names)), 'DUPLICATE_SLOT')
    ids = [c['id'] for c in query['constraints']]
    ei.require(len(ids) == len(set(ids)), 'DUPLICATE_CONSTRAINT')
    for constraint in query['constraints']:
        ei.require(constraint['left'] in names and constraint['right'] in names, 'UNKNOWN_SLOT')
        ei.require(constraint['left'] != constraint['right'], 'SELF_CONSTRAINT')
        if constraint['operator'] == 'gap':
            ei.require(type(constraint['min_gap_us']) is int and type(constraint['max_gap_us']) is int,
                       'INTEGER_MICROSECONDS_REQUIRED')
            ei.require(constraint['min_gap_us'] <= constraint['max_gap_us'], 'INVALID_GAP_BOUNDS')
    return query


@dataclass
class PreparedSnapshot:
    """Internal validated projection; serialized execution records are not an input API."""
    intervals: list
    evidence: dict
    types: dict


def prepare(graph, manifest):
    intervals, evidence = ei.project(graph, manifest)
    view = ei.validate_graph(graph)
    types = {r.process: frozenset(str(c) for c in view.objects(URIRef(r.process), RDF.type)
                                 if str(c) in SELECTORS) for r in intervals}
    return PreparedSnapshot(intervals, evidence, types)


def compare(binding, constraint):
    return ei.evaluate(binding[constraint['left']], binding[constraint['right']], constraint['operator'],
                       min_gap_us=constraint.get('min_gap_us'), max_gap_us=constraint.get('max_gap_us'))


def indexed_search(candidates, constraints):
    """Class candidates, start indexes per clock, and incremental conjunctive joins."""
    indexes = {}
    for name, records in candidates.items():
        by_clock = defaultdict(list)
        for record in records:
            by_clock[record.clock].append(record)
        indexes[name] = {}
        for clock, group in by_clock.items():
            group.sort(key=lambda r: (r.start_us, r.event_id))
            indexes[name][clock] = ([r.start_us for r in group], group)
    # Static smallest-domain order is deterministic; reverse edges still receive
    # incremental predicate checks when both endpoints have been assigned.
    order = sorted(candidates, key=lambda name: (len(candidates[name]), name))
    found = []
    stats = {'complete_bindings_examined': 0, 'candidate_extensions': 0, 'range_lookups': 0}

    def eligible(name, binding):
        allowed = None
        for c in constraints:
            if c['right'] != name or c['left'] not in binding:
                continue
            a = binding[c['left']]
            current = set()
            for clock, (starts, records) in indexes[name].items():
                if clock != a.clock:
                    # Keep these for an explicit unresolved result; never interpret
                    # a different clock as a failed temporal predicate.
                    current.update(records)
                    continue
                stats['range_lookups'] += 1
                if c['operator'] == 'before':
                    lo, hi = bisect_right(starts, a.end_us), len(starts)
                elif c['operator'] == 'meets':
                    lo, hi = bisect_left(starts, a.end_us), bisect_right(starts, a.end_us)
                elif c['operator'] == 'gap':
                    lo = bisect_left(starts, a.end_us + c['min_gap_us'])
                    hi = bisect_right(starts, a.end_us + c['max_gap_us'])
                else:  # overlap also needs the end predicate, checked below
                    lo, hi = bisect_right(starts, a.start_us), bisect_left(starts, a.end_us)
                current.update(records[lo:hi])
            allowed = current if allowed is None else allowed & current
        return sorted(candidates[name] if allowed is None else allowed, key=lambda r: r.event_id)

    def visit(binding, used, unknown):
        if len(binding) == len(order):
            stats['complete_bindings_examined'] += 1
            found.append((dict(binding), 'INCOMPARABLE' if unknown else 'SATISFIED'))
            return
        name = order[len(binding)]
        for record in eligible(name, binding):
            if record.process in used:
                continue
            stats['candidate_extensions'] += 1
            binding[name] = record
            statuses = [compare(binding, c)['status'] for c in constraints
                        if name in (c['left'], c['right']) and c['left'] in binding and c['right'] in binding]
            if 'NOT_SATISFIED' not in statuses:
                visit(binding, used | {record.process}, unknown or 'INCOMPARABLE' in statuses)
            del binding[name]

    visit({}, set(), False)
    return found, stats


def execute(snapshot, query, *, engine='indexed'):
    validate_query(query)
    ei.require(engine in ('indexed', 'reference'), 'UNKNOWN_ENGINE')
    # Validate pair subtraction range once per clock so index pruning cannot hide
    # an arithmetic error that exhaustive evaluation would encounter.
    groups = defaultdict(list)
    for record in snapshot.intervals:
        groups[(record.patient_id, record.patient_bearer, record.episode_id)].append(record)
    trajectories, totals = [], defaultdict(int)
    for scope, records in sorted(groups.items()):
        clocks = defaultdict(list)
        for record in records:
            clocks[record.clock].extend((record.start_us, record.end_us))
        for coordinates in clocks.values():
            ei.checked_us(max(coordinates) - min(coordinates))
        candidates = {slot['id']: [r for r in records if slot['class_iri'] in snapshot.types[r.process]]
                      for slot in query['slots']}
        search = indexed_search if engine == 'indexed' else reference.search
        found, stats = search(candidates, query['constraints'])
        for name, value in stats.items():
            totals[name] += value
        matches, unresolved = [], []
        for binding, status in found:
            slots = {name: {'event_id': r.event_id, 'process': r.process, 'patient_role': r.patient_role,
                            'patient_bearer': r.patient_bearer, 'evidence_id': r.evidence_id,
                            'selected_class_iri': next(s['class_iri'] for s in query['slots'] if s['id'] == name)}
                     for name, r in sorted(binding.items())}
            item = {'slots': slots, 'constraints': [{'id': c['id'], **compare(binding, c)}
                                                    for c in query['constraints']]}
            (matches if status == 'SATISFIED' else unresolved).append(item)
        for collection in (matches, unresolved):
            collection.sort(key=ei.canonical)
        trajectories.append({'patient_id': scope[0], 'patient_bearer': scope[1], 'episode_id': scope[2],
                             'status': 'MATCH' if matches else ('INCOMPARABLE' if unresolved else 'NO_RECORDED_MATCH'),
                             'matches': matches, 'unresolved_bindings': unresolved})
    context = {'profile': PROFILE_ID, 'query': query, 'source_context_id': snapshot.evidence['context_id'],
               'artifacts': {path: hashlib.sha256((ei.ROOT / path).read_bytes()).hexdigest() for path in (
                   'patterns/interval_cohort.py', 'patterns/interval_cohort_reference.py',
                   'schemas/interval-cohort.schema.json')}}
    return {'profile': PROFILE_ID, 'query_id': query['id'], 'context_id': ei.digest(ei.canonical(context)),
            'context': context, 'search_complete': True, 'scope': 'represented_patient_episodes',
            'matched_patient_ids': sorted({t['patient_id'] for t in trajectories if t['matches']}),
            'trajectories': trajectories, 'evidence': snapshot.evidence,
            'execution': {'engine': engine, **totals}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    example = ei.ROOT / 'examples/interval-cohort'
    parser.add_argument('--source', type=Path, default=example / 'source-rows.json')
    parser.add_argument('--graph', type=Path)
    parser.add_argument('--manifest', type=Path, default=example / 'manifest.json')
    parser.add_argument('--query', type=Path, default=example / 'query.json')
    parser.add_argument('--engine', choices=['indexed', 'reference'], default='indexed')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/interval-cohort-run/result.json')
    args = parser.parse_args()
    try:
        query = validate_query(json.loads(args.query.read_text()))
        graph = Graph().parse(args.graph, format='turtle') if args.graph else build_graph(json.loads(args.source.read_text()))
        result = execute(prepare(graph, json.loads(args.manifest.read_text())), query, engine=args.engine)
    except (ei.ContractError, json.JSONDecodeError, OSError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'profile': PROFILE_ID, 'matched_patient_ids': result['matched_patient_ids'],
                      'trajectories': len(result['trajectories']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
