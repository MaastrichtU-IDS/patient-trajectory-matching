"""Overlap arithmetic, episode scope, failure gates and reproduced aggregate provenance."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import csv
import io
import itertools
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from . import verify_pressure_overlap as v
from .test_reviewed_source_query import declare


class PressureOverlapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)/'source'
        shutil.copytree(v.p.ei.ROOT/'examples/source-mixed-query', cls.folder)
        for table in ('d_items', 'chartevents'):
            path = cls.folder/(table+'.csv')
            with path.open() as handle:
                reader = csv.DictReader(handle); fields = reader.fieldnames; rows = list(reader)
            extra = []
            for item, patients in [('2001', {'1','2'}), ('2002', {'1'})]:
                extra.extend({**row, 'itemid':item} for row in rows if row['itemid']=='2000' and
                    (table=='d_items' or row['subject_id'] in patients))
            with path.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows+extra)
        cls.executions = {}; cls.results = {}; cls.declarations = {}
        for name, item in zip(v.NAMES, ('2000','2001','2002')):
            request = v.read('examples/indexed-source-windows/synthetic-request.json')
            request['id'] = name
            for slot in ('baseline','followup'): request['query'][slot]['item_ids'] = [item]
            prepared = v.r.prepare(cls.folder, request); declaration = declare(prepared[2], prepared[3])
            report, _, result = v.r.execute_prepared(*prepared, declaration)
            cls.executions[name] = report, v._snapshot(report, result)
            cls.results[name] = result; cls.declarations[name] = declaration

    def test_actual_synthetic_queries_preserve_eligibility_without_followup(self):
        report = v.compare(self.executions)
        counts = report['context']['counts']
        self.assertEqual(counts['patients']['population'], 4)
        self.assertEqual(counts['stays']['population'], 5)
        self.assertEqual(counts['segments']['population'], 3)
        for level in v.LEVELS:
            self.assertEqual(counts[level]['matched_by_stratum'], dict(zip(v.NAMES, (3,2,1))))
            self.assertEqual(counts[level]['matched_in_all_three'], 1)
            self.assertEqual(counts[level]['matched_in_any_separate_stratum'], 3)
        self.assertEqual(self.executions['noninvasive'][0]['metrics']['eligible_pairs_without_selected_followup'], 1)
        self.assertFalse(report['pool_measurements']); self.assertFalse(report['pooled_query_executed'])
        self.assertFalse(report['patient_rows_or_identifiers_included'])
        self.assertNotIn('bindings', report); self.assertNotIn('memberships', report['context'])

    def test_same_patient_overlap_does_not_require_same_stay_or_segment(self):
        segments = {('P','S1','A'), ('P','S2','B'), ('P','S1','C')}
        memberships = dict(zip(v.NAMES, ({('P','S1','A')}, {('P','S2','B')}, {('P','S1','C')})))
        patient = v._counts({('P',)}, {n:{x[:1] for x in s} for n,s in memberships.items()})
        stay = v._counts({x[:2] for x in segments}, {n:{x[:2] for x in s} for n,s in memberships.items()})
        segment = v._counts(segments, memberships)
        self.assertEqual(patient['matched_in_all_three'], 1)
        self.assertEqual(stay['matched_in_all_three'], 0)
        self.assertEqual(segment['matched_in_all_three'], 0)
        self.assertEqual(segment['matched_in_any_separate_stratum'], 3)

    def test_all_membership_subsets_against_independent_set_algebra(self):
        population = set(range(4))
        subsets = [{i for i in population if mask & (1<<i)} for mask in range(16)]
        for a,b,c in itertools.product(subsets, repeat=3):
            result = v._counts(population, dict(zip(v.NAMES, (a,b,c))))
            self.assertEqual(result['matched_in_any_separate_stratum'], len(a|b|c))
            self.assertEqual(result['matched_in_none'], len(population-(a|b|c)))
            self.assertEqual(result['matched_in_all_three'], len(a&b&c))
            cells = {tuple(cell['strata']):cell['count'] for cell in result['exclusive_membership_cells']}
            for names, expected in [((),population-(a|b|c)), (('arterial',),a-b-c), (('noninvasive',),b-a-c),
                (('art',),c-a-b), (('arterial','noninvasive'),(a&b)-c), (('arterial','art'),(a&c)-b),
                (('noninvasive','art'),(b&c)-a), (v.NAMES,a&b&c)]:
                self.assertEqual(cells[names], len(expected))
            self.assertEqual(sum(cells.values()), len(population))

    def test_counts_reject_outside_population_or_missing_stratum(self):
        for memberships in ({'arterial':set()}, {'arterial':{2},'noninvasive':set(),'art':set()}):
            with self.assertRaises(v.p.ei.ContractError): v._counts({1}, memberships)

    def test_incomplete_run_never_produces_overlap(self):
        for field, value in [('status','BLOCKED_INCOMPLETE_PARTITIONED_QUERY'), ('search_complete_over_selected_records',False)]:
            executions = deepcopy(self.executions); executions['art'][0][field] = value
            with self.assertRaisesRegex(v.p.ei.ContractError,'INCOMPLETE_STRATUM'): v.compare(executions)
            with self.assertRaisesRegex(v.p.ei.ContractError,'INCOMPLETE_STRATUM'): v._snapshot(executions['art'][0],self.results['art'])

    def test_missing_or_extra_stratum_refused(self):
        for names in (v.NAMES[:2], v.NAMES+('extra',)):
            inputs = {n:self.executions.get(n,self.executions['art']) for n in names}
            with self.assertRaisesRegex(v.p.ei.ContractError,'MISSING_OR_EXTRA_STRATUM'): v.compare(inputs)

    def test_changed_query_source_or_population_refused(self):
        for mode in ('query','source','population'):
            inputs = deepcopy(self.executions); report, snapshot = inputs['art']
            if mode=='query': report['request']['query']['baseline']['value_lexical']='64'
            if mode=='source': report['source_selection_summary']['context']['source_files'][0]['file_sha256']='0'*64
            if mode=='population':
                patient = next(iter(snapshot['population']['patients']))
                snapshot['population']['patients'].remove(patient); snapshot['population']['patients'].add(('OTHER',))
            with self.subTest(mode=mode), self.assertRaisesRegex(v.p.ei.ContractError,'INCOMPARABLE_STRATUM'): v.compare(inputs)

    def test_mixed_or_repeated_measurement_items_refused(self):
        for mode in ('mixed','repeated'):
            inputs = deepcopy(self.executions); query = inputs['art'][0]['request']['query']
            if mode=='mixed': query['followup']['item_ids']=['2001']
            else:
                for slot in ('baseline','followup'): query[slot]['item_ids']=['2000']
            with self.subTest(mode=mode), self.assertRaises(v.p.ei.ContractError): v.compare(inputs)

    def test_snapshot_rejects_changed_results_or_accounting(self):
        report = self.executions['arterial'][0]
        for mode in ('context','patients','bindings','reference','scope','batch','roster'):
            result = deepcopy(self.results['arterial'])
            if mode=='context': result['context']['timeout_seconds']=19
            if mode=='patients': result['certain_patient_ids']=[]
            if mode=='bindings': result['bindings']=[]
            if mode=='reference': result['anchors'][0]['reference_bindings']=[]
            if mode=='scope': result['anchors'][0]['bindings'][0][0]='OTHER'
            if mode=='batch': result['batches'][0]['status']='BLOCKED_BATCH'
            if mode=='roster': result['roster'].append(deepcopy(result['roster'][0]))
            with self.subTest(mode=mode), self.assertRaises(v.p.ei.ContractError): v._snapshot(report,result)

    def test_comparison_is_deterministic_and_does_not_mutate_inputs(self):
        before = deepcopy(self.executions)
        first = v.compare(self.executions); second = v.compare(dict(reversed(list(self.executions.items()))))
        self.assertEqual(first,second); self.assertEqual(before,self.executions)
        self.assertEqual(first['context_id'],v.p.cr.digest(first['context']))

    def test_execute_rebuilds_source_and_refuses_different_committed_report(self):
        report = self.executions['arterial'][0]; declaration = self.declarations['arterial']
        for changed in (False,True):
            expected = deepcopy(report)
            if changed: expected['metrics']['matched_patients']+=1
            inputs = [report['request'],declaration,expected]
            with (patch.object(v,'read',side_effect=inputs), patch.object(v.windows,'validate_summary') as pin_check,
                  patch.object(v.r,'run',wraps=v.r.run) as run):
                if changed:
                    with self.assertRaisesRegex(v.p.ei.ContractError,'DEMO_STRATUM_NOT_REPRODUCED'): v._execute_stratum(self.folder,'arterial')
                else: self.assertEqual(v._execute_stratum(self.folder,'arterial'),self.executions['arterial'])
                run.assert_called_once_with(self.folder,report['request'],declaration)
                pin_check.assert_called_once()

    def test_cli_protects_inputs_and_preserves_previous_output_on_failure(self):
        output = Path(self.temp.name)/'overlap.json'; output.write_text('previous')
        with patch.object(v,'verify',side_effect=v.p.ei.ContractError('INCOMPLETE_STRATUM')), redirect_stderr(io.StringIO()):
            self.assertEqual(v.main(['--input-dir',str(self.folder),'--output',str(output)]),2)
        self.assertEqual(output.read_text(),'previous')
        protected = [self.folder/'chartevents.csv',v.p.ei.ROOT/'data/art-source-fidelity-review.json',
            v.p.ei.ROOT/'verification/reviewed-art-demo-report.json',v.p.ei.ROOT/'patterns/verify_pressure_overlap.py']
        with patch.object(v,'verify',side_effect=AssertionError('Input overwrite must fail before execution')), redirect_stderr(io.StringIO()):
            for path in protected:
                before = path.read_bytes()
                self.assertEqual(v.main(['--input-dir',str(self.folder),'--output',str(path)]),2)
                self.assertEqual(path.read_bytes(),before)

    def test_cli_writes_only_complete_aggregate_report(self):
        expected = v.compare(self.executions); output = Path(self.temp.name)/'complete.json'
        with patch.object(v,'verify',return_value=expected), redirect_stdout(io.StringIO()):
            self.assertEqual(v.main(['--input-dir',str(self.folder),'--output',str(output)]),0)
        self.assertEqual(json.loads(output.read_text()),expected)

    def test_committed_overlap_provenance_and_partition_counts(self):
        report = v.read('verification/reviewed-pressure-overlap-report.json'); context = report['context']
        self.assertEqual(report['context_id'],v.p.cr.digest(context))
        self.assertEqual(set(context['artifacts']),set(v.FILES))
        for path,digest in context['artifacts'].items(): self.assertEqual(digest,v.p.ei.digest((v.p.ei.ROOT/path).read_text()),path)
        for name in v.NAMES:
            source = v.read(f'verification/reviewed-{name}-demo-report.json')
            proof = context['strata'][name]
            self.assertEqual(proof['query_report_sha256'],v.p.cr.digest(source))
            self.assertEqual(proof['execution_context_id'],source['execution_context_id'])
            self.assertEqual(context['source_files'],source['source_selection_summary']['context']['source_files'])
        for level,total,union in [('patients',100,23),('stays',140,29),('segments',944,196)]:
            counts = context['counts'][level]; cells = counts['exclusive_membership_cells']
            self.assertEqual(counts['population'],total); self.assertEqual(counts['matched_in_any_separate_stratum'],union)
            self.assertEqual(sum(c['count'] for c in cells),total)
            for name in v.NAMES:
                self.assertEqual(sum(c['count'] for c in cells if name in c['strata']),counts['matched_by_stratum'][name])
            for pair in counts['pairwise_intersections']:
                self.assertEqual(pair['count'],sum(c['count'] for c in cells if set(pair['strata'])<=set(c['strata'])))
        self.assertTrue(report['all_three_queries_reproduced'])
        for flag in ('pool_measurements','pooled_query_executed','patient_rows_or_identifiers_included','clinical_mapping_verified'):
            self.assertFalse(report[flag])


if __name__=='__main__': unittest.main()
