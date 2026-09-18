"""Exact half-open state support, refutation and unknown gaps from explicit assertions."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
from jsonschema import Draft202012Validator
from . import exact_intervals as ei, claim_rdf as cr

SOURCE_PROFILE='state-validity-source-1.0'
QUERY_PROFILE='state-validity-query-1.1'
SCHEMA=ei.ROOT/'schemas/state-validity.schema.json'
FIELDS=cr.FIELDS | frozenset(('assertion_policy_id','records','unit','state_iri','polarity','start_us','end_us','time_us'))


def validate(source, query=None):
    schema=json.loads(SCHEMA.read_text())
    Draft202012Validator(schema).validate(source)
    clocks={c['clock_id']:c for c in source['clocks']}
    ei.require(len(clocks)==len(source['clocks']), 'DUPLICATE_CLOCK')
    for clock in clocks.values():
        ei.normalize(clock['origin'],clock['origin'])
        ei.require(clock['scope']=='global' or re.fullmatch(r'patient:[A-Za-z0-9_-]+',clock['scope']), 'INVALID_CLOCK_SCOPE')
    ids=[r['id'] for r in source['records']]
    ei.require(len(ids)==len(set(ids)), 'DUPLICATE_RECORD')
    if query is not None: Draft202012Validator(schema['$defs']['query']).validate(query)
    for row in source['records'] + ([] if query is None else [query]):
        ei.require(row['clock_id'] in clocks,'UNKNOWN_CLOCK')
        ei.require(clocks[row['clock_id']]['scope'] in ('global','patient:'+row['patient_id']), 'CLOCK_SCOPE_MISMATCH')
        for key,value in row.items():
            if key.endswith('_us'): ei.checked_us(value)
        if 'start_us' in row: ei.require(row['start_us']<row['end_us'],'IMPROPER_INTERVAL')
    return source


def encode(source):
    """Information-object evidence only: no state occurrence or persistence entailment."""
    validate(source)
    return cr.encode(source,fields=FIELDS)


def decode(graph):
    return validate(cr.decode(graph,fields=FIELDS))


def _scope(r):
    return r['patient_id'],r['episode_id'],r['state_iri']


def _conflicts(records):
    """Whole-snapshot contradiction between admitted facts; never clipped to a query window.

    An admitted point fact constrains the state at its instant, so it contradicts an
    opposite-polarity interval containing it and an opposite-polarity fact at the same
    instant. Points are never encoded as zero-duration intervals; each conflict kind
    carries its own extent fields.
    """
    groups=defaultdict(list)
    for r in records: groups[(_scope(r),r['clock_id'])].append(r)
    conflicts=[]
    for rows in groups.values():
        intervals=[r for r in rows if r['kind']=='state_interval']
        points=[r for r in rows if r['kind']=='point_observation']
        for a in intervals:
            if a['polarity']!='positive': continue
            for b in intervals:
                if b['polarity']!='negative': continue
                start,end=max(a['start_us'],b['start_us']),min(a['end_us'],b['end_us'])
                if start<end: conflicts.append({'kind':'interval_overlap','positive_id':a['id'],
                                                'negative_id':b['id'],'start_us':start,'end_us':end})
        for interval in intervals:
            for p in points:
                if interval['polarity']==p['polarity']: continue
                if not interval['start_us']<=p['time_us']<interval['end_us']: continue
                positive,negative=(interval,p) if interval['polarity']=='positive' else (p,interval)
                conflicts.append({'kind':'point_in_interval','positive_id':positive['id'],
                                  'negative_id':negative['id'],'time_us':p['time_us']})
        for a in points:
            if a['polarity']!='positive': continue
            for b in points:
                if b['polarity']=='negative' and a['time_us']==b['time_us']:
                    conflicts.append({'kind':'point_point','positive_id':a['id'],
                                      'negative_id':b['id'],'time_us':a['time_us']})
    return sorted(conflicts,key=lambda r:(r['kind'],r['positive_id'],r['negative_id']))


def _union(segments, statuses):
    result=[]
    for s in segments:
        if s['status'] not in statuses: continue
        if result and result[-1]['end_us']==s['start_us']: result[-1]['end_us']=s['end_us']
        else: result.append({'start_us':s['start_us'],'end_us':s['end_us']})
    return result


def execute(source, query):
    validate(source,query)
    source,query=deepcopy(source),deepcopy(query)
    context={'profile':QUERY_PROFILE,'source_sha256':cr.digest(source),'query':query,
             'assertion_policy_id':source['assertion_policy_id'],
             'artifacts':{p:ei.digest((ei.ROOT/p).read_text()) for p in
                          ('patterns/state_validity.py','schemas/state-validity.schema.json',
                           'patterns/claim_rdf.py','ontology/state-validity-description-profile.ttl')}}
    result={'profile':QUERY_PROFILE,'context':context,'context_id':cr.digest(context),
            'scope':'explicit_state_assertions_in_supplied_snapshot',
            'assertion_policy_verified':False,
            'clinical_truth_verified':False,'state_inference_from_samples':False,
            'source':source,'status':None,'coverage':None,'segments':None,
            'evidence':{r['id']:{'record':r,'evidence_id':cr.digest({'source':context['source_sha256'],'record':r})}
                        for r in source['records']}}
    conflicts=_conflicts(source['records'])
    if conflicts:
        result.update(status='BLOCKED_SOURCE_CONFLICT',conflicts=conflicts)
        return result
    relevant=[r for r in source['records'] if _scope(r)==_scope(query)]
    foreign=[r['id'] for r in relevant if r['clock_id']!=query['clock_id']]
    if foreign:
        result.update(status='INCOMPARABLE',incomparable_record_ids=sorted(foreign))
        return result
    start,end=query['start_us'],query['end_us']
    intervals=[r for r in relevant if r['kind']=='state_interval' and r['start_us']<end and start<r['end_us']]
    cuts=sorted({start,end}|{max(start,r['start_us']) for r in intervals}|{min(end,r['end_us']) for r in intervals})
    segments=[]
    for left,right in zip(cuts,cuts[1:]):
        active=[r for r in intervals if r['start_us']<=left and right<=r['end_us']]
        positive=sorted(r['id'] for r in active if r['polarity']=='positive')
        negative=sorted(r['id'] for r in active if r['polarity']=='negative')
        segments.append({'start_us':left,'end_us':right,'status':'SUPPORTED' if positive else 'REFUTED' if negative else 'UNKNOWN',
                         'positive_record_ids':positive,'negative_record_ids':negative})
    positive=_union(segments,{'SUPPORTED'}); negative=_union(segments,{'REFUTED'}); unknown=_union(segments,{'UNKNOWN'})
    length=lambda intervals:sum(r['end_us']-r['start_us'] for r in intervals)
    # Admitted point facts carry no extent, so they stay outside the segment partition and
    # the duration sum. A surviving negative point refutes a throughout-window claim; any
    # point inside a positive interval was already returned as a conflict above.
    window_points=sorted((r for r in relevant if r['kind']=='point_observation' and start<=r['time_us']<end),
                         key=lambda r:(r['time_us'],r['id']))
    refuting=[r['id'] for r in window_points if r['polarity']=='negative']
    coverage={'positive_intervals':positive,'negative_intervals':negative,'unknown_intervals':unknown,
              'supported_duration_us':length(positive),'refuted_duration_us':length(negative),
              'unknown_duration_us':length(unknown),'window_duration_us':end-start,
              'longest_supported_duration_us':max((r['end_us']-r['start_us'] for r in positive),default=0),
              'continuous_positive_support':not negative and not unknown and not refuting,
              'refuting_point_count':len(refuting),
              'monitoring_completeness':'NOT_ESTABLISHED'}
    result.update(status='VIOLATED' if negative or refuting else 'UNKNOWN' if unknown else 'HOLDS',
                  segments=segments,coverage=coverage,
                  points=[{'id':r['id'],'time_us':r['time_us'],'polarity':r['polarity'],
                           'role':'REFUTING' if r['polarity']=='negative' else 'RETAINED'} for r in window_points],
                  refuting_point_ids=sorted(refuting),
                  point_observation_ids=sorted(r['id'] for r in window_points))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--query',type=Path,required=True)
    parser.add_argument('--graph',type=Path,help='Optional lossless claim-description Turtle output')
    args=parser.parse_args()
    if args.graph is not None:
        ei.require(args.graph.resolve() not in {args.source.resolve(),args.query.resolve()},'OUTPUT_OVERWRITES_INPUT')
    source=json.loads(args.source.read_text()); query=json.loads(args.query.read_text())
    result=execute(source,query)
    if args.graph is not None: args.graph.write_text(ei.turtle_text(encode(source)))
    print(json.dumps(result,indent=2))
    return 2 if result['status']=='BLOCKED_SOURCE_CONFLICT' else 0


if __name__=='__main__': raise SystemExit(main())
