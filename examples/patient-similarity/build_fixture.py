"""Build explicitly authored availability overlay on the existing guided cohort.

Run from the repository root. Source events and units are copied from the existing
PRO/SOLID projection, never reinterpreted from outcome values. Availability is a
new synthetic assumption, not a claim about the prior fixture or real records.
"""
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HOUR = 3600000000
SOURCE = ROOT / 'demo/cohort-data.json'
POLICY = 'Authored overlay: event available at occurrence; age band known one hour before origin.'

def build():
    data = json.loads(SOURCE.read_text())
    profile = {'profile_id':'authored-guided-h0-qbe-1.0','clinical_validity_claim':False,
        'weights':{'age_band':'1','baseline_creatinine':'1','clinical_concepts':'1'},
        'minimum_coverage':'0.5','history_us':14*24*HOUR,'creatinine_window_us':48*HOUR,
        'creatinine_scale':'1','creatinine_unit':'mg/dL',
        'concept_ancestors':{'ex:DrugA':[],'ex:DrugAChild':['ex:DrugA'],'ex:DrugB':[]},
        'mapping_review':'authored_synthetic_review'}
    patients = []
    for row in data['patients']:
        pid = row['patient_id']; case = row['case']; clock = row['manifest']['clock_id']
        patient = {'patient_id':pid,'label':f'Authored patient {pid}', 'bearer_id':f'https://example.org/trajectory/data/person-{pid}',
            'episode_id':case['episode_id'],'clock':clock,'index_us':0,
            'index_rule':'authored fixed episode origin; strictly earlier history','records':[]}
        for event in case['events']:
            eid = event['event_id']; binding = row['evidence']['bindings'][eid]
            record = {'id':eid,'component':'baseline_creatinine' if event['event_kind']=='measurement' else 'clinical_concepts',
                'value':{'hasValue':event['value']} if event['event_kind']=='measurement' else [event['concept']],
                'occurrence_us':event['time']['start_min_us'],'available_at_us':event['time']['start_min_us'],
                'clock':clock,'source':{'document':'demo/cohort-data.json','row':eid,
                    'source_record_sha256':event['provenance']['source_record_sha256'],
                    'original_value':event['original_value'],'original_unit':event['original_unit'],
                    'availability_policy':POLICY,'binding':binding},
                'process':{'id':binding['process'],'hasParticipant':binding['patient_role']},
                'role':{'id':binding['patient_role'],'isFeatureOf':binding['patient_bearer']}}
            if event['event_kind']=='measurement':record['unit']=event['unit']
            else:record['coverage_complete']=case['source_search_complete']
            patient['records'].append(record)
        age = case['age']; age_band=f'{age//10*10}-{age//10*10+9}'; aid=f'{pid}-age-overlay'
        patient['records'].append({'id':aid,'component':'age_band','value':age_band,'occurrence_us':-HOUR,
            'available_at_us':-HOUR,'clock':clock,
            'source':{'document':'demo/cohort-data.json','row':f'{pid}/manifest/age','availability_policy':POLICY},
            'process':{'id':aid+'-process','hasParticipant':aid+'-role'},
            'role':{'id':aid+'-role','isFeatureOf':patient['bearer_id']}})
        patients.append(patient)
    dataset={'dataset_id':'guided-cohort-authored-history-overlay-1.0',
        'label':'Eleven guided authored histories with explicit synthetic availability',
        'scope':'authored_synthetic','clinical_validity_claim':False,
        'index_rule':patients[0]['index_rule'], 'patients':patients,
        'construction_origin':{'path':'demo/cohort-data.json','sha256':sha256(SOURCE.read_bytes()).hexdigest(),
            'availability_policy':POLICY,'original_availability_policy':data['availability_policy'],
            'scope':'Same patient episodes and projected source events; added age-band and availability declarations.'}}
    return dataset, profile

if __name__ == '__main__':
    for name, value in zip(('dataset','profile'),build()):
        (HERE / f'{name}.json').write_text(json.dumps(value,indent=2)+'\n')
