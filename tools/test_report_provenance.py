"""The provenance gate has to fail on staleness, and has to be checking something."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import check_report_provenance as gate


class ReportProvenanceTests(unittest.TestCase):
    def test_the_committed_reports_bind_their_current_implementations(self):
        self.assertEqual(gate.main(), 0)

    def test_the_gate_is_actually_reading_reports(self):
        """A scanner that finds nothing passes every time. These floors are deliberately
        far below the current counts: they catch a gate that has stopped walking, not a
        report that was legitimately removed.
        """
        reports = [name for name in gate.tracked_reports() if name not in gate.HISTORICAL]
        self.assertGreater(len(reports), 20, 'the listing has stopped finding reports')
        bound = sum(len(mapping)
                    for name in reports
                    for mapping in gate.artifact_maps(
                        json.loads((gate.ROOT / name).read_text()), []))
        self.assertGreater(bound, 1000, 'the walk has stopped finding artifact maps')

    def test_a_stale_digest_fails(self):
        """The property the gate exists for: three of these reports had no reader at
        all, so their bindings drifted through several releases undetected.
        """
        real = gate.digests
        victim = 'patterns/pro_solid.py'
        with patch.object(gate, 'digests',
                          lambda path: {'0' * 64} if path == victim else real(path)):
            self.assertEqual(gate.main(), 1)

    def test_a_report_recording_its_own_failure_is_not_accepted(self):
        """A failed run publishes a stub, and the stub's digests are freshly computed.

        Several producers deliberately overwrite a stale success with a failure record,
        so the report that is sitting there can be an account of a run that did not
        happen. Its bindings match, so a gate that only compares digests reports it as
        current -- and goes quiet about the very measurement that was replaced. The
        profiler's stub carries a full artifact map of 73 bindings, so this is not a
        report that merely escapes the walk: it is counted as healthy.
        """
        for stub in ({'status': 'FAILED'}, {'passed': False}, {'failure_code': 'LINUX_PROC_REQUIRED'},
                     {'failure': {'stage': 'cold', 'code': 'WORKLOAD_JOB_INCOMPLETE'}}):
            with self.subTest(stub=stub):
                self.assertTrue(gate.records_failure({**stub, 'artifacts': {}}))

    def test_a_successful_report_is_not_mistaken_for_a_failure(self):
        """The predicate must not fire on healthy reports, or the gate gets switched off."""
        for report in ({'status': 'VERIFIED'}, {'status': 'MEASURED'}, {'passed': True},
                       {'status': 'COMPLETED_RECORDED_QUERY'}, {'failure': None}, {'failure_code': None}):
            with self.subTest(report=report):
                self.assertFalse(gate.records_failure(report))
        for name in gate.tracked_reports():
            if name in gate.HISTORICAL:
                continue
            report = json.loads((gate.ROOT / name).read_text())
            self.assertFalse(gate.records_failure(report), f'{name} is committed and should be healthy')

    def test_a_published_failure_makes_the_gate_fail(self):
        real = gate.loaded
        victim = next(name for name in gate.tracked_reports() if name not in gate.HISTORICAL)
        with patch.object(gate, 'loaded',
                          lambda name: {'status': 'FAILED', 'artifacts': {}} if name == victim else real(name)):
            self.assertEqual(gate.main(), 1)

    def test_either_digest_convention_is_accepted(self):
        """Some producers hash bytes and some hash read_text(), which normalizes CRLF.
        Failing a report for choosing the other convention would be a false alarm, and a
        gate that cries wolf gets switched off.
        """
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'crlf.csv'
            path.write_bytes(b'a,b\r\n1,2\r\n')
            with patch.object(gate, 'ROOT', Path(folder)):
                answers = gate.digests('crlf.csv')
        self.assertEqual(len(answers), 2, 'a CRLF file must offer both readings')
        self.assertIn(hashlib.sha256(b'a,b\r\n1,2\r\n').hexdigest(), answers)
        self.assertIn(hashlib.sha256(b'a,b\n1,2\n').hexdigest(), answers)

    def test_the_historical_manifest_is_exempt_and_still_stale(self):
        """It is exempt because it describes the tree at its own release, which is the
        point of keeping it -- not because it was convenient to skip.
        """
        self.assertIn('verification/v24-release-manifest.json', gate.HISTORICAL)
        manifest = json.loads(
            (gate.ROOT / 'verification/v24-release-manifest.json').read_text())
        drifted = [name for name, recorded in manifest['files'].items()
                   if recorded not in gate.digests(name)]
        self.assertTrue(drifted, 'if it still matched, the exemption would be untested')

    def test_artifact_maps_are_found_by_shape_not_by_name(self):
        """Producers name these artifacts, mapping_files, fixture_sha256 and more, so
        keying off a name would silently skip whichever one is added next.
        """
        digest = hashlib.sha256(b'x').hexdigest()
        real = 'patterns/pro_solid.py'
        found = gate.artifact_maps({'oddly_named_block': {real: digest}}, [])
        self.assertEqual(found, [{real: digest}])
        self.assertEqual(gate.artifact_maps({'not_a_map': {'nope.py': digest}}, []), [],
                         'a path that is not a file must not be read as a binding')

    def test_nested_maps_inside_lists_are_found(self):
        digest = hashlib.sha256(b'x').hexdigest()
        real = 'patterns/pro_solid.py'
        found = gate.artifact_maps({'runs': [{'artifacts': {real: digest}}]}, [])
        self.assertEqual(found, [{real: digest}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
