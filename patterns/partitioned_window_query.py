"""Pair-covering exact-record batches with explicit review and complete execution accounting."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from itertools import combinations
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import zlib
from jsonschema import Draft202012Validator
from . import indexed_source_windows as windows, claim_rdf as cr, claim_projection as cp
from . import exact_intervals as ei, measurement_claims as points, local_claim_projection as intervals
from . import mixed_record_query as mixed, source_mixed_query as source_query, source_mixed_reference as reference

PROFILE = 'partitioned-window-query-1.0'
REVIEW_PROFILE = 'partitioned-window-review-1.0'
BLOCK_SIZE = 8
MAX_WINDOW_RECORDS = 128
MAX_BATCHES = 4096
MAX_REVIEW_BYTES = 32 * 1024 * 1024
SCHEMA = ei.ROOT / 'schemas/partitioned-window-review.schema.json'
FILES = tuple(sorted(set(windows.FILES + source_query.FILES + ('patterns/partitioned_window_query.py',
    'schemas/partitioned-window-review.schema.json'))))


def partition_numbers(numbers):
    """Single batch up to 16; otherwise unions of every two distinct disjoint eight-record blocks."""
    ei.require(numbers == sorted(set(numbers)) and all(type(n) is int and n > 0 for n in numbers), 'INVALID_WINDOW_RECORD_NUMBERS')
    ei.require(len(numbers) <= MAX_WINDOW_RECORDS, 'PARTITION_WINDOW_LIMIT')
    if len(numbers) <= 2 * BLOCK_SIZE: return [numbers.copy()]
    blocks = [numbers[i:i+BLOCK_SIZE] for i in range(0,len(numbers),BLOCK_SIZE)]
    return [a+b for a,b in combinations(blocks,2)]


def coverage(numbers, batches):
    expected = set(numbers); actual = set(); pairs = set()
    for batch in batches:
        ei.require(batch == sorted(set(batch)) and set(batch) <= expected and not windows.count_reasons(len(batch)), 'INVALID_PARTITION_BATCH')
        actual.update(batch); pairs.update(combinations(batch,2))
    ei.require(actual == expected and pairs == set(combinations(numbers,2)), 'INCOMPLETE_PARTITION_COVERAGE')
    ei.require(bool(batches), 'MISSING_EMPTY_WINDOW_BATCH')
    return {'all_records_covered':True,'all_distinct_pairs_covered':True,'record_count':len(numbers),
            'unordered_pair_count':len(pairs),'ordered_pair_count':2*len(pairs),'batch_count':len(batches)}


def plan(selection):
    """Internal handoff from windows.run(); no edited external selection documents are ingested."""
    s = selection['summary']
    ei.require(s['full_source_scan_complete'] and s['row_accounting_complete'], 'INCOMPLETE_SOURCE_SELECTION')
    windows.validate(s['context']['request'])
    entries = []; total = 0
    for anchor in selection['anchors']:
        reason = ('INDEX_REFERENCE_DISAGREEMENT' if not anchor['reference_agreement'] else
                  'PARTITION_WINDOW_LIMIT' if len(anchor['measurement_record_numbers']) > MAX_WINDOW_RECORDS else None)
        batches = [] if reason else partition_numbers(anchor['measurement_record_numbers'])
        total += len(batches)
        entries.append({'anchor_id':anchor['id'],'patient_id':anchor['patient_id'],'episode_id':anchor['episode_id'],
                        'window_records':len(anchor['measurement_record_numbers']), 'reason':reason,'numbers':batches})
    if total > MAX_BATCHES:
        for entry in entries:
            entry.update(reason=entry['reason'] or 'PARTITION_GLOBAL_BATCH_LIMIT', numbers=[])
    anchors = []; batches = []
    for entry, original in zip(entries,selection['anchors']):
        proof = coverage(original['measurement_record_numbers'],entry['numbers']) if entry['reason'] is None else None
        own = []
        for numbers in entry['numbers']:
            bid = cr.digest([s['context_id'],entry['anchor_id'],numbers,BLOCK_SIZE])
            batches.append({'id':bid,'anchor_id':entry['anchor_id'],'patient_id':entry['patient_id'],
                            'episode_id':entry['episode_id'],'measurement_record_numbers':numbers})
            own.append(bid)
        anchors.append({k:v for k,v in entry.items() if k != 'numbers'} | {'batch_ids':own,'coverage':proof,
            'status':'PLANNED' if proof else 'BLOCKED_PLAN'})
    context = {'profile':PROFILE,'selection_context':s['context'],'selection_context_id':s['context_id'],
        'limits':{'block_size':BLOCK_SIZE,'window_records':MAX_WINDOW_RECORDS,'batches':MAX_BATCHES},
        'semantics':'exact_independent_source_records_one_literal_treatment_and_measurement_item',
        'anchors':anchors,'batches':batches,'artifacts':{p:ei.digest((ei.ROOT/p).read_text()) for p in FILES}}
    blocked = sum(a['status'] == 'BLOCKED_PLAN' for a in anchors)
    return {'profile':PROFILE,'context':context,'context_id':cr.digest(context),'anchors':anchors,'batches':batches,
        'status':'PLANNED_PENDING_REVIEW' if not blocked else 'BLOCKED_INCOMPLETE_PLAN',
        'summary':{'anchors':len(anchors),'batches':len(batches),'blocked_anchors':blocked,
            'partitioned_anchors':sum(len(a['batch_ids'])>1 for a in anchors),'coverage_verified_anchors':len(anchors)-blocked,
            'max_batch_records':max((len(b['measurement_record_numbers']) for b in batches),default=0),
            'empty_window_anchors':sum(a['window_records']==0 for a in anchors),
            'measurement_memberships':sum(len(b['measurement_record_numbers']) for b in batches),
            'clinical_mapping_verified':False,'accepted_claims':0,'mixed_query_executed':False,'cohort_membership':None}}


def build_batch(selection, planned, batch):
    """Separate partition profile; legacy whole-window export bounds remain unchanged."""
    ei.require(batch in planned['batches'], 'UNKNOWN_PLANNED_BATCH')
    ei.require(not windows.count_reasons(len(batch['measurement_record_numbers'])), 'PARTITION_BATCH_LIMIT')
    request = selection['summary']['context']['request']; dataset = request['dataset_id']; patient = batch['patient_id']
    segment = selection['interval_segments'][batch['anchor_id']]
    manifests = {m['table']:m for m in selection['summary']['context']['source_files']}
    sem = {'profile':'joint-semantic-policy-1.0','id':'source_item_codes','classes':[request['query']['treatment_class_iri']], 'rules':[],'disjoint':[]}
    def store(profile,claims,origin,key,kind):
        return {'profile':profile,'dataset_id':'dataset_'+cr.digest(dataset),
            'snapshot_id':kind+'_'+cr.digest([planned['context_id'],batch['id']]),
            'clocks':[{'clock_id':'patient_'+patient,'scope':'patient:'+patient,'origin':origin.replace(' ','T'),
                       'origin_source_key':key,'policy':windows.local.POLICY}], 'claims':claims}
    io = selection['interval_origins'][patient]
    istore = store(intervals.RECORD_STORE_PROFILE,[windows.inputs.describe(segment,dataset)],io['label'],io['source_key'],'interval')
    intervals.validate_store(istore)
    ipolicy = {'profile':cp.POLICY_PROFILE,'id':'pending_'+cr.digest(istore),'store_sha256':cr.digest(istore),
               'semantic_policy_sha256':cr.digest(sem),'decisions':[]}
    mstore = mpolicy = alignment = None
    if batch['measurement_record_numbers']:
        claims=[]
        for number in batch['measurement_record_numbers']:
            raw = selection['measurement_records'][str(number)]
            record_id = 'chartevents:'+dataset+':'+manifests['chartevents']['csv_sha256']+':'+str(number)
            entry = {'raw':raw,'record_id':record_id,'source':windows.source.evidence('chartevents',number,raw,manifests['chartevents'])}
            claims.append(windows.measurements.describe(entry,dataset))
        mo = selection['measurement_origins'][patient]
        key = 'chartevents:'+dataset+':'+manifests['chartevents']['csv_sha256']+':'+str(mo['record_number'])
        mstore = store(points.PROFILE,claims,mo['label'],key,'measurement'); mpolicy = points.pending_policy(mstore)
        alignment = {'profile':'patient-local-clock-alignment-1.0','id':'alignment_'+batch['id'],
            'interval_store_sha256':cr.digest(istore),'measurement_store_sha256':cr.digest(mstore),'bindings':[]}
    return {'batch_id':batch['id'],'interval_store':istore,'interval_policy':ipolicy,'semantic_policy':sem,
            'measurement_store':mstore,'measurement_policy':mpolicy,'alignment':alignment,'query':deepcopy(request['query'])}


def prepare_review(selection, planned):
    rows=[]
    for descriptor in planned['batches']:
        b=build_batch(selection,planned,descriptor)
        rows.append({k:b[k] for k in ('batch_id','interval_policy','measurement_policy','alignment')})
    return {'profile':REVIEW_PROFILE,'plan_context_id':planned['context_id'],'batches':rows}


def merge_bindings(batch_rows):
    """Deduplicate complete identities; a local missing-followup row cannot erase a followup in another batch."""
    pairs = defaultdict(dict)
    for rows in batch_rows:
        for row in rows:
            identity = tuple(row[:4]); followup = row[4]; value = tuple(row[5:])
            ei.require(followup not in pairs[identity] or pairs[identity][followup] == value, 'CONFLICTING_BATCH_BINDING_VALUE')
            pairs[identity][followup] = value
    result=[]
    for identity, followups in sorted(pairs.items()):
        if len(followups)>1: followups.pop(None,None)
        for fid,value in sorted(followups.items(),key=lambda p:'' if p[0] is None else p[0]):result.append(list(identity)+[fid,*value])
    return result


def _review(selection, planned, review):
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(review)), 'INVALID_PARTITION_REVIEW')
    ei.require(review['plan_context_id'] == planned['context_id'], 'STALE_PARTITION_REVIEW')
    ei.require([r['batch_id'] for r in review['batches']] == [b['id'] for b in planned['batches']], 'PARTITION_REVIEW_COVERAGE_MISMATCH')
    states={}; alignments={}; built=[]
    for descriptor,r in zip(planned['batches'],review['batches']):
        b=build_batch(selection,planned,descriptor)
        for kind,sem in [('interval',b['semantic_policy']),('measurement',points.SEMANTIC_POLICY)]:
            store=b[kind+'_store']; policy=r[kind+'_policy']
            if store is None:
                ei.require(policy is None,'POLICY_WITHOUT_PARTITION_STORE');continue
            ei.require(policy is not None,'MISSING_PARTITION_POLICY')
            selection_result=cp._select_validated(store,policy,sem)
            ei.require(not selection_result['blockers'],'PARTITION_SELECTION_CONFLICT')
            accepted=set(selection_result['accepted_claim_ids'])
            for claim in store['claims']:
                state=(cr.digest(claim),claim['id'] in accepted)
                ei.require(states.setdefault(claim['id'],state)==state,'INCONSISTENT_REPEATED_RECORD_SELECTION')
            b[kind+'_policy']=deepcopy(policy)
        if b['measurement_store'] is None:
            ei.require(r['alignment'] is None,'ALIGNMENT_WITHOUT_PARTITION_STORES')
        else:
            ei.require(r['alignment'] is not None,'MISSING_PARTITION_ALIGNMENT')
            aligned=mixed.validate_alignment(r['alignment'],b['interval_store'],b['measurement_store'])
            state=bool(aligned);patient=descriptor['patient_id']
            ei.require(alignments.setdefault(patient,state)==state,'INCONSISTENT_PARTITION_ALIGNMENT')
        b['alignment']=deepcopy(r['alignment']);built.append(b)
    return built,states


def execute(selection, planned, review, *, timeout_seconds=20):
    ei.require(type(timeout_seconds) in (int,float) and 0 < timeout_seconds <= 60,'INVALID_TIMEOUT')
    built,states=_review(selection,planned,review)
    context={'profile':PROFILE,'plan_context_id':planned['context_id'],'review':deepcopy(review),'timeout_seconds':timeout_seconds,
             'plan_context':planned['context']}
    batches=[]; by_anchor=defaultdict(list)
    for descriptor,b in zip(planned['batches'],built):
        entry={'batch_id':descriptor['id'],'anchor_id':descriptor['anchor_id'],'status':'COMPLETED_BATCH',
               'bindings':[],'mixed_result':None}
        batches.append(entry);by_anchor[descriptor['anchor_id']].append(entry)
        if b['measurement_store'] is None:continue
        try:
            run=mixed.execute(b['interval_store'],b['interval_policy'],b['semantic_policy'],b['measurement_store'],
                              b['measurement_policy'],b['alignment'],b['query'],timeout_seconds=timeout_seconds)
        except ei.ContractError as error:
            entry.update(status='BLOCKED_BATCH',reason=str(error));continue
        entry['mixed_result']=run
        if run['status']!='COMPLETED_RECORD_QUERY':entry['status']='BLOCKED_BATCH';continue
        try:
            rows=source_query.graph_bindings(run); expected_patients=sorted({row[0] for row in rows})
            ei.require(run['certain_patient_ids']==expected_patients==run['possible_patient_ids'],'BATCH_PATIENT_SET_DISAGREEMENT')
        except ei.ContractError as error:
            entry.update(status='BLOCKED_BATCH',reason=str(error));continue
        entry['bindings']=rows
    anchors=[]; all_rows=[]
    request=selection['summary']['context']['request']; dataset=request['dataset_id']
    files={m['table']:m for m in selection['summary']['context']['source_files']}
    def selected(claim):return states.get(claim['id'],(None,False))[1]
    for planned_anchor in planned['anchors']:
        aid=planned_anchor['anchor_id']; entry={'anchor_id':aid,'patient_id':planned_anchor['patient_id'],
            'episode_id':planned_anchor['episode_id'],'batch_ids':planned_anchor['batch_ids'],'bindings':None,'reference_bindings':None}
        anchors.append(entry)
        if planned_anchor['status']!='PLANNED':entry.update(status='BLOCKED_PLAN',reason=planned_anchor['reason']);continue
        own=by_anchor[aid]
        if len(own)!=len(planned_anchor['batch_ids']) or any(b['status']!='COMPLETED_BATCH' for b in own):
            entry['status']='BLOCKED_BATCH_EXECUTION';continue
        segment=selection['interval_segments'][aid]; claim=windows.inputs.describe(segment,dataset)
        source_evidence=segment['source']; interval_id='input_'+source_evidence['csv_sha256']+'_'+str(source_evidence['record_number'])
        raw_i=[{'id':interval_id,'subject_id':segment['patient_id'],'stay_id':segment['icu_stay_id'],
            'itemid':segment['item']['itemid'],'starttime':segment['start']['raw_value'],'endtime':segment['end']['raw_value']}] if selected(claim) else []
        original=next(a for a in selection['anchors'] if a['id']==aid); raw_m=[]
        for number in original['measurement_record_numbers']:
            rid='chart_'+files['chartevents']['csv_sha256']+'_'+str(number)
            if states.get('claim_'+rid,(None,False))[1]:raw_m.append({**selection['measurement_records'][str(number)],'id':rid})
        expected=reference.execute(raw_i,raw_m,segment['item']['itemid'],request['query']);entry['reference_bindings']=expected
        try: observed=merge_bindings(b['bindings'] for b in own)
        except ei.ContractError as error:
            entry.update(status='BLOCKED_BINDING_MERGE',reason=str(error));continue
        if observed!=expected:
            entry.update(status='BLOCKED_REFERENCE_DISAGREEMENT',observed_bindings=observed);continue
        entry.update(status='VERIFIED_RECORD_MATCH' if observed else 'VERIFIED_NO_SELECTED_RECORD_MATCH',bindings=observed)
        all_rows.extend(observed)
    complete=all(a['status'].startswith('VERIFIED_') for a in anchors)
    roster=[]
    for stay in selection['roster']:
        own=[a for a in anchors if a['anchor_id'] in stay['anchor_ids']]
        status=('NO_ADMITTED_ANCHOR' if not own else 'BLOCKED_STAY' if any(not a['status'].startswith('VERIFIED_') for a in own) else
                'VERIFIED_RECORD_MATCH' if any(a['bindings'] for a in own) else 'VERIFIED_NO_SELECTED_RECORD_MATCH')
        roster.append({**stay,'status':status})
    return {'profile':PROFILE,'context':context,'context_id':cr.digest(context),
        'status':'COMPLETED_PARTITIONED_QUERY' if complete else 'BLOCKED_INCOMPLETE_PARTITIONED_QUERY',
        'source_selection_summary':deepcopy(selection['summary']),'partition_plan_summary':deepcopy(planned['summary']),
        'batches':batches,'anchors':anchors,'roster':roster,
        'batch_outcomes':dict(sorted(Counter(b['status'] for b in batches).items())),
        'anchor_outcomes':dict(sorted(Counter(a['status'] for a in anchors).items())),
        'bindings':all_rows if complete else None,'certain_patient_ids':sorted({r[0] for r in all_rows}) if complete else None,
        'possible_patient_ids':sorted({r[0] for r in all_rows}) if complete else None,
        'search_complete_over_selected_records':complete,'unique_accepted_claims':sum(s[1] for s in states.values()),
        'clinical_mapping_verified':False,'clinical_knowledge_status':'UNKNOWN','source_history_verified':False,
        'physical_elapsed_time_verified':False,'causal_effect_estimated':False,'full_mixed_owl_reasoning_verified':False}


def run(folder, request, review=None, *, prepare=False):
    selection=windows.run(folder,request); planned=plan(selection)
    if review is not None:return execute(selection,planned,review)
    return {'plan':planned,'review_template':prepare_review(selection,planned) if prepare else None}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,default=ei.ROOT/'examples/source-mixed-query')
    parser.add_argument('--request',type=Path,default=ei.ROOT/'examples/indexed-source-windows/synthetic-request.json')
    group=parser.add_mutually_exclusive_group();group.add_argument('--prepare-review',action='store_true');group.add_argument('--review',type=Path)
    parser.add_argument('--output',type=Path,default=ei.ROOT/'verification/partitioned-window-query-run/result.json')
    args=parser.parse_args(argv);temporary=None
    try:
        protected={p.resolve() for p in [*windows.inputs.source_paths(args.input_dir).values(),*windows.measurements.paths_for(args.input_dir).values(),args.request]+([args.review] if args.review else [])}
        ei.require(args.output.resolve() not in protected,'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size<=cr.MAX_BYTES,'REQUEST_SIZE_LIMIT')
        if args.review:ei.require(args.review.stat().st_size<=MAX_REVIEW_BYTES,'PARTITION_REVIEW_SIZE_LIMIT')
        result=run(args.input_dir,json.loads(args.request.read_text()),json.loads(args.review.read_text()) if args.review else None,prepare=args.prepare_review)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=args.output.parent,delete=False) as handle:
            temporary=Path(handle.name);json.dump(result,handle,indent=2);handle.write('\n')
        temporary.replace(args.output)
    except (ValueError,OSError,EOFError,csv.Error,zlib.error,sqlite3.Error,RecursionError) as error:
        print(json.dumps({'status':'INVALID_PARTITION_INPUT','reason':str(error)}),file=sys.stderr);return 2
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    status=result.get('status',result.get('plan',{}).get('status'));print(json.dumps({'status':status}))
    return 0 if status in ('PLANNED_PENDING_REVIEW','COMPLETED_PARTITIONED_QUERY') else 2


if __name__=='__main__':raise SystemExit(main())
