"""A session identity must mean the same thing on every machine that computes it."""
from copy import deepcopy
import unittest

from . import claim_rdf as cr, reviewed_pressure_session as session


def context(python='3.12.13', native='a' * 64, wrapper='b' * 64, version='0.4.28', item='2001'):
    """The shape that matters: a backend fingerprint nested inside the mapping."""
    return {'profile': 'mapped-pressure-session-1.0',
            'measurement_mapping': {
                'selection_plan': {
                    'supported_item_ids': [item],
                    'semantic_run': {'backend_result': {
                        'status': 'OK',
                        'backend': {'name': 'rustdl', 'version': version, 'python': python,
                                    'native_sha256': native, 'wrapper_sha256': wrapper,
                                    'instance_api_reports_truncation': False}}}}},
            # A backend also appears inside lists -- per-item runs are recorded that
            # way -- so stripping has to descend through them, not only through dicts.
            'item_outcomes': [{'item_id': item,
                               'semantic_run': {'backend_result': {'backend': {
                                   'name': 'rustdl', 'version': version, 'python': python,
                                   'native_sha256': native, 'wrapper_sha256': wrapper}}}}],
            'source_files': {'chartevents': 'c' * 64}}


class SessionIdentityTests(unittest.TestCase):
    def identity(self, **kwargs):
        return cr.digest(session.identity(context(**kwargs)))

    def test_the_runtime_that_produced_the_evidence_does_not_decide_identity(self):
        """The defect this exists for. The semantic backend records its interpreter
        patch version and the digest of its native wheel, and hashing those made the
        identity unreproducible off the machine that wrote it: three runners gave three
        ids for identical inputs, so the committed value could never be matched.
        """
        baseline = self.identity()
        self.assertEqual(baseline, self.identity(python='3.12.15'))
        self.assertEqual(baseline, self.identity(native='f' * 64))
        self.assertEqual(baseline, self.identity(wrapper='e' * 64))
        self.assertEqual(baseline, self.identity(python='3.13.0', native='9' * 64,
                                                 wrapper='8' * 64))

    def test_a_fingerprint_nested_inside_a_list_is_withheld_too(self):
        """Per-item semantic runs are recorded in a list, so a dict-only walk would
        leave those fingerprints deciding identity while appearing to strip them."""
        stripped = session.identity(context())
        withheld = stripped['item_outcomes'][0]['semantic_run']['backend_result']['backend']
        self.assertNotIn('python', withheld)
        self.assertNotIn('native_sha256', withheld)
        self.assertEqual(withheld['version'], '0.4.28')

    def test_the_engine_version_still_decides_identity(self):
        """Stripping the machine must not strip a real difference with it: a different
        reasoner version can reach different conclusions and is pinned for that reason.
        """
        self.assertNotEqual(self.identity(), self.identity(version='0.4.29'))

    def test_the_mapping_itself_still_decides_identity(self):
        self.assertNotEqual(self.identity(), self.identity(item='2002'))

    def test_the_fingerprint_is_kept_as_evidence_and_only_withheld_from_the_hash(self):
        """Identity is not the place for it; the report still has to carry it, because
        which native build produced a result is exactly what a reader needs to know.
        """
        original = context()
        stripped = session.identity(original)
        backend = original['measurement_mapping']['selection_plan']['semantic_run'][
            'backend_result']['backend']
        self.assertEqual(backend['python'], '3.12.13', 'the input must not be mutated')
        self.assertEqual(backend['native_sha256'], 'a' * 64)
        withheld = stripped['measurement_mapping']['selection_plan']['semantic_run'][
            'backend_result']['backend']
        self.assertNotIn('python', withheld)
        self.assertNotIn('native_sha256', withheld)
        self.assertEqual(withheld['name'], 'rustdl', 'only the runtime fields are withheld')

    def test_every_withheld_field_is_one_the_backend_actually_reports(self):
        """A name that no longer appears would silently stop being withheld."""
        reported = context()['measurement_mapping']['selection_plan']['semantic_run'][
            'backend_result']['backend']
        for field in session.RUNTIME_FIELDS:
            self.assertIn(field, reported, field)


if __name__ == '__main__':
    unittest.main(verbosity=2)
