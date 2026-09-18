"""Completion is public only after supplemental admission and durable retention."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.recorded_journey import RecordedJourneyWorkspace

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'examples/clinical-features/authored-pack.json'
REQUEST = {'profile': 'literal', 'stratum': 'synthetic',
           'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}}


class CompletionAdmissionTests(unittest.TestCase):
    def pending_admission(self, workspace, callback='_capture_completed'):
        """Pause only the adapter callback; run the real underlying engine to completion."""
        with patch.object(workspace, callback, return_value=None):
            job = workspace.start(REQUEST)
            service = workspace._service('literal')
            service.pool.submit(lambda: None).result(timeout=60)
            service_id = (workspace.store.get(job['id'])['service_id']
                          if workspace.store else job['id'])
            underlying = service.get(service_id)
            self.assertEqual(underlying['status'], 'COMPLETED', underlying)
        return job, underlying, service_id

    def test_history_and_inspection_cannot_publish_unadmitted_completion(self):
        workspace = RecordedJourneyWorkspace(clinical_features_path=PACK)
        try:
            for route in ('history', 'inspect'):
                with self.subTest(route=route):
                    job, underlying, service_id = self.pending_admission(workspace)
                    token = underlying['summary']['anchors'][0]['token']
                    with patch.object(workspace.clinical_features, 'bind',
                                      side_effect=ValueError('simulated feature admission failure')):
                        if route == 'history':
                            history = workspace.history()
                            listed = next(row for row in history['jobs'] if row['id'] == job['id'])
                            self.assertEqual(listed['status'], 'FAILED')
                        else:
                            with self.assertRaises(ValueError):
                                workspace.inspect('literal', job['id'], token)
                        self.assertEqual(workspace.get('literal', job['id'])['status'], 'FAILED')
                        with self.assertRaises(ValueError):
                            workspace.snapshot('literal', job['id'])
                        self.assertNotIn('summary', workspace._service('literal').get(service_id))
        finally:
            workspace.close()

    def test_rejected_pattern_cannot_leave_a_completed_job(self):
        workspace = RecordedJourneyWorkspace(clinical_features_path=PACK)
        try:
            job = workspace.start(REQUEST)
            workspace._service('literal').pool.submit(lambda: None).result(timeout=60)
            self.assertEqual(workspace.get('literal', job['id'])['status'], 'COMPLETED')
            retained = workspace.snapshot('literal', job['id'])
            form = workspace.pattern_metadata('literal', job['id'])['default_pattern']
            with patch.object(workspace.clinical_features, 'bind',
                              side_effect=ValueError('simulated feature admission failure')):
                with self.assertRaises(ValueError):
                    workspace.pattern({'profile': 'literal', 'job_id': job['id'], 'pattern': form})
                new = [row for row in workspace.history()['jobs'] if row['id'] != job['id']]
                self.assertEqual(len(new), 1)
                self.assertEqual(new[0]['status'], 'FAILED')
                failed = workspace.get('literal', new[0]['id'])
                self.assertNotIn('summary', failed)
                with self.assertRaises(ValueError):
                    workspace.snapshot('literal', new[0]['id'])
                self.assertEqual(workspace.snapshot('literal', job['id']), retained)
        finally:
            workspace.close()

    def test_evicted_service_completion_becomes_terminal_durable_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = RecordedJourneyWorkspace(clinical_features_path=PACK, state_dir=Path(folder) / 'state')
            try:
                job, _, service_id = self.pending_admission(workspace, '_persist_finished')
                service = workspace._service('literal')
                with service.lock:
                    del service.jobs[service_id]
                result = workspace.get('literal', job['id'])
                self.assertEqual(result['status'], 'FAILED')
                self.assertEqual(result['error'], 'service_job_expired')
                self.assertEqual(workspace.store.get(job['id'])['state'], 'failed')
                self.assertFalse(workspace.history()['jobs'][0]['resumable'])
            finally:
                workspace.close()
            restored = RecordedJourneyWorkspace(clinical_features_path=PACK, state_dir=Path(folder) / 'state')
            try:
                self.assertEqual(restored.get('literal', job['id'])['status'], 'FAILED')
                self.assertFalse(restored.history()['jobs'][0]['resumable'])
            finally:
                restored.close()


if __name__ == '__main__':
    unittest.main()
