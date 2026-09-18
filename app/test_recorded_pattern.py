"""Executable point/interval authoring against the reviewed engine and SQL oracle."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from app import recorded_pattern as authored
from patterns import reviewed_pressure_session as pressure

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}


class RecordedPatternTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name) / 'source'
        shutil.copytree(ROOT / 'examples/source-mixed-query', cls.folder)
        request = json.loads((ROOT / 'examples/indexed-source-windows/synthetic-request.json').read_text())
        declaration = json.loads((ROOT / 'demo/pressure-synthetic-review.json').read_text())
        cls.session = pressure.Session(cls.folder, request, declaration)
        cls.parent = request['query']
        cls.original = cls.session.execute(CONTROLS)

    def form(self):
        return authored.decompile_query(self.parent)

    def run_form(self, form):
        result = authored.execute(self.session, form, query_context_id=self.original['context_id'])
        self.assertEqual(result['status'], 'COMPLETED')
        return result

    def test_exact_roundtrip_preserves_points_and_default_bindings(self):
        form = self.form()
        self.assertEqual(authored.compile_pattern(form, self.parent), self.parent)
        result = self.run_form(form)
        self.assertEqual(result['metrics'], self.original['metrics'])
        self.assertEqual(result['roster'], self.original['roster'])
        self.assertEqual([d['bindings'] for d in result['details'].values()],
                         [d['bindings'] for d in self.original['details'].values()])
        metadata = authored.describe(self.session)
        self.assertEqual(metadata['shape'], ['baseline_point', 'treatment_interval', 'optional_followup_point'])
        self.assertEqual(form['baseline']['maximum_offset_us'], -1)
        self.assertEqual(result['context']['parent_query_context_id'], self.original['context_id'])
        self.assertNotIn('parent_job_id', result['context'])
        self.assertNotIn('controls', result['context'])

    def test_prepared_pattern_reuses_batches_without_changing_exact_evidence(self):
        from patterns.prepared_mixed_query import PreparedExecutor
        executor = PreparedExecutor()
        form = self.form()
        adapter = authored.prepare(self.session, form, parent_query_context_id=self.original['context_id'])
        adapter.execute(CONTROLS, batch_executor=executor.execute)
        form['baseline'].update(operator='ge', value_lexical='60')
        adapter = authored.prepare(self.session, form, parent_query_context_id=self.original['context_id'])
        actual = adapter.execute(CONTROLS, batch_executor=executor.execute)
        expected = adapter.execute(CONTROLS)
        actual.pop('elapsed_seconds'); expected.pop('elapsed_seconds')
        self.assertEqual(actual, expected)
        self.assertGreater(executor.snapshot()['hits'], 0)

    def test_all_scalar_operators_checked_by_independent_sql(self):
        for operator, expected in [('lt', 1), ('le', 2), ('eq', 1), ('ge', 2), ('gt', 1)]:
            with self.subTest(operator=operator):
                form = self.form()
                form['baseline'].update(operator=operator, value_lexical='60')
                result = self.run_form(form)
                self.assertEqual(result['metrics']['patients'], expected)
                self.assertTrue(all(d['sql_agreement'] for d in result['evidence']))
                for detail in result['evidence']:
                    for m in detail['measurements']:
                        if m['eligible_baseline']:
                            self.assertEqual(m['baseline_exclusions'], [])
                        self.assertNotIn('NOT_BELOW_THRESHOLD', m['baseline_exclusions'])

    def test_followup_optional_and_full_roster_preserved(self):
        form = self.form()
        form['followup'].update(minimum_offset_us=0, maximum_offset_us=0)
        result = self.run_form(form)
        self.assertEqual(result['metrics']['patients'], 3)
        self.assertEqual(result['metrics']['pairs_without_followup'], 3)
        self.assertEqual(result['metrics']['followup_bindings'], 0)
        self.assertEqual(len(result['roster']), 5)
        self.assertEqual(sum(r['status'] == 'NO_ADMITTED_ANCHOR' for r in result['roster']), 2)

    def test_signed_baseline_bounds_and_inside_interval_are_executable(self):
        form = self.form()
        form['baseline'].update(minimum_offset_us=-30 * 60000000, maximum_offset_us=-15 * 60000000)
        form['followup']['within_interval'] = True
        result = self.run_form(form)
        for d in result['evidence']:
            for m in d['measurements']:
                if m['eligible_baseline']:
                    self.assertEqual(m['baseline_exclusions'], [])
        self.assertEqual(authored.decompile_query(authored.compile_pattern(form, self.parent)), form)

    def test_end_anchor_requires_and_respects_retained_envelope(self):
        form = self.form()
        form['followup'].update(anchor='end', maximum_offset_us=0)
        with self.assertRaisesRegex(ValueError, 'DURATIONS'):
            authored.compile_pattern(form, self.parent)
        result = self.run_form(form)
        self.assertEqual(result['metrics']['patients'], 3)
        form['followup']['maximum_offset_us'] = self.parent['followup']['max_after_anchor_us']
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_REVIEWED'):
            self.run_form(form)

    def test_rejects_unreviewed_shape_units_and_offsets(self):
        mutations = [lambda f: f.update(slots=[]),
            lambda f: f['baseline'].update(unit_lexical='kPa'),
            lambda f: f['baseline'].update(item_ids=['other']),
            lambda f: f['baseline'].update(operator='contains'),
            lambda f: f['baseline'].update(maximum_offset_us=0),
            lambda f: f['baseline'].update(minimum_offset_us=-31 * 60000000),
            lambda f: f['baseline'].update(minimum_offset_us=True),
            lambda f: f['baseline'].update(value_lexical='NaN'),
            lambda f: f['followup'].update(minimum_offset_us=-1),
            lambda f: f['followup'].update(maximum_offset_us=121 * 60000000),
            lambda f: f['followup'].update(within_interval=1)]
        for change in mutations:
            form = self.form(); change(form)
            with self.subTest(form=form), self.assertRaises(ValueError):
                authored.compile_pattern(form, self.parent)
        parent = deepcopy(self.parent); parent['followup']['within_interval'] = True
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_REVIEWED'):
            authored.compile_pattern(self.form(), parent)

    def test_retained_execution_does_not_read_changed_files_or_mutate_source(self):
        before = deepcopy([self.session.context, self.session.built, self.session.review, self.original])
        with patch.object(pressure, 'fingerprint', side_effect=AssertionError('Source must not be reopened')):
            self.run_form(self.form())
        self.assertEqual(before, [self.session.context, self.session.built, self.session.review, self.original])

    def test_disagreement_or_engine_failure_never_returns_partial_success(self):
        with patch.object(pressure.p.reference, 'execute', return_value=[]):
            result = authored.execute(self.session, self.form(), query_context_id=self.original['context_id'])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIsNone(result['metrics'])
        self.assertEqual(result['anchors'], [])
        self.assertEqual(result['evidence'], [])

    def test_reviewed_mapping_provenance_survives_retained_execution(self):
        from patterns import mapped_pressure_session
        session = mapped_pressure_session.Session(self.folder,
            self.session.context['parent_request'], self.session.declaration,
            ROOT / 'examples/measurement-source-catalogue')
        form = self.form()
        form['baseline'].update(operator='ge', value_lexical='60')
        with patch.object(mapped_pressure_session, 'load_pack', side_effect=AssertionError('Do not reopen mapping files')):
            result = authored.execute(session, form, query_context_id='retained-mapped-parent')
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['metrics']['patients'], 2)
        plan = session.context['measurement_mapping']['selection_plan']
        self.assertEqual(result['measurement_mapping']['mapping_context_id'], plan['mapping']['context_id'])
        self.assertTrue(all(d['measurement_mapping'] == session.context['measurement_mapping'] for d in result['evidence']))


if __name__ == '__main__':
    unittest.main()
