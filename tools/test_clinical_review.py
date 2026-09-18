"""Clinical approval cannot be manufactured from technical test status."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.check_clinical_review import ROOT, clinical_claim_gate, read_json, validate_review


class ClinicalReviewTests(unittest.TestCase):
    def setUp(self):
        self.template = read_json(ROOT / 'data/clinical-review/pressure-review.pending.json')

    def accepted_fixture(self):
        # Test-only attestation, never committed as a review or used as evidence.
        review = deepcopy(self.template)
        sign = {'name': 'Authored test reviewer', 'role': 'Test fixture only',
                'organization': 'Test fixture', 'date': date.today().isoformat(),
                'protocol_sha256': review['artifacts']['protocol']['sha256'],
                'rationale': 'Authored attestation for validator boundary testing only.'}
        for value in review['decisions'].values():
            value.update(decision='ACCEPT', reviewer=deepcopy(sign))
        review.update(status='ACCEPTED', signoff=deepcopy(sign))
        return review

    def test_pending_package_is_valid_but_blocks_claims(self):
        result = validate_review(self.template)
        self.assertEqual(result['status'], 'PENDING')
        self.assertFalse(result['clinical_approval_recorded'])
        with self.assertRaisesRegex(ValueError, 'approval is not recorded'):
            clinical_claim_gate(self.template, {'status': 'TECHNICALLY_VERIFIED'})

    def test_multivariable_review_stays_pending_and_separates_authored_example(self):
        review = read_json(ROOT / 'data/clinical-review/multivariable-review.pending.json')
        result = validate_review(review)
        self.assertFalse(result['clinical_approval_recorded'])
        self.assertEqual(len(result['pending_or_unaccepted_decisions']), 9)
        definition = read_json(ROOT / review['artifacts']['variable_definition']['path'])
        recorded = {entry['id']: entry for entry in definition['variables']}
        self.assertEqual((recorded['heart_rate']['item_id'], recorded['heart_rate']['unit']), ('220045', 'bpm'))
        self.assertEqual((recorded['respiratory_rate']['item_id'], recorded['respiratory_rate']['unit']), ('220210', 'insp/min'))
        self.assertEqual(definition['authored_example']['not_public_source_ids']['heart_rate'], '3000')
        self.assertFalse(definition['authored_example']['clinical_approval_recorded'])
        with self.assertRaisesRegex(ValueError, 'approval is not recorded'):
            clinical_claim_gate(review, result)

    def test_old_pressure_approval_cannot_authorize_multivariable_report_or_review(self):
        accepted_pressure = self.accepted_fixture()
        multivariable = read_json(ROOT / 'data/clinical-review/multivariable-review.pending.json')
        result = validate_review(multivariable)
        with self.assertRaisesRegex(ValueError, 'clinical_protocol_sha256'):
            clinical_claim_gate(accepted_pressure, result)
        multivariable.update(status='ACCEPTED', decisions=accepted_pressure['decisions'],
                             signoff=accepted_pressure['signoff'])
        with self.assertRaisesRegex(ValueError, 'different protocol version'):
            validate_review(multivariable)

    def test_status_alone_cannot_create_acceptance(self):
        self.template['status'] = 'ACCEPTED'
        with self.assertRaisesRegex(ValueError, 'must be PENDING'):
            validate_review(self.template)

    def test_every_decision_and_final_signature_are_required(self):
        review = self.accepted_fixture()
        review['decisions']['similarity']['reviewer'] = None
        with self.assertRaisesRegex(ValueError, 'attestation'):
            validate_review(review)
        review = self.accepted_fixture()
        review['signoff'] = None
        with self.assertRaisesRegex(ValueError, 'must be PENDING'):
            validate_review(review)

    def test_scope_is_exactly_source_and_protocol_bound(self):
        review = self.accepted_fixture()
        result = validate_review(review)
        report = {key: result[key] for key in ('dataset_id', 'clinical_protocol_sha256', 'source_file_sha256')}
        gate = clinical_claim_gate(review, report)
        self.assertFalse(gate['clinical_usefulness_established'])
        for field, value in [('dataset_id', 'mimic-iv-3.1'), ('clinical_protocol_sha256', '0' * 64),
                             ('source_file_sha256', {})]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                clinical_claim_gate(review, {**report, field: value})

    def test_acceptance_cannot_include_unincorporated_changes(self):
        review = self.accepted_fixture()
        review['decisions']['baseline']['requested_changes'] = 'Change threshold to 60.'
        with self.assertRaisesRegex(ValueError, 'unincorporated changes'):
            validate_review(review)

    def test_revisions_and_rejections_are_explicit(self):
        review = self.accepted_fixture()
        review['decisions']['baseline'].update(decision='REVISE', requested_changes='Change threshold to 60.')
        review.update(status='REVISION_REQUIRED', signoff=None)
        self.assertFalse(validate_review(review)['clinical_approval_recorded'])
        review['decisions']['baseline']['decision'] = 'REJECT'
        review['status'] = 'REJECTED'
        self.assertEqual(validate_review(review)['status'], 'REJECTED')

    def test_unknown_missing_or_stale_decisions_fail(self):
        for change in ('unknown', 'missing', 'stale'):
            review = self.accepted_fixture()
            if change == 'unknown':
                review['decisions']['extra'] = review['decisions']['baseline']
            elif change == 'missing':
                del review['decisions']['baseline']
            else:
                review['decisions']['baseline']['reviewer']['protocol_sha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_review(review)

    def test_artifact_edits_cannot_reuse_old_signature(self):
        review = self.accepted_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for binding in review['artifacts'].values():
                destination = root / binding['path']
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / binding['path']).read_bytes())
            candidate = root / review['artifacts']['candidate_plan']['path']
            candidate.write_text(candidate.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'Artifact changed'):
                validate_review(review, root)
            review['artifacts']['candidate_plan']['sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'Protocol must bind'):
                validate_review(review, root)

    def test_paths_cannot_escape_repository(self):
        self.template['artifacts']['protocol']['path'] = '../protocol.json'
        with self.assertRaisesRegex(ValueError, 'inside repository'):
            validate_review(self.template)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            path.write_text('{"status":"PENDING","status":"ACCEPTED"}')
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                read_json(path)


if __name__ == '__main__':
    unittest.main()
