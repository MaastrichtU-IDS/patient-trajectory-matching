"""Adversarial traceability tests: missing obligations and invented readiness fail."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.check_completion import ROOT, validate


class CompletionAuditTests(unittest.TestCase):
    def setUp(self):
        self.register=json.loads((ROOT/'verification/completion-register.json').read_text())

    def test_consistent_partial_project_is_not_reported_complete(self):
        a=validate(self.register)
        self.assertTrue(a['audit_consistent'])
        self.assertFalse(a['project_complete'])
        self.assertFalse(a['acceptance_commands_executed'])
        self.assertEqual(a['requirements_total'],104)
        self.assertEqual(a,validate(copy.deepcopy(self.register)))

    def test_removed_requirement_and_duplicate_are_rejected(self):
        for mutate in (lambda r:r['requirements'].pop(),lambda r:r['requirements'].append(copy.deepcopy(r['requirements'][0]))):
            r=copy.deepcopy(self.register);mutate(r)
            with self.assertRaises(ValueError):validate(r)

    def test_renaming_or_rewording_original_obligations_fails(self):
        for field in ('requirement','release','acceptance_gates','status'):
            r=copy.deepcopy(self.register);r['requirements'][0]['legacy'][field]='easier substitute'
            with self.assertRaisesRegex(ValueError,'Legacy requirement'):validate(r)

    def test_old_source_digest_requires_explicit_reassessment(self):
        self.register['requirements_source']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'digest changed'):validate(self.register)

    def test_supported_claim_cannot_keep_open_work(self):
        row=next(r for r in self.register['requirements'] if r['status']=='partial')
        row['status']='supported'
        with self.assertRaisesRegex(ValueError,'Supported requirement'):validate(self.register)

    def test_missing_external_approval_is_named_not_a_failure(self):
        self.assertIn('D-CLINICAL',validate(self.register)['unsatisfied_external_dependencies'])
        row=next(r for r in self.register['requirements'] if r['status']=='blocked');row['dependencies']=[]
        with self.assertRaisesRegex(ValueError,'named external dependency'):validate(self.register)

    def test_missing_and_outside_repository_evidence_rejected(self):
        for path in ('not-present.acceptance','../external-proof.json','/etc/passwd'):
            r=copy.deepcopy(self.register);r['requirements'][0]['evidence']=[path]
            with self.assertRaises(ValueError):validate(r)

    def test_unknown_check_and_status_rejected(self):
        r=copy.deepcopy(self.register);r['requirements'][0]['acceptance_checks']=['invented-test']
        with self.assertRaisesRegex(ValueError,'Unknown acceptance'):validate(r)
        r=copy.deepcopy(self.register);r['requirements'][0]['status']='complete-enough'
        with self.assertRaisesRegex(ValueError,'Unsupported status'):validate(r)

    def test_h0_cannot_omit_second_refinement(self):
        self.register['h0_acceptance']['required_journey'].remove('second_refinement')
        with self.assertRaisesRegex(ValueError,'H0 journey'):validate(self.register)

    def test_green_register_cannot_claim_production_or_clinical_readiness(self):
        for field in ('project_complete','production_ready','clinical_validity_claim'):
            r=copy.deepcopy(self.register);r[field]=True
            with self.assertRaisesRegex(ValueError,'readiness'):validate(r)

    def test_stale_h0_execution_report_cannot_establish_integration(self):
        report=json.loads((ROOT/self.register['h0_acceptance']['verification_report']).read_text())
        report['artifacts'][next(iter(report['artifacts']))]='0'*64
        with patch('tools.check_completion.json.loads',return_value=report):
            with self.assertRaisesRegex(ValueError,'Stale H0 acceptance evidence'):
                validate(self.register)

    def test_cli_cannot_overwrite_code_or_evidence(self):
        for path in (ROOT/'README.md', ROOT/'tools/test_check_completion.py', ROOT/'verification/completion-register.json'):
            before=path.read_bytes()
            run=subprocess.run([sys.executable,str(ROOT/'tools/check_completion.py'),'--output',str(path)],capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(path.read_bytes(),before)

    def test_cli_outputs_machine_readable_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'nested/report.json'
            run=subprocess.run([sys.executable,str(ROOT/'tools/check_completion.py'),'--output',str(output)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertFalse(json.loads(output.read_text())['project_complete'])
            self.assertTrue(json.loads(run.stdout)['audit_consistent'])


if __name__=='__main__':unittest.main()
