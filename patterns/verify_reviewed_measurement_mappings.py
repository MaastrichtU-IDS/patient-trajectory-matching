"""Check authored mapped measurement strata against literal execution and exact SQL."""
import json
from pathlib import Path
from . import reviewed_measurement_mappings as m
from . import source_mixed_query as source, source_mixed_reference as sql

EXAMPLE = m.ei.ROOT / 'examples/reviewed-measurement-mappings'


def load_values(): return {p.stem: json.loads(p.read_text()) for p in EXAMPLE.glob('*.json')}


def execute(values): return m.execute(*(values[name] for name in m.INPUTS))


def exact_sql_rows(values):
    """Fixture-only conversion of explicitly accepted exact claim labels to reference DTOs."""
    answer = []
    for name, policy in [('interval',values['semantic-policy']), ('measurement',m.mixed.points.SEMANTIC_POLICY)]:
        selection = m.mixed.cp._select_validated(values[name+'-store'], values[name+'-policy'], policy)
        m.ei.require(not selection['blockers'], 'FIXTURE_SOURCE_CONFLICT')
        variables = {v['id']:v for v in selection['selected']['variables']}; rows=[]
        for event in selection['selected']['events']:
            row={'id':event['id'],'subject_id':event['patient_id'],'stay_id':event['episode_id']}
            if name=='interval':
                for side in ('start','end'):
                    v=variables[event[side+'_var']]
                    m.ei.require(v['local_lower']==v['local_upper'],'EXACT_FIXTURE_REQUIRED')
                    row[side+'time']=v['local_lower']
                row['itemid']='authored-treatment'
            else:
                v=variables[event['time_var']]
                m.ei.require(v['local_lower']==v['local_upper'],'EXACT_FIXTURE_REQUIRED')
                row.update(itemid=event['item_id'],charttime=v['local_lower'],valuenum=event['value_lexical'],valueuom=event['unit_lexical'])
            rows.append(row)
        answer.append(rows)
    return answer


def verify():
    values=load_values();result=execute(values)
    m.ei.require(result['status']=='COMPLETED_REVIEWED_MEASUREMENT_QUERY','MAPPED_MEASUREMENT_FAILED')
    m.ei.require(result['certain_patient_ids']==result['possible_patient_ids']==['P1','P2'],'MAPPED_MEASUREMENT_MEMBERSHIP')
    m.ei.require(result['selection_plan']['supported_item_ids']==['pressure_a','pressure_b'],'MAPPING_ITEM_SUPPORT')
    intervals,measurements=exact_sql_rows(values); evidence=[]
    for stratum in result['strata']:
        query=stratum['query'];run=stratum['result']
        literal=m.mixed.execute(*(values[n] for n in ('interval-store','interval-policy','semantic-policy','measurement-store','measurement-policy','alignment')),query)
        m.ei.require(run==literal,'LITERAL_STRATUM_DIFFERENCE')
        observed=source.graph_bindings(run)
        expected=sql.execute(intervals,measurements,'authored-treatment',query)
        m.ei.require(observed==expected,'MAPPED_MEASUREMENT_SQL_DIFFERENCE')
        records={r['event']['id']:r['event'] for r in run['measurement_view']['records']}
        for binding in run['baseline_bindings']:
            m.ei.require(binding['treatment']['patient_role'] and binding['treatment']['patient_bearer'],'PRO_REQUIRED')
            for followup in binding['followup']:
                a,b=records[binding['baseline_id']],records[followup['measurement_id']]
                m.ei.require((a['item_id'],a['unit_lexical'])==(b['item_id'],b['unit_lexical']),'CROSS_ITEM_OR_UNIT_PAIR')
        evidence.append({'item_id':stratum['item_id'],'certain_synthetic_patient_ids':run['certain_patient_ids'],
                         'sql_binding_rows':len(observed),'query_context_id':run['context_id']})
    a=result['strata'][0]['result']['baseline_bindings'][0]
    m.ei.require([f['measurement_id'] for f in a['followup']]==['followup1_P1'],'CROSS_ITEM_FOLLOWUP_NOT_EXCLUDED')
    m.ei.require(result['strata'][1]['result']['baseline_bindings'][0]['followup_status']=='NO_SELECTED_FOLLOWUP_IN_WINDOW','OPTIONAL_FOLLOWUP_CHANGED')
    return {'profile':'reviewed-measurement-mapping-verification-1.0','status':'VERIFIED','synthetic_only':True,
            'clinical_mapping_verified':False,'public_demo_mapping_executed':False,
            'verifier_sha256':m.ei.digest(Path(__file__).read_text()),
            'reference_artifacts':{p:m.ei.digest((m.ei.ROOT/p).read_text()) for p in ('patterns/source_mixed_query.py','patterns/source_mixed_reference.py')},
            'fixture_sha256':{str(p.relative_to(m.ei.ROOT)):m.ei.digest(p.read_text()) for p in sorted(EXAMPLE.glob('*.json'))},
            'context':result['context'],'context_id':result['context_id'],
            'mapping_context_id':result['selection_plan']['mapping']['context_id'],
            'selector_semantic_status':result['selection_plan']['semantic_run']['status'],
            'selector_ontology_sha256':result['selection_plan']['semantic_run']['ontology_sha256'],
            'item_outcomes':result['selection_plan']['item_outcomes'],'strata':evidence,
            'certain_synthetic_patient_ids':result['certain_patient_ids'],'all_literal_and_sql_bindings_equal':True,
            'cross_item_followup_excluded':True,'optional_followup_preserved':True,'pro_witnesses_preserved':True,
            'reasoning_scope':'catalogue rule witnesses followed by unchanged literal-item mixed queries'}


if __name__=='__main__':
    report=verify()
    (m.ei.ROOT/'verification/reviewed-measurement-mappings-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'synthetic_patients':report['certain_synthetic_patient_ids']}))
