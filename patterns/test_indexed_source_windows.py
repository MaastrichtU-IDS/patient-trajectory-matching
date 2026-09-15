"""Indexed-window fidelity, original identity, complete anchors and pending-only export."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime,timedelta
import csv
import io
import json
from pathlib import Path
import random
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from . import indexed_source_windows as w, claim_rdf as cr, exact_intervals as ei
from . import measurement_claims as points, source_mixed_reference as sql, mixed_record_query as mixed
from . import verify_indexed_source_windows as verify

FOLDER=ei.ROOT/'examples/source-mixed-query'
REQUEST=ei.ROOT/'examples/indexed-source-windows/synthetic-request.json'


def accept(store,policy):
    policy['decisions']=[{'id':'test_'+str(n),'claim_id':c['id'],'claim_sha256':cr.digest(c),'action':'accept',
                         'supersedes':None,'reason':'Explicit fabricated test selection only'} for n,c in enumerate(store['claims'])]


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'source';shutil.copytree(FOLDER,self.folder)
        self.request=json.loads(REQUEST.read_text())

    def run_select(self):return w.run(self.folder,self.request)

    def rewrite(self,table,change):
        path=self.folder/(table+'.csv')
        with path.open() as f:
            reader=csv.DictReader(f);header=reader.fieldnames;rows=list(reader)
        change(rows)
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=header,lineterminator='\n');writer.writeheader();writer.writerows(rows)

    def test_full_row_and_stay_accounting(self):
        r=self.run_select();s=r['summary']
        self.assertEqual(s['input_rows'],{'inputevents':5,'chartevents':9})
        self.assertEqual(s['anchors'],3);self.assertEqual(len(r['roster']),5)
        self.assertEqual(s['stays_without_admitted_anchor'],2)
        self.assertEqual(s['anchor_outcomes'],{'COUNT_BOUNDED_WINDOW':3})
        self.assertEqual([a['measurement_record_numbers'] for a in r['anchors']],[[1,2,3],[4],[5,6]])
        self.assertTrue(s['all_indexed_windows_agree_with_reference'])
        for table,n in s['input_rows'].items():self.assertEqual(sum(s['row_outcomes'][table].values()),n)

    def test_outside_rows_keep_original_numbers_and_exclusion_accounting(self):
        self.rewrite('chartevents',lambda rows:rows.insert(0,{**rows[0],'itemid':'99999'}))
        r=self.run_select()
        self.assertEqual(r['anchors'][0]['measurement_record_numbers'],[2,3,4])
        self.assertEqual(r['summary']['row_outcomes']['chartevents']['OUTSIDE_ITEM_SCOPE'],1)
        self.assertEqual(r['source_ledger']['chartevents_candidates'][0]['record_number'],2)

    def test_windows_ignore_numeric_value_unit_and_outcome_direction(self):
        original=self.run_select()
        self.rewrite('chartevents',lambda rows:[rows[n].update(value='999',valuenum='999',valueuom='other-unit') for n in (0,6)])
        changed=self.run_select()
        self.assertEqual(original['anchors'][0]['measurement_record_numbers'],changed['anchors'][0]['measurement_record_numbers'])
        self.assertNotEqual(original['summary']['context_id'],changed['summary']['context_id'])
        self.assertIn(6,changed['anchors'][2]['measurement_record_numbers']) # Lower follow-up preserved.

    def test_missing_followup_and_completely_empty_anchor_remain(self):
        self.rewrite('chartevents',lambda rows:rows.clear())
        r=self.run_select()
        self.assertEqual(r['summary']['anchors'],3);self.assertEqual(r['summary']['anchor_outcomes'],{'EMPTY_WINDOW':3})
        b=w.pending_batch(r,r['anchors'][0]['id'])
        self.assertIsNone(b['measurement_store']);self.assertIsNone(b['alignment_template'])
        self.assertEqual(len(b['interval_store']['claims']),1)
        self.assertIsNone(r['summary']['cohort_membership'])

    def test_inclusive_bounds_and_strict_baseline(self):
        self.rewrite('chartevents',lambda rows:rows.__setitem__(slice(None),[
            {**rows[0],'charttime':'2150-01-01 '+time,'value':'60','valuenum':'60'}
            for time in ['09:29:59','09:30:00','09:59:59','10:00:00','12:00:00','12:00:01']]))
        r=self.run_select();self.assertEqual(r['anchors'][0]['measurement_record_numbers'],[2,3,4,5])
        # Start-time point comes from follow-up; exclude that window to check strict baseline independently.
        self.request['query']['followup'].update(anchor='end',within_interval=True)
        r=self.run_select();self.assertEqual(r['anchors'][0]['measurement_record_numbers'],[2,3])
        self.request['query']['baseline']['min_before_start_us']=0
        r=self.run_select();self.assertEqual(r['anchors'][0]['measurement_record_numbers'],[2,3,4])

    def test_halfopen_interval_and_end_anchor(self):
        self.rewrite('chartevents',lambda rows:rows.__setitem__(slice(None),[
            {**rows[0],'charttime':'2150-01-01 '+time} for time in ['10:00:00','10:44:59','10:45:00']]))
        self.request['query']['followup']['within_interval']=True
        self.assertEqual(self.run_select()['anchors'][0]['measurement_record_numbers'],[1,2])
        self.request['query']['followup'].update(anchor='end',within_interval=False)
        self.assertEqual(self.run_select()['anchors'][0]['measurement_record_numbers'],[3])

    def test_large_gap_bounds_do_not_overflow_sqlite(self):
        self.request['query']['baseline']['max_before_start_us']=9223372036854775807
        self.request['query']['followup']['max_after_anchor_us']=9223372036854775807
        r=self.run_select();self.assertTrue(r['summary']['all_indexed_windows_agree_with_reference'])
        self.assertEqual(r['anchors'][0]['measurement_record_numbers'],[1,2,3])

    def test_overlapping_windows_deduplicate_within_anchor_not_between_anchors(self):
        self.request['query']['baseline']['min_before_start_us']=0
        self.rewrite('chartevents',lambda rows:rows.insert(0,{**rows[0],'charttime':'2150-01-01 10:00:00'}))
        self.rewrite('inputevents',lambda rows:rows.append({**rows[0],'orderid':'999'}))
        r=self.run_select()
        first,last=r['anchors'][0],r['anchors'][-1]
        self.assertEqual(first['measurement_record_numbers'],last['measurement_record_numbers'])
        self.assertEqual(first['measurement_record_numbers'].count(1),1)
        self.assertEqual(r['summary']['anchors'],4)

    def test_patient_and_stay_isolation(self):
        self.rewrite('chartevents',lambda rows:rows.append({**rows[0],'stay_id':'101'}))
        r=self.run_select();self.assertNotIn(10,r['anchors'][0]['measurement_record_numbers'])
        for anchor in r['anchors']:
            for n in anchor['measurement_record_numbers']:
                row=r['measurement_records'][str(n)]
                self.assertEqual((row['subject_id'],row['stay_id']),(anchor['patient_id'],anchor['episode_id']))
        self.assertEqual(r['source_ledger']['chartevents_candidates'][-1]['window_selection'],'OUTSIDE_ALL_ANCHOR_WINDOWS')

    def test_independent_direct_reference_over_random_microsecond_boundaries(self):
        rng=random.Random(141);query=deepcopy(self.request['query']);epoch=datetime(2150,1,1)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE measurement(number INTEGER PRIMARY KEY,patient TEXT,stay TEXT,item TEXT,position INTEGER)')
            for _ in range(50):
                start=rng.randrange(3,12);end=start+rng.randrange(1,8)
                segment={'patient_id':'1','icu_stay_id':'100',**{k:{'raw_value':(epoch+timedelta(microseconds=v)).isoformat()} for k,v in [('start',start),('end',end)]}}
                query['baseline'].update(min_before_start_us=0,max_before_start_us=rng.randrange(0,5))
                query['followup'].update(anchor=rng.choice(['start','end']),min_after_anchor_us=0,max_after_anchor_us=rng.randrange(0,6),within_interval=rng.choice([False,True]))
                rows=[(n,{'subject_id':'1','stay_id':'100','itemid':'2000','charttime':(epoch+timedelta(microseconds=n)).isoformat()}) for n in range(25)]
                db.execute('DELETE FROM measurement')
                db.executemany('INSERT INTO measurement VALUES (?,?,?,?,?)',[(n,'1','100','2000',w.local.coordinate(row['charttime'],w.ORIGIN)) for n,row in rows])
                self.assertEqual(w.indexed_ids(db,segment,'2000',query),w.reference_ids(segment,rows,query))

    def test_query_uses_scope_and_time_index(self):
        original=w.indexed_ids;plans=[]
        def inspect(db,segment,item,query):
            plans.append(str(db.execute('EXPLAIN QUERY PLAN SELECT number FROM measurement WHERE patient=? AND stay=? AND item=? AND position BETWEEN ? AND ?',('1','100','2000',0,10)).fetchall()))
            return original(db,segment,item,query)
        with patch.object(w,'indexed_ids',side_effect=inspect):self.run_select()
        self.assertEqual(len(plans),3)
        for detail in plans:
            self.assertIn('measurement_window',detail);self.assertIn('SEARCH',detail)

    def test_oversized_anchor_keeps_all_references_and_blocks_export(self):
        self.rewrite('chartevents',lambda rows:rows.__setitem__(slice(None),[
            {**rows[0],'charttime':f'2150-01-01 10:{n:02}:00'} for n in range(33)]))
        r=self.run_select();a=r['anchors'][0]
        self.assertEqual(a['selected_measurement_count'],33);self.assertEqual(len(a['measurement_record_numbers']),33)
        self.assertIn('MEASUREMENT_CLAIM_LIMIT',a['reasons'])
        self.assertEqual(r['summary']['status'],'BLOCKED_INCOMPLETE_WINDOW_SELECTION')
        self.assertIsNone(r['summary']['cohort_membership'])
        with self.assertRaisesRegex(ei.ContractError,'BLOCKED_ANCHOR'):w.pending_batch(r,a['id'])
        self.assertEqual(len(r['anchors']),3)

    def test_index_disagreement_blocks_result(self):
        with patch.object(w,'indexed_ids',return_value=[]):r=self.run_select()
        self.assertFalse(r['summary']['all_indexed_windows_agree_with_reference'])
        self.assertEqual(r['summary']['status'],'BLOCKED_INCOMPLETE_WINDOW_SELECTION')
        self.assertIn('INDEX_REFERENCE_DISAGREEMENT',r['anchors'][0]['reasons'])

    def test_scan_and_anchor_limits_refuse_partial_output(self):
        for module,key,value in [(w,'MAX_ANCHORS',2),(w.scan,'MAX_CHART_ROWS',8),(w.scan,'MAX_SELECTED_ROWS',8),(w.inputs,'MAX_EPISODES',4)]:
            with self.subTest(key=key),patch.object(module,key,value),self.assertRaises(ei.ContractError):self.run_select()

    def test_pending_batch_retains_original_source_identity_and_isolation(self):
        r=self.run_select();b=w.pending_batch(r,r['anchors'][0]['id'])
        files={f['table']:f for f in r['summary']['context']['source_files']}
        self.assertEqual(b['interval_policy']['decisions'],[]);self.assertEqual(b['measurement_policy']['decisions'],[])
        self.assertEqual(b['alignment_template']['bindings'],[])
        self.assertEqual(b['isolation']['interval']['logical_axioms_checked'],137)
        self.assertEqual(b['isolation']['measurement']['logical_axioms_checked'],141)
        for c,evidence in zip(b['measurement_store']['claims'],b['source_evidence']['measurements']):
            self.assertEqual(c['source_sha256'],files['chartevents']['file_sha256'])
            self.assertEqual(c['source_record_id'],'chart_'+files['chartevents']['csv_sha256']+'_'+str(evidence['record_number']))
        self.assertEqual(points.select(b['measurement_store'],b['measurement_policy'])['status'],'EMPTY_SELECTED_RECORDS')

    def test_pending_batch_accepted_synthetically_matches_real_rust_and_sql(self):
        r=self.run_select();a=r['anchors'][0];b=w.pending_batch(r,a['id'])
        for kind in ('interval','measurement'):accept(b[kind+'_store'],b[kind+'_policy'])
        b['alignment_template']['bindings']=[{'patient_id':'1','interval_clock_id':'patient_1','measurement_clock_id':'patient_1',
                                             'basis':'same_dataset_patient_calendar','reason':'Constructed synthetic fixture calendar'}]
        result=mixed.execute(b['interval_store'],b['interval_policy'],b['semantic_policy'],b['measurement_store'],b['measurement_policy'],b['alignment_template'],b['query'])
        self.assertEqual(result['status'],'COMPLETED_RECORD_QUERY');self.assertEqual(result['certain_patient_ids'],['1'])
        self.assertEqual(result['interval_view']['semantic_run']['status'],'READY')
        from .source_mixed_query import graph_bindings
        segment=r['interval_segments'][a['id']];event=b['interval_store']['claims'][0]['bundle']['events'][0]
        raw_i=[{'id':event['id'],'subject_id':'1','stay_id':'100','itemid':'1000','starttime':segment['start']['raw_value'],'endtime':segment['end']['raw_value']}]
        raw_m=[{**r['measurement_records'][str(n)],'id':c['bundle']['events'][0]['id']} for n,c in zip(a['measurement_record_numbers'],b['measurement_store']['claims'])]
        self.assertEqual(graph_bindings(result),sql.execute(raw_i,raw_m,'1000',b['query']))

    def test_origins_precede_window_selection(self):
        self.rewrite('chartevents',lambda rows:rows.append({**rows[0],'charttime':'2150-01-01 01:00:00'}))
        r=self.run_select();b=w.pending_batch(r,r['anchors'][0]['id'])
        self.assertNotIn(10,r['anchors'][0]['measurement_record_numbers'])
        self.assertEqual(b['measurement_store']['clocks'][0]['origin'],'2150-01-01T01:00:00')
        self.assertTrue(b['measurement_store']['clocks'][0]['origin_source_key'].endswith(':10'))
        self.assertEqual(b['source_evidence']['measurement_origin']['record_number'],10)

    def test_duplicate_and_warning_exclusions_have_ledger_identity(self):
        r=self.run_select();ledger={e['record_number']:e for e in r['source_ledger']['chartevents_candidates']}
        self.assertEqual(ledger[7]['duplicate_of_record_number'],1)
        self.assertEqual(ledger[8]['reasons'],['SOURCE_WARNING_FLAGGED'])
        self.assertEqual(ledger[7]['window_selection'],'NOT_ADMITTED')
        self.assertNotIn('7',r['measurement_records'])

    def test_request_requires_one_stratum_and_declared_calendar_basis(self):
        for mutation in [lambda q:q.pop('calendar_basis'),lambda q:q['query']['followup'].update(item_ids=['999']),
                         lambda q:q['query']['baseline'].update(item_ids=['2000','999']),lambda q:q['query'].update(treatment_class_iri='https://example.org/Drug')]:
            self.request=json.loads(REQUEST.read_text());mutation(self.request)
            with self.subTest(mutation=mutation),self.assertRaises(ei.ContractError):self.run_select()

    def test_query_change_rebinds_batch_even_without_membership_change(self):
        a=self.run_select();ba=w.pending_batch(a,a['anchors'][0]['id'])
        self.request['query']['baseline']['value_lexical']='50';b=self.run_select();bb=w.pending_batch(b,b['anchors'][0]['id'])
        self.assertEqual(a['anchors'][0]['measurement_record_numbers'],b['anchors'][0]['measurement_record_numbers'])
        self.assertNotEqual(ba['batch_id'],bb['batch_id']);self.assertNotEqual(ba['interval_policy']['store_sha256'],bb['interval_policy']['store_sha256'])

    def test_invalid_unknown_anchor_and_input_immutability(self):
        original=deepcopy(self.request);r=self.run_select();before=cr.digest(r)
        w.pending_batch(r,r['anchors'][0]['id']);self.assertEqual(cr.digest(r),before);self.assertEqual(self.request,original)
        with self.assertRaisesRegex(ei.ContractError,'UNKNOWN_ANCHOR'):w.pending_batch(r,'missing')

    def test_cli_aggregate_batch_and_atomic_failure(self):
        out=Path(self.temp.name)/'result.json'
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(w.main(['--input-dir',str(self.folder),'--aggregate-only','--output',str(out)]),0)
            self.assertNotIn('measurement_records',json.loads(out.read_text()))
            anchor=self.run_select()['anchors'][0]['id']
            self.assertEqual(w.main(['--input-dir',str(self.folder),'--anchor-id',anchor,'--output',str(out)]),0)
            self.assertEqual(json.loads(out.read_text())['status'],'PENDING_REVIEW');before=out.read_bytes()
            with patch.object(w,'MAX_ANCHORS',0):self.assertEqual(w.main(['--input-dir',str(self.folder),'--output',str(out)]),2)
            self.assertEqual(out.read_bytes(),before)
            self.assertEqual(w.main(['--input-dir',str(self.folder),'--output',str(self.folder/'chartevents.csv')]),2)

    def test_demo_report_pins_context_and_complete_anchor_accounting(self):
        report=json.loads((ei.ROOT/'verification/indexed-source-windows-demo-report.json').read_text())
        self.assertFalse(report['patient_rows_or_identifiers_included'])
        for name,s in report['strata'].items():
            self.assertEqual(verify.validate_summary(s,name),s)
            self.assertEqual(s['anchors'],944);self.assertEqual(s['icu_stays'],140)
            self.assertTrue(s['all_indexed_windows_agree_with_reference']);self.assertFalse(s['mixed_query_executed'])
            self.assertEqual(s['accepted_claims'],0);self.assertIsNone(s['cohort_membership'])
            bad=deepcopy(s);bad['context']['source_files'][0]['file_sha256']='0'*64
            with self.assertRaises(ei.ContractError):verify.validate_summary(bad,name)

    def test_demo_report_implementation_hashes_current(self):
        report=json.loads((ei.ROOT/'verification/indexed-source-windows-demo-report.json').read_text())
        self.assertEqual(report['verifier_sha256'],ei.digest(Path(verify.__file__).read_text()))
        for s in report['strata'].values():
            for path,digest in s['context']['artifacts'].items():self.assertEqual(ei.digest((ei.ROOT/path).read_text()),digest,path)


if __name__=='__main__':unittest.main()
