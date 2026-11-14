"""The provenance gate has to fail on staleness, and has to be checking something."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import check_report_provenance as gate


def bindings():
    """Every binding the gate reads: artifact map entries and build fingerprint entries (D34)."""
    total = 0
    for name in gate.tracked_reports():
        if name in gate.HISTORICAL:
            continue
        report = gate.loaded(name) or {}
        total += sum(len(m) for m in gate.artifact_maps(report, []))  # a build record's maps among them
    return total


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
        self.assertEqual(gate.artifact_maps({'not_a_map': {'nope': digest}}, []), [],
                         'a key without a file suffix is not a path')

    def test_a_map_naming_a_missing_file_is_still_a_map(self):
        """A moved file must not make its map invisible: that is how a gate passes with
        fewer bindings and nobody notices."""
        digest = hashlib.sha256(b'x').hexdigest()
        mapping = {'patterns/pro_solid.py': digest, 'patterns/moved_away.py': digest}
        self.assertEqual(gate.artifact_maps({'artifacts': mapping}, []), [mapping])
        self.assertEqual(gate.artifact_maps({'pins': {'icu/inputevents.csv.gz': digest}}, []), [],
                         'an external source keyed outside the tree is not a repository binding')

    def test_moving_a_bound_file_fails_the_gate(self):
        """Simulate moving one bound file: the gate must fail and name it, and the count
        of bindings it read must not drop."""
        before = bindings()
        target = 'services/pressure.py'
        real = Path.is_file
        with patch.object(Path, 'is_file', lambda self: False if str(self).endswith(target) else real(self)), \
             patch('builtins.print') as printed:
            self.assertEqual(gate.main(), 1)
        lines = ' '.join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn(f'{target} is bound but no longer in the repository', lines)
        self.assertIn(f'{before} bindings', lines, 'a moved file must not shrink the walk')

    def test_moving_a_module_bound_by_a_build_record_fails_the_gate_without_shrinking_the_walk(self):
        """D34 (the D33 review's must-fix 2): the count includes a build record's entries, and a module bound only by
        the record, once moved, fails by name."""
        before = bindings()
        recorded = [r for name in gate.tracked_reports() if name not in gate.HISTORICAL
                    for r in gate.build_records(gate.loaded(name) or {}, [])]
        self.assertTrue(recorded and all('patterns/build_record.py' in r['modules'] for r in recorded))
        target = 'patterns/build_record.py'
        real = Path.is_file
        with patch.object(Path, 'is_file', lambda self: False if str(self).endswith(target) else real(self)), \
             patch('builtins.print') as printed:
            self.assertEqual(gate.main(), 1)
        lines = ' '.join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn(target, lines)
        self.assertIn(f'{before} bindings', lines, 'a moved module must not shrink the walk')

    def test_a_folder_that_no_longer_exists_still_binds(self):
        """Moving a whole top-level folder removes its name from the tree. Its bindings
        must still be read, and fail, rather than stop looking like repository paths."""
        digest = hashlib.sha256(b'x').hexdigest()
        gone = {'demo.old/README.md': digest, 'no_such_folder/module.py': digest}
        self.assertEqual(gate.artifact_maps({'artifacts': gone}, []), [gone])
        with patch.object(gate, 'tracked_reports', return_value=['verification/fake.json']), \
             patch.object(gate, 'loaded', return_value={'artifacts': gone}), \
             patch('builtins.print') as printed:
            self.assertEqual(gate.main(), 1)
        lines = ' '.join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn('no_such_folder/module.py is bound but no longer in the repository', lines)

    def test_nested_maps_inside_lists_are_found(self):
        digest = hashlib.sha256(b'x').hexdigest()
        real = 'patterns/pro_solid.py'
        found = gate.artifact_maps({'runs': [{'artifacts': {real: digest}}]}, [])
        self.assertEqual(found, [{real: digest}])


class BuildRecordGateTests(unittest.TestCase):
    """D34: a report may bind its build phase by a runtime record; its maps are checked as artifact maps, its shape too."""

    def fake(self, report):
        with patch.object(gate, 'tracked_reports', return_value=['verification/fake.json']), \
             patch.object(gate, 'loaded', return_value=report), patch('builtins.print') as printed:
            code = gate.main()
        return code, ' '.join(str(call.args[0]) for call in printed.call_args_list)

    def record(self):
        return {'rule': 'runtime_record_v1',
                'modules': {p: hashlib.sha256((gate.ROOT / p).read_bytes()).hexdigest()
                            for p in ('patterns/execution_store.py', 'patterns/trajectory_times.py')},
                'files': {p: hashlib.sha256((gate.ROOT / p).read_bytes()).hexdigest()
                          for p in ('schemas/execution-store-manifest.schema.json',)},
                'versions': {'python': 'cpython 3.12'}, 'root': 'checkout', 'settings': {'staging': 'parquet'}}

    def test_a_current_record_passes_and_every_kind_of_staleness_fails(self):
        code, text = self.fake({'build_provenance': self.record()})
        self.assertEqual(code, 0, text)
        self.assertIn('3 bindings', text)
        cases = []
        stale = self.record()
        stale['modules']['patterns/execution_store.py'] = '0' * 64
        cases.append((stale, 'patterns/execution_store.py no longer matches its recorded digest'))
        gone = self.record()
        gone['modules']['patterns/no_such_module.py'] = '0' * 64
        cases.append((gone, 'patterns/no_such_module.py is bound but no longer in the repository'))
        cases.append(({**self.record(), 'rule': 'build_closure_v1'}, 'unknown rule'))
        cases.append(({**self.record(), 'reused_from': 'x'}, 'a build record has keys'))
        empty = self.record()
        empty['files'] = {}
        cases.append((empty, "files is not a map of repository paths to digests"))
        for record, expected in cases:
            code, text = self.fake({'build_provenance': record})
            self.assertEqual(code, 1, expected)
            self.assertIn(expected, text)

    def test_the_static_fingerprint_is_refused_as_an_unknown_rule(self):
        code, text = self.fake({'build_provenance': {'rule': 'build_closure_v1', 'entry_points': {},
                                                     'fingerprints': {}, 'files': {}}})
        self.assertEqual(code, 1)
        self.assertIn('unknown rule', text)

    def test_the_committed_scaling_report_binds_its_build_by_record(self):
        report = json.loads((gate.ROOT / 'verification/execution-store-scaling-report.json').read_text())
        records = gate.build_records(report, [])
        self.assertEqual(len(records), 1)
        self.assertIn('patterns/execution_store.py', records[0]['modules'])
        self.assertNotIn('patterns/trajectory_graph.py', records[0]['modules'])
        self.assertEqual(gate.record_problems(records[0]), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
