"""Persistence, atomicity and fail-closed retention tests without real patient data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.recorded_store import RecordedStore, MAX_PAYLOAD_BYTES
from patterns.claim_rdf import digest


class RecordedStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state' / 'jobs.sqlite3'
        self.config = {'mode': 'authored-test', 'version': 1}
        self.key = 'a' * 32
        self.service_id = 'b' * 32
        self.store = RecordedStore(self.path, self.config)
        self.addCleanup(lambda: self.store.close() if self.store else None)

    def complete(self, key=None):
        key = key or self.key
        source = {'authored': True}
        source_id = digest(source)
        query = {'session_id': source_id, 'controls': {}}
        query_id = digest(query)
        result = {'status': 'COMPLETED', 'anchors': [], 'anchors_verified': 0,
                  'anchors_total': 0, 'clinical_mapping_verified': False,
                  'context': query, 'context_id': query_id}
        snapshot = {'profile': 'literal', 'job_id': key, 'source_context': source,
                    'session_context_id': source_id, 'query_context': query,
                    'query_context_id': query_id, 'temporal_result': result, 'details': []}
        return {'id': key, 'status': 'COMPLETED', 'summary': result}, snapshot

    def start(self, key=None):
        key = key or self.key
        self.store.record_started(key, {'profile': 'literal', 'controls': {'threshold': '65'}})
        self.store.record_running(key, self.service_id)
        return key

    def reopen(self, **kwargs):
        self.store.close()
        self.store = RecordedStore(self.path, self.config, **kwargs)

    def test_complete_evidence_survives_restart_and_mutating_return(self):
        self.start()
        job, snapshot = self.complete()
        self.store.record_complete(self.key, job, snapshot)
        self.reopen()
        first = self.store.get(self.key)
        self.assertEqual(first['state'], 'completed')
        self.assertEqual(first['snapshot'], snapshot)
        first['snapshot']['details'].append({'corrupt': True})
        self.assertEqual(self.store.get(self.key)['snapshot'], snapshot)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.recover(), [])

    def test_recovery_marks_unfinished_and_preserves_stable_id(self):
        self.start()
        queued = 'c' * 32
        self.store.record_started(queued, {'profile': 'literal'})
        self.reopen()
        recovered = self.store.recover()
        self.assertEqual({r['id'] for r in recovered}, {self.key, queued})
        self.assertTrue(all(r['state'] == 'interrupted' and r['snapshot'] is None for r in recovered))
        self.store.record_running(self.key, 'd' * 32)
        self.assertEqual(self.store.get(self.key)['service_id'], 'd' * 32)
        self.assertEqual(self.store.get(self.key)['id'], self.key)

    def test_injected_partial_write_rolls_back_transition_and_audit(self):
        self.start()
        before = self.store.get(self.key)
        events = self.store.audit_events()
        with patch.object(self.store, '_audit', side_effect=OSError('authored failure')):
            with self.assertRaises(OSError):
                self.store.record_complete(self.key, *self.complete())
        self.assertEqual(self.store.get(self.key), before)
        self.assertEqual(self.store.audit_events(), events)
        self.reopen()
        self.assertEqual(self.store.get(self.key), before)

    def test_corrupted_payload_and_metadata_fail_closed(self):
        self.start()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (b'{"changed":true}', self.key))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.store.get(self.key)
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.store.list_jobs()

    def test_partial_or_foreign_completed_evidence_not_published(self):
        self.start()
        job, snapshot = self.complete()
        for mutate in (
            lambda s: s['temporal_result'].update(anchors_total=1),
            lambda s: s.update(job_id='f' * 32),
            lambda s: s.update(profile='reviewed'),
            lambda s: s.update(session_context_id='bad'),
        ):
            changed = deepcopy(snapshot)
            mutate(changed)
            with self.assertRaises(ValueError):
                self.store.record_complete(self.key, job, changed)
        self.assertEqual(self.store.get(self.key)['state'], 'running')

    def test_config_and_version_mix_refused(self):
        self.store.close()
        with self.assertRaisesRegex(ValueError, 'configuration'):
            RecordedStore(self.path, {'mode': 'other'})
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
        with self.assertRaisesRegex(ValueError, 'schema version'):
            RecordedStore(self.path, self.config)

    def test_retention_eviction_does_not_evict_active_jobs(self):
        self.reopen(max_jobs=2)
        self.start()
        second = self.start('c' * 32)
        with self.assertRaisesRegex(ValueError, 'active jobs'):
            self.start('d' * 32)
        self.assertEqual(len(self.store.list_jobs()), 2)
        self.store.record_failure(second)
        self.start('d' * 32)
        with self.assertRaises(KeyError):
            self.store.get(second)
        self.assertEqual(self.store.get(self.key)['state'], 'running')
        self.assertTrue(any(e['action'] == 'evicted' for e in self.store.audit_events()))

    def test_payload_limit_and_byte_retention_are_atomic(self):
        with self.assertRaisesRegex(ValueError, '8 MiB'):
            self.store.record_started(self.key, {'profile': 'literal', 'oversized': 'x' * MAX_PAYLOAD_BYTES})
        self.assertEqual(self.store.list_jobs(), [])
        self.reopen(max_bytes=700)
        self.start()
        with self.assertRaises(ValueError):
            self.store.record_complete(self.key, *self.complete())
        self.assertEqual(self.store.get(self.key)['state'], 'running')

    def test_audit_only_contains_bounded_identifiers_and_retention(self):
        self.reopen(max_audit=3)
        for _ in range(5):
            self.store.audit('owner', 'history_read')
        events = self.store.audit_events()
        self.assertEqual(len(events), 3)
        self.assertEqual([e['sequence'] for e in events], [3, 4, 5])
        with self.assertRaises(ValueError):
            self.store.audit('owner', 'password=not-a-secret')
        self.assertEqual(len(self.store.audit_events()), 3)
        self.assertEqual(set(events[0]), {'sequence', 'timestamp', 'actor', 'action', 'job_id', 'status'})

    def test_unsafe_state_permissions_and_symlinks_refused(self):
        self.store.close()
        self.path.parent.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'owner-only'):
            RecordedStore(self.path, self.config)
        self.path.parent.chmod(0o700)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'owner-only'):
            RecordedStore(self.path, self.config)
        self.path.chmod(0o600)
        linked = Path(self.temp.name) / 'link'
        linked.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            RecordedStore(linked / 'jobs.sqlite3', self.config)

    def test_exclusive_instance_lock_prevents_false_recovery(self):
        self.start()
        with self.assertRaisesRegex(ValueError, 'running instance'):
            RecordedStore(self.path, self.config)
        self.assertEqual(self.store.get(self.key)['state'], 'running')

    def test_duplicate_and_invalid_terminal_transition_rejected(self):
        self.start()
        with self.assertRaises(ValueError):
            self.store.record_started(self.key, {'profile': 'literal'})
        self.store.record_complete(self.key, *self.complete())
        with self.assertRaises(ValueError):
            self.store.record_failure(self.key)
        with self.assertRaises(ValueError):
            self.store.record_running(self.key, self.service_id)



class DurableJourneyTests(unittest.TestCase):
    def test_pattern_source_drift_blocks_revision_but_retained_evidence_remains(self):
        from app.recorded_journey import RecordedJourneyWorkspace
        from demo.pressure import PressureService
        import shutil
        import time
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'source'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/source-mixed-query', folder)
            service = PressureService(synthetic=True)
            service.folder = folder
            workspace = RecordedJourneyWorkspace(literal_service=service, state_dir=Path(directory) / 'state')
            try:
                job = workspace.start({'profile': 'literal', 'stratum': 'synthetic',
                    'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
                deadline = time.monotonic() + 30
                while job['status'] == 'RUNNING' and time.monotonic() < deadline:
                    time.sleep(.01)
                    job = workspace.get('literal', job['id'])
                self.assertEqual(job['status'], 'COMPLETED')
                form = workspace.pattern_metadata('literal', job['id'])['default_pattern']
                first = workspace.pattern({'profile': 'literal', 'job_id': job['id'], 'pattern': form})
                before = workspace.snapshot('literal', first['id'])
                source_file = next(folder.glob('*.csv'))
                with source_file.open('a') as handle:
                    handle.write('\n')
                with self.assertRaises(ValueError):
                    workspace.pattern({'profile': 'literal', 'job_id': first['id'], 'pattern': form})
                self.assertEqual(workspace.snapshot('literal', first['id']), before)
            finally:
                workspace.close()

    def test_worker_persists_without_poll_and_restart_restores_evidence(self):
        from app.recorded_journey import RecordedJourneyWorkspace
        with tempfile.TemporaryDirectory() as directory:
            workspace = RecordedJourneyWorkspace(state_dir=directory)
            request = {'profile': 'literal', 'stratum': 'synthetic',
                       'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}}
            job = workspace.start(request)
            key = job['id']
            # close drains the service worker plus its persistence callback; no polling.
            workspace.close()
            restored = RecordedJourneyWorkspace(state_dir=directory)
            try:
                completed = restored.get('literal', key)
                self.assertEqual(completed['status'], 'COMPLETED')
                snapshot = restored.snapshot('literal', key)
                self.assertEqual(snapshot['job_id'], key)
                self.assertEqual(restored._service('literal').jobs, {})
                token = snapshot['details'][0]['anchor']['token']
                self.assertTrue(restored.inspect('literal', key, token)['observations'])
                self.assertEqual(restored.history()['jobs'][0]['id'], key)
                self.assertFalse(restored.history()['jobs'][0]['resumable'])
                # Persist an accepted intent, then model process interruption.
                interrupted = 'f' * 32
                restored.store.record_started(interrupted, request)
            finally:
                restored.close()
            recovered = RecordedJourneyWorkspace(state_dir=directory)
            try:
                self.assertEqual(recovered.get('literal', interrupted)['status'], 'INTERRUPTED')
                restarted = recovered.resume({'job_id': interrupted})
                self.assertEqual(restarted['id'], interrupted)
            finally:
                recovered.close()
            final = RecordedJourneyWorkspace(state_dir=directory)
            try:
                self.assertEqual(final.get('literal', interrupted)['status'], 'COMPLETED')
                self.assertEqual(len(final.history()['jobs']), 2)
            finally:
                final.close()

if __name__ == '__main__':
    unittest.main()
