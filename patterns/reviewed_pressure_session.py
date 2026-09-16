"""Requery immutable reviewed windows and expose local, source-backed trajectory evidence."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import time
from . import reviewed_source_query as r

p = r.p
PROFILE = 'reviewed-pressure-session-1.0'
FILES = tuple(sorted(set(r.FILES + ('patterns/reviewed_pressure_session.py',))))


def controls(value):
    p.ei.require(isinstance(value,dict) and set(value)=={'threshold','baseline_minutes','followup_minutes'}, 'INVALID_PRESSURE_CONTROLS')
    p.ei.require(isinstance(value['threshold'],str) and len(value['threshold'])<=12, 'INVALID_THRESHOLD')
    threshold = p.points.decimal_value(value['threshold'])
    p.ei.require(0 < threshold <= 300, 'INVALID_THRESHOLD')
    p.ei.require(type(value['baseline_minutes']) is int and 1<=value['baseline_minutes']<=30 and
        type(value['followup_minutes']) is int and 0<=value['followup_minutes']<=120, 'OUTSIDE_REVIEWED_WINDOWS')
    return deepcopy(value)


def fingerprint(folder):
    paths={**p.windows.inputs.source_paths(folder),**p.windows.measurements.paths_for(folder)}
    answer={}
    for table,path in paths.items():
        p.ei.require(path.stat().st_size<=r.audit.MAX_RAW_BYTES,'SOURCE_FILE_SIZE_LIMIT')
        digest=hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b''): digest.update(chunk)
        answer[table]=digest.hexdigest()
    return answer


class Session:
    def __init__(self, folder, request, declaration, progress=lambda **kw:None):
        self.folder=folder; before=fingerprint(folder); progress(stage='Auditing source records')
        self.selection,self.planned,self.package,self.audit=r.prepare(folder,request)
        self.review=r.materialize_review(self.package,self.audit,declaration)
        self.compiled=r.u._compile(self.selection,self.planned,self.package,self.review)
        p.ei.require(self.compiled['summary']['review_complete'],'INCOMPLETE_REVIEW')
        self.built,self.states=p._review(self.selection,self.planned,self.compiled['compiled_review'])
        p.ei.require(all(state[1] for state in self.states.values()),'SESSION_REQUIRES_COMPLETE_ACCEPTANCE')
        p.ei.require(before==fingerprint(folder),'SOURCE_CHANGED_DURING_PREPARATION')
        self.declaration=deepcopy(declaration)
        self.context={'profile':PROFILE,'parent_package_context_id':self.package['context_id'],
            'audit_context_id':self.audit['context_id'],'review_sha256':p.cr.digest(self.review),
            'declaration':self.declaration,'parent_request':deepcopy(request),'source_files':before,
            'artifacts':{f:p.ei.digest((p.ei.ROOT/f).read_text()) for f in FILES}}
        self.id=p.cr.digest(self.context)
        self.claims={c['claim']['bundle']['events'][0]['id']:c for c in self.package['context']['claims']}
        self.decisions={c['claim_id']:c['decisions'] for c in self.review['claims']}
        self.by_anchor={a['id']:a for a in self.selection['anchors']}

    def check_current(self):
        p.ei.require(fingerprint(self.folder)==self.context['source_files'],'SOURCE_CHANGED_RESTART_SESSION')
        p.ei.require(all(p.ei.digest((p.ei.ROOT/f).read_text())==h for f,h in self.context['artifacts'].items()),'IMPLEMENTATION_CHANGED_RESTART_SERVER')

    def query(self, options):
        options=controls(options); q=deepcopy(self.context['parent_request']['query'])
        p.ei.require(q['baseline']['operator']=='lt' and q['baseline']['min_before_start_us']==1 and q['followup']['min_after_anchor_us']==0 and
            q['followup']['anchor']=='start' and not q['followup']['within_interval'],'UNSUPPORTED_PARENT_WINDOWS')
        p.ei.require(options['baseline_minutes']*60000000<=q['baseline']['max_before_start_us'] and
            options['followup_minutes']*60000000<=q['followup']['max_after_anchor_us'],'OUTSIDE_REVIEWED_WINDOWS')
        q['baseline']['max_before_start_us']=options['baseline_minutes']*60000000
        q['followup']['max_after_anchor_us']=options['followup_minutes']*60000000
        q['baseline']['value_lexical']=options['threshold']; p.mixed.validate_query(q)
        return q

    def execute(self, options, progress=lambda **kw:None):
        q=self.query(options); self.check_current(); start=time.monotonic()
        by_anchor=defaultdict(list); failures={}; witnesses={}
        for i,(descriptor,b) in enumerate(zip(self.planned['batches'],self.built)):
            aid=descriptor['anchor_id']; rows=[]
            try:
                if b['measurement_store'] is not None:
                    result=p.mixed.execute(b['interval_store'],b['interval_policy'],b['semantic_policy'],
                        b['measurement_store'],b['measurement_policy'],b['alignment'],q)
                    p.ei.require(result['status']=='COMPLETED_RECORD_QUERY' and result['search_complete_over_selected_records'],'INCOMPLETE_MIXED_QUERY')
                    rows=p.source_query.graph_bindings(result)
                    p.ei.require(result['certain_patient_ids']==result['possible_patient_ids']==sorted({x[0] for x in rows}),'BATCH_MEMBERSHIP_DISAGREEMENT')
                    for binding in result['baseline_bindings']:
                        if binding['eligibility']['status']=='CERTAIN':
                            witnesses.setdefault(aid,{})[binding['baseline_id']]={'treatment':binding['treatment'],
                                'eligibility':binding['eligibility'],'mixed_context_id':result['context_id']}
                by_anchor[aid].append(rows)
            except (ValueError,RuntimeError) as error: failures[aid]=str(error)
            progress(stage='Matching reviewed records',completed=i+1,total=len(self.built))
        files={m['table']:m for m in self.selection['summary']['context']['source_files']}
        details={}; anchors=[]; all_rows=[]
        for a in self.planned['anchors']:
            aid=a['anchor_id']; original=self.by_anchor[aid]; segment=self.selection['interval_segments'][aid]
            event='input_'+files['inputevents']['csv_sha256']+'_'+str(segment['source']['record_number'])
            token=p.cr.digest([self.id,aid])[:24]
            row={'token':token,'patient_id':a['patient_id'],'episode_id':a['episode_id'],
                'start':segment['start']['raw_value'],'end':segment['end']['raw_value']}
            try:
                p.ei.require(aid not in failures and len(by_anchor[aid])==len(a['batch_ids']),'BLOCKED_ANCHOR:'+failures.get(aid,''))
                observed=p.merge_bindings(by_anchor[aid])
                raw_i=[{'id':event,'subject_id':segment['patient_id'],'stay_id':segment['icu_stay_id'],
                    'itemid':segment['item']['itemid'],'starttime':row['start'],'endtime':row['end']}]
                raw_m=[{**self.selection['measurement_records'][str(n)],'id':'chart_'+files['chartevents']['csv_sha256']+'_'+str(n)} for n in original['measurement_record_numbers']]
                expected=p.reference.execute(raw_i,raw_m,segment['item']['itemid'],q)
                p.ei.require(observed==expected,'REFERENCE_DISAGREEMENT')
                row.update(status='MATCH' if observed else 'NO_SELECTED_MATCH',eligible_pairs=len({tuple(x[:4]) for x in observed}))
                details[token]={'anchor':deepcopy(row),'treatment_event_id':event,'measurement_ids':[x['id'] for x in raw_m],
                    'bindings':observed,'witnesses':witnesses.get(aid,{}),'sql_agreement':True}
                all_rows.extend(observed)
            except ValueError as error: row.update(status='BLOCKED',reason=str(error))
            anchors.append(row)
        self.check_current() # A source change cannot leave a successful result using an obsolete review.
        complete=all(a['status']!='BLOCKED' for a in anchors)
        roster=[]
        for stay in self.selection['roster']:
            own=[a for a in anchors if (a['patient_id'],a['episode_id'])==(stay['patient_id'],stay['episode_id'])]
            status='NO_ADMITTED_ANCHOR' if not own else 'BLOCKED' if any(a['status']=='BLOCKED' for a in own) else 'MATCH' if any(a['status']=='MATCH' for a in own) else 'NO_SELECTED_MATCH'
            roster.append({'patient_id':stay['patient_id'],'episode_id':stay['episode_id'],'status':status})
        metrics=None
        if complete:
            follow=[x for x in all_rows if x[4] is not None]
            metrics={'patients':len({x[0] for x in all_rows}),'stays':len({tuple(x[:2]) for x in all_rows}),
                'segments':sum(a['status']=='MATCH' for a in anchors),'eligible_pairs':len({tuple(x[:4]) for x in all_rows}),
                'followup_bindings':len(follow),'pairs_without_followup':sum(x[4] is None for x in all_rows)}
        context={'profile':PROFILE,'session_id':self.id,'query':q,'controls':controls(options),
            'parent_review_sha256':self.context['review_sha256'],'envelope':'same item/unit/patient/stay; temporal windows within reviewed parent windows',
            'source_scope':'selected recorded claims; clinical occurrence unverified'}
        return {'status':'COMPLETED' if complete else 'BLOCKED','context':context,'context_id':p.cr.digest(context),
            'metrics':metrics,'roster':roster,'anchors':anchors if complete else [],'details':details if complete else {},
            'anchors_verified':sum(a['status']!='BLOCKED' for a in anchors),'anchors_total':len(anchors),
            'elapsed_seconds':round(time.monotonic()-start,3),'clinical_mapping_verified':False,
            'source_selection_summary':self.selection['summary']}

    def inspect(self, result, token):
        p.ei.require(result['status']=='COMPLETED' and result['context']['session_id']==self.id,'INCOMPLETE_OR_STALE_RESULT')
        p.ei.require(token in result['details'],'UNKNOWN_ANCHOR')
        d=deepcopy(result['details'][token]); q=result['context']['query']; start=datetime.fromisoformat(d['anchor']['start'])
        def evidence(eid):
            c=self.claims[eid]; event=c['claim']['bundle']['events'][0]; variables=c['claim']['bundle']['variables']
            item=c['source_record'].get('item',{})
            return {'event_id':eid,'claim_id':c['claim_id'],'claim_sha256':c['claim_sha256'],'kind':c['kind'],
                'value':event.get('value_lexical'),'unit':event.get('unit_lexical'),'item_id':event.get('item_id',item.get('itemid')),
                'label':item.get('label'),'time':variables[0]['local_lower'],'source':c['source_evidence'],
                'clock':c['clock'],'decisions':self.decisions[c['claim_id']]}
        d['treatment']=evidence(d['treatment_event_id']); d['measurements']=[]
        baselines={x[3] for x in d['bindings']}; follows={x[4] for x in d['bindings'] if x[4] is not None}
        for eid in d['measurement_ids']:
            e=evidence(eid); delta=datetime.fromisoformat(e['time'])-start
            offset=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
            reasons=[]
            if not -q['baseline']['max_before_start_us']<=offset<=-1: reasons.append('OUTSIDE_BASELINE_WINDOW')
            if Decimal(e['value'])>=Decimal(q['baseline']['value_lexical']): reasons.append('NOT_BELOW_THRESHOLD')
            if e['unit']!=q['baseline']['unit_lexical']: reasons.append('UNIT_NOT_SELECTED')
            e.update(offset_minutes=str(Decimal(offset)/Decimal(60000000)),eligible_baseline=eid in baselines,
                selected_followup=eid in follows,baseline_exclusions=reasons)
            d['measurements'].append(e)
        d.update(query=deepcopy(q),calendar_reason=self.declaration['calendar_reason'],reviewer=self.declaration['reviewer'],
            session_context_id=self.id,query_context_id=result['context_id'],clinical_mapping_verified=False)
        return d
