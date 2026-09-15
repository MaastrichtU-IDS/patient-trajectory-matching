"""Pair coverage, common selected evidence, merged follow-up and complete failure accounting."""
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from . import partitioned_window_query as p, claim_rdf as cr, exact_intervals as ei

FOLDER=ei.ROOT/'examples/source-mixed-query'
REQUEST=ei.ROOT/'examples/indexed-source-windows/synthetic-request.json'


def accept_review(selection,planned):
    """Test-only acceptance for fabricated source records; never a production default."""
    review=p.prepare_review(selection,planned)
    for descriptor,row in zip(planned['batches'],review['batches']):
        built=p.build_batch(selection,planned,descriptor)
        for kind in ('interval','measurement'):
            store=built[kind+'_store']
            if store:
                row[kind+'_policy']['decisions']=[{'id':'d_'+str(n),'claim_id':c['id'],'claim_sha256':cr.digest(c),
                    'action':'accept','supersedes':None,'reason':'Explicit fabricated record selection for tests'} for n,c in enumerate(store['claims'])]
        if row['alignment']:
            patient=descriptor['patient_id']
            row['alignment']['bindings']=[{'patient_id':patient,'interval_clock_id':'patient_'+patient,
                'measurement_clock_id':'patient_'+patient,'basis':'same_dataset_patient_calendar','reason':'Constructed same-calendar synthetic fixture'}]
    return review


class PartitionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'source';shutil.copytree(FOLDER,self.folder)
        self.request=json.loads(REQUEST.read_text())

    def setup_plan(self):
        s=p.windows.run(self.folder,self.request);return s,p.plan(s)

    def rewrite(self,rows):
        path=self.folder/'chartevents.csv'
        with path.open() as handle:header=csv.DictReader(handle).fieldnames
        with path.open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=header,lineterminator='\n');writer.writeheader();writer.writerows(rows)

    def large(self,n=38,late_followup=False):
        with (self.folder/'chartevents.csv').open() as handle:base=next(csv.DictReader(handle))
        rows=[base]
        for i in range(1,n):
            time=(f'09:{30+i:02}:00' if late_followup and i<n-1 else f'10:{i:02}:00')
            rows.append({**base,'charttime':'2150-01-01 '+time,'value':'70','valuenum':'70'})
        self.rewrite(rows);return self.setup_plan()

    def test_partition_coverage_all_sizes_through_bound(self):
        for n in range(129):
            with self.subTest(n=n):
                numbers=list(range(1,n+1));batches=p.partition_numbers(numbers);proof=p.coverage(numbers,batches)
                self.assertEqual(proof['ordered_pair_count'],n*(n-1));self.assertLessEqual(max(map(len,batches)),16)
                if n<=16:self.assertEqual(len(batches),1)

    def test_missing_cross_block_pairs_are_detected(self):
        numbers=list(range(1,39));batches=p.partition_numbers(numbers)
        with self.assertRaisesRegex(ei.ContractError,'INCOMPLETE_PARTITION_COVERAGE'):p.coverage(numbers,batches[1:])

    def test_disjoint_chunking_is_not_complete(self):
        numbers=list(range(1,25))
        with self.assertRaisesRegex(ei.ContractError,'INCOMPLETE_PARTITION_COVERAGE'):p.coverage(numbers,[numbers[:8],numbers[8:16],numbers[16:]])

    def test_invalid_duplicate_outside_and_unsorted_numbers_refused(self):
        for numbers in ([1,1],[2,1],[0],[True]):
            with self.subTest(numbers=numbers),self.assertRaises(ei.ContractError):p.partition_numbers(numbers)
        with self.assertRaises(ei.ContractError):p.coverage([1,2],[[1,2,3]])

    def test_oversized_legacy_window_resolved_without_changing_original(self):
        s,plan=self.large();before=cr.digest(s)
        self.assertEqual(s['anchors'][0]['status'],'BLOCKED_WINDOW')
        self.assertEqual(plan['anchors'][0]['status'],'PLANNED');self.assertEqual(len(plan['anchors'][0]['batch_ids']),10)
        self.assertEqual(plan['anchors'][0]['coverage']['record_count'],38)
        self.assertEqual(cr.digest(s),before);self.assertEqual(plan['summary']['blocked_anchors'],0)

    def test_plan_keeps_empty_anchors_and_stays(self):
        self.rewrite([]);s,plan=self.setup_plan()
        self.assertEqual(plan['summary']['anchors'],3);self.assertEqual(plan['summary']['batches'],3)
        r=p.execute(s,plan,p.prepare_review(s,plan))
        self.assertEqual(len(r['roster']),5);self.assertEqual(r['certain_patient_ids'],[])
        self.assertEqual(r['batch_outcomes'],{'COMPLETED_BATCH':3})

    def test_reference_disagreement_cannot_be_partitioned_away(self):
        s,plan=self.setup_plan();s['anchors'][0]['reference_agreement']=False
        blocked=p.plan(s);self.assertEqual(blocked['anchors'][0]['status'],'BLOCKED_PLAN')
        r=p.execute(s,blocked,p.prepare_review(s,blocked))
        self.assertIsNone(r['bindings']);self.assertIsNone(r['certain_patient_ids'])
        self.assertEqual(len(r['anchors']),3)

    def test_window_and_global_plan_limits_preserve_blocked_roster(self):
        s,_=self.large()
        with patch.object(p,'MAX_WINDOW_RECORDS',37):plan=p.plan(s)
        self.assertEqual(plan['anchors'][0]['reason'],'PARTITION_WINDOW_LIMIT')
        self.assertEqual(len(plan['anchors']),3)
        with patch.object(p,'MAX_BATCHES',1):plan=p.plan(s)
        self.assertEqual(plan['summary']['blocked_anchors'],3);self.assertEqual(plan['batches'],[])
        r=p.execute(s,plan,p.prepare_review(s,plan));self.assertIsNone(r['bindings'])

    def test_review_defaults_pending_and_no_implicit_alignment(self):
        s,plan=self.setup_plan();review=p.prepare_review(s,plan)
        for row in review['batches']:
            self.assertEqual(row['interval_policy']['decisions'],[])
            if row['measurement_policy']:self.assertEqual(row['measurement_policy']['decisions'],[])
            if row['alignment']:self.assertEqual(row['alignment']['bindings'],[])
        r=p.execute(s,plan,review)
        self.assertEqual(r['status'],'COMPLETED_PARTITIONED_QUERY');self.assertEqual(r['unique_accepted_claims'],0)
        self.assertEqual(r['certain_patient_ids'],[])

    def test_synthetic_execution_preserves_missing_and_lower_followup(self):
        s,plan=self.setup_plan();r=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(r['status'],'COMPLETED_PARTITIONED_QUERY');self.assertEqual(r['certain_patient_ids'],['1','2','3'])
        self.assertEqual([row[5] for row in r['bindings']],['10','14',None,'-7'])
        self.assertEqual(r['unique_accepted_claims'],9)
        for batch in r['batches']:
            self.assertEqual(batch['mixed_result']['interval_view']['semantic_run']['status'],'READY')

    def test_cross_partition_followup_removes_local_missing_rows(self):
        s,plan=self.large(17,late_followup=True);r=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(r['status'],'COMPLETED_PARTITIONED_QUERY')
        local=[row for b in r['batches'] for row in b['bindings']]
        self.assertTrue(any(row[4] is None for row in local))
        self.assertEqual(len(r['bindings']),1);self.assertIsNotNone(r['bindings'][0][4]);self.assertEqual(r['bindings'][0][5],'12')

    def test_complete_38_record_execution_and_pair_deduplication(self):
        s,plan=self.large();r=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(r['status'],'COMPLETED_PARTITIONED_QUERY');self.assertEqual(len(r['bindings']),37)
        self.assertEqual(len({tuple(row) for row in r['bindings']}),37)
        self.assertGreater(sum(len(b['bindings']) for b in r['batches']),37)
        self.assertEqual(r['unique_accepted_claims'],41) # 38 points + three interval anchors, counted once.

    def test_merge_conflicting_delta_is_not_silently_deduplicated(self):
        a=['p','s','t','b','f','1','u'];b=[*a[:5],'2','u']
        with self.assertRaisesRegex(ei.ContractError,'CONFLICTING_BATCH_BINDING'):p.merge_bindings([[a],[b]])

    def test_missing_duplicate_extra_or_reordered_reviews_refused(self):
        s,plan=self.setup_plan();review=accept_review(s,plan)
        for rows in [review['batches'][:-1],review['batches']+review['batches'][:1],list(reversed(review['batches']))]:
            with self.assertRaisesRegex(ei.ContractError,'COVERAGE_MISMATCH'):p.execute(s,plan,{**review,'batches':rows})

    def test_stale_plan_and_claim_hashes_refused(self):
        s,plan=self.setup_plan();review=accept_review(s,plan)
        for mutation in [lambda r:r.update(plan_context_id='0'*64),lambda r:r['batches'][0]['interval_policy'].update(store_sha256='0'*64),
                         lambda r:r['batches'][0]['measurement_policy']['decisions'][0].update(claim_sha256='0'*64)]:
            r=deepcopy(review);mutation(r)
            with self.assertRaises(ei.ContractError):p.execute(s,plan,r)

    def test_repeated_record_decisions_must_select_same_view(self):
        s,plan=self.large(17);review=accept_review(s,plan)
        review['batches'][0]['measurement_policy']['decisions'][0]['action']='reject'
        with self.assertRaisesRegex(ei.ContractError,'INCONSISTENT_REPEATED_RECORD_SELECTION'):p.execute(s,plan,review)

    def test_repeated_interval_decisions_must_agree(self):
        s,plan=self.large(17);review=accept_review(s,plan)
        review['batches'][0]['interval_policy']['decisions'][0]['action']='reject'
        with self.assertRaisesRegex(ei.ContractError,'INCONSISTENT_REPEATED_RECORD_SELECTION'):p.execute(s,plan,review)

    def test_alignment_must_agree_across_partitions(self):
        s,plan=self.large(17);review=accept_review(s,plan);review['batches'][0]['alignment']['bindings']=[]
        with self.assertRaisesRegex(ei.ContractError,'INCONSISTENT_PARTITION_ALIGNMENT'):p.execute(s,plan,review)

    def test_common_withdrawal_removes_followup_everywhere(self):
        s,plan=self.large(17);review=accept_review(s,plan)
        cid=next(d['claim_id'] for d in review['batches'][0]['measurement_policy']['decisions'] if d['claim_id'].endswith('_2'))
        for row in review['batches']:
            if row['measurement_policy']:
                for parent in row['measurement_policy']['decisions'].copy():
                    if parent['claim_id']==cid:row['measurement_policy']['decisions'].append({**parent,'id':'withdraw','action':'withdraw','supersedes':parent['id']})
        r=p.execute(s,plan,review);self.assertEqual(len(r['bindings']),15)
        self.assertFalse(any(row[4].endswith('_2') for row in r['bindings']))

    def test_backend_failure_keeps_complete_execution_ledger(self):
        s,plan=self.setup_plan();real=p.mixed.execute;calls=0
        def failing(*a,**kw):
            nonlocal calls
            calls+=1
            if calls==1:return {'status':'BLOCKED_INTERVAL_VIEW'}
            return real(*a,**kw)
        with patch.object(p.mixed,'execute',side_effect=failing):r=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(len(r['batches']),3);self.assertEqual(calls,3);self.assertIsNone(r['bindings']);self.assertIsNone(r['certain_patient_ids'])
        self.assertEqual(r['roster'][0]['status'],'BLOCKED_STAY')

    def test_contract_failure_is_recorded_as_blocked_batch(self):
        s,plan=self.setup_plan()
        with patch.object(p.mixed,'execute',side_effect=ei.ContractError('resource')):r=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(r['batch_outcomes'],{'BLOCKED_BATCH':3});self.assertIsNone(r['bindings'])

    def test_unpartitioned_sql_disagreement_blocks_cohort(self):
        s,plan=self.setup_plan()
        with patch.object(p.reference,'execute',return_value=[]):r=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(r['anchor_outcomes'],{'BLOCKED_REFERENCE_DISAGREEMENT':3})
        self.assertIsNone(r['certain_patient_ids']);self.assertFalse(r['search_complete_over_selected_records'])

    def test_missing_alignment_stays_unknown_not_negative(self):
        s,plan=self.setup_plan();review=accept_review(s,plan)
        for row in review['batches']:
            if row['alignment']:row['alignment']['bindings']=[]
        r=p.execute(s,plan,review);self.assertEqual(r['status'],'BLOCKED_INCOMPLETE_PARTITIONED_QUERY')
        self.assertIsNone(r['possible_patient_ids'])

    def test_original_source_claim_hash_and_origin_stable_across_batches(self):
        s,plan=self.large(17);seen={};origins=set()
        for b in plan['batches']:
            built=p.build_batch(s,plan,b)
            if built['measurement_store']:
                origins.add(built['measurement_store']['clocks'][0]['origin'])
                for claim in built['measurement_store']['claims']:
                    self.assertEqual(seen.setdefault(claim['id'],cr.digest(claim)),cr.digest(claim))
                    self.assertTrue(claim['source_record_id'].startswith('chart_'))
        self.assertEqual(len(origins),1)

    def test_inputs_immutable_and_clinical_scope_explicit(self):
        s,plan=self.setup_plan();review=accept_review(s,plan);before=cr.digest([s,plan,review]);r=p.execute(s,plan,review)
        self.assertEqual(before,cr.digest([s,plan,review]))
        self.assertEqual(r['source_selection_summary'],s['summary'])
        self.assertEqual(r['partition_plan_summary'],plan['summary'])
        self.assertIsNot(r['source_selection_summary'],s['summary'])
        for field in ('clinical_mapping_verified','source_history_verified','physical_elapsed_time_verified','causal_effect_estimated','full_mixed_owl_reasoning_verified'):
            self.assertFalse(r[field])

    def test_cli_preparation_execution_and_atomic_failure(self):
        out=Path(self.temp.name)/'result.json';reviewpath=Path(self.temp.name)/'review.json'
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(p.main(['--input-dir',str(self.folder),'--prepare-review','--output',str(out)]),0)
            prepared=json.loads(out.read_text());reviewpath.write_text(json.dumps(prepared['review_template']))
            self.assertEqual(p.main(['--input-dir',str(self.folder),'--review',str(reviewpath),'--output',str(out)]),0)
            before=out.read_bytes();reviewpath.write_text('{}')
            self.assertEqual(p.main(['--input-dir',str(self.folder),'--review',str(reviewpath),'--output',str(out)]),2)
            self.assertEqual(before,out.read_bytes())
            self.assertEqual(p.main(['--input-dir',str(self.folder),'--output',str(self.folder/'chartevents.csv')]),2)

    def test_demo_partition_report_has_complete_coverage_and_current_hashes(self):
        from . import verify_partitioned_windows as verify
        report=json.loads((ei.ROOT/'verification/partitioned-window-demo-report.json').read_text())
        self.assertEqual(report['verifier_sha256'],ei.digest(Path(verify.__file__).read_text()))
        for path,digest in report['artifacts'].items():self.assertEqual(ei.digest((ei.ROOT/path).read_text()),digest,path)
        for row in report['strata'].values():
            self.assertEqual(row['summary']['anchors'],944);self.assertEqual(row['summary']['blocked_anchors'],0)
            self.assertEqual(row['summary']['coverage_verified_anchors'],944);self.assertLessEqual(row['summary']['max_batch_records'],16)
            self.assertEqual(sum(row['partition_batch_histogram'].values()),944)
        self.assertEqual(report['real_source_claims_accepted'],0);self.assertFalse(report['real_source_mixed_query_executed'])


if __name__=='__main__':unittest.main()
