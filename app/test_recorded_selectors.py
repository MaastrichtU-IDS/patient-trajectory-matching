"""Real checked selector metadata and refusal of unreviewed or altered inputs."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'demo'))
from pressure import PressureService
from mapped_pressure import MappedPressureService
from app import recorded_selectors as selectors


class RecordedSelectorTests(unittest.TestCase):
    def service(self, **kwargs):
        value = MappedPressureService(**kwargs)
        self.addCleanup(value.close)
        return value

    def copied_pack(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name) / 'mapping'
        shutil.copytree(ROOT / 'examples/measurement-source-catalogue', folder)
        return folder

    def rewrite(self, folder, name, change):
        path = folder / (name + '.json')
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def test_literal_and_reviewed_choices_have_distinct_actual_semantic_intent(self):
        literal = PressureService(synthetic=True)
        self.addCleanup(literal.close)
        with patch.object(selectors.mapped.source.mappings, 'plan', side_effect=AssertionError('literal invoked mapping')):
            exact = selectors.describe_selector(literal, 'synthetic', 'literal')
        reviewed = selectors.describe_selector(self.service(), 'synthetic', 'reviewed')
        self.assertEqual(exact['source_item_ids'], reviewed['source_item_ids'])
        self.assertIsNone(exact['class_iri'])
        self.assertIsNone(exact['semantic_check'])
        self.assertEqual(reviewed['semantic_check']['status'], 'READY')
        self.assertEqual(reviewed['semantic_check']['backend']['name'], 'rustdl')
        self.assertEqual(reviewed['semantic_check']['backend']['version'], '0.4.28')
        self.assertEqual(reviewed['semantic_check']['supported_item_ids'], ['2000'])
        self.assertEqual(reviewed['unit_lexical'], 'mmHg')
        self.assertIn('pressure-family', reviewed['class_iri'])
        self.assertTrue(reviewed['review_evidence'][0]['decision_id'])
        self.assertFalse(reviewed['clinical_mapping_verified'])
        self.assertFalse(reviewed['cross_item_value_comparison_performed'])
        self.assertFalse(reviewed['unit_conversion_performed'])

    def test_configured_selector_keeps_its_separate_reviewed_item(self):
        value = selectors.describe_selector(
            self.service(config=ROOT / 'examples/configured-pressure-service/config.json'),
            'configured', 'reviewed')
        self.assertEqual(value['source_item_ids'], ['2001'])
        self.assertEqual(value['semantic_check']['supported_item_ids'], ['2001'])
        self.assertIn('Second synthetic pressure item', value['label'])
        self.assertEqual(len(value['review_evidence']), 1)

    def test_pending_mapping_blocks_before_rust_and_never_falls_back(self):
        folder = self.copied_pack()
        self.rewrite(folder, 'review', lambda value: value.update(decisions=[]))
        with patch.object(selectors.mapped.source.mappings.semantic, 'check') as semantic:
            with self.assertRaisesRegex(ValueError, 'BLOCKED_MAPPING_REVIEW'):
                selectors.describe_selector(self.service(mapping_dir=folder), 'synthetic', 'reviewed')
            semantic.assert_not_called()

    def test_unresolved_rust_semantics_is_blocked(self):
        with patch.object(selectors.mapped.source.mappings.semantic, 'run_backend',
                          return_value={'status': 'BACKEND_TIMEOUT'}):
            with self.assertRaisesRegex(ValueError, 'BLOCKED_MEASUREMENT_MAPPING_SEMANTICS'):
                selectors.describe_selector(self.service(), 'synthetic', 'reviewed')

    def test_unit_mismatch_is_not_converted(self):
        folder = self.copied_pack()
        self.rewrite(folder, 'selector', lambda value: value.update(unit_lexical='kPa'))
        with self.assertRaisesRegex(ValueError, 'RECORDED_SELECTOR_UNIT_MISMATCH'):
            selectors.describe_selector(self.service(mapping_dir=folder), 'synthetic', 'reviewed')

    def test_altered_source_catalogue_is_not_advertised(self):
        folder = self.copied_pack()
        self.rewrite(folder, 'catalogue', lambda value: value['entries'][0].update(label='Altered label'))
        with patch.object(selectors.mapped.source.mappings, 'plan') as plan:
            with self.assertRaisesRegex(ValueError, 'MAPPED_PRESSURE_SOURCE_CATALOGUE_MISMATCH'):
                selectors.describe_selector(self.service(mapping_dir=folder), 'synthetic', 'reviewed')
            plan.assert_not_called()

    def test_unknown_mode_stratum_and_mislabelled_service_are_rejected(self):
        service = self.service()
        for stratum, mode, code in [
            ('synthetic', 'arbitrary-ontology', 'UNKNOWN_RECORDED_SELECTOR_MODE'),
            ('https://example.org/other', 'reviewed', 'UNKNOWN_RECORDED_SELECTOR_STRATUM'),
            ('synthetic', 'literal', 'RECORDED_SELECTOR_SERVICE_MISMATCH'),
        ]:
            with self.subTest(mode=mode, stratum=stratum):
                with self.assertRaisesRegex(ValueError, code):
                    selectors.describe_selector(service, stratum, mode)

    def test_metadata_preserves_configured_query(self):
        service = self.service(config=ROOT / 'examples/configured-pressure-service/config.json')
        before = deepcopy(service.configuration['request'])
        selectors.describe_selector(service, 'configured', 'reviewed')
        self.assertEqual(service.configuration['request'], before)


if __name__ == '__main__':
    unittest.main()
