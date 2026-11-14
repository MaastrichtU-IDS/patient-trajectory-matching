"""The build fingerprint (D33): it changes exactly when code a build reaches changes.

Each test copies the modules into a scratch tree, edits one definition, and compares the fingerprints of the scaling
benchmark's build phase (`tools/benchmark_execution_store.py`, `BUILD_ROOTS`) before and after.
"""
from pathlib import Path
import shutil
import tempfile
import unittest

from . import build_fingerprint as bf

ROOT = Path(__file__).resolve().parents[1]
ROOTS = {'tools/benchmark_execution_store.py': ['build_worker']}


class BuildFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fingerprint-')
        self.root = Path(self.tmp.name)
        for folder in ('patterns', 'tools'):
            (self.root / folder).mkdir()
            for path in (ROOT / folder).glob('*.py'):
                shutil.copy(path, self.root / folder / path.name)
        for path in bf.fingerprints(ROOTS)[1]:  # the data files the build reads
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / path, self.root / path)
        self.before, self.files = bf.fingerprints(ROOTS, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, path, old, new):
        target = self.root / path
        text = target.read_text(encoding='utf-8')
        self.assertEqual(text.count(old), 1, old)
        target.write_text(text.replace(old, new), encoding='utf-8')
        return bf.fingerprints(ROOTS, self.root)[0]

    def changed(self, after):
        return sorted(p for p in set(self.before) | set(after) if self.before.get(p) != after.get(p))

    def test_it_reaches_the_store_the_converter_and_the_engine_validation(self):
        self.assertEqual(sorted(self.before), ['patterns/_resources.py', 'patterns/execution_store.py',
                                               'patterns/series_values.py', 'patterns/synthetic_ingest.py',
                                               'patterns/trajectory_graph.py', 'tools/benchmark_execution_store.py'])
        self.assertIn('StoreBuilder', self.before['patterns/execution_store.py']['roots'])
        self.assertIn('patient_times', self.before['patterns/trajectory_graph.py']['roots'])
        self.assertIn('schemas/execution-store-manifest.schema.json', self.files)
        self.assertIn('demos/mockups/synthetic/generate.py', self.files)
        for path, record in self.before.items():  # the gate recomputes each module from its recorded roots
            self.assertEqual(bf.fingerprint(path, record['roots'], self.root), record['sha256'], path)

    def test_a_change_to_a_definition_the_build_reaches_changes_it(self):
        after = self.edit('patterns/execution_store.py', 'def _derive(db, relation, parts, on_stage=None):\n',
                          'def _derive(db, relation, parts, on_stage=None):\n    pass\n')
        self.assertEqual(self.changed(after), ['patterns/execution_store.py'])

    def test_a_change_to_a_constant_the_build_reads_changes_it(self):
        after = self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', 'BUCKETS = 9\n')
        self.assertEqual(self.changed(after), ['patterns/execution_store.py'])

    def test_a_change_to_matching_alone_does_not(self):
        after = self.edit('patterns/execution_store.py', '    if not (type(workers) is int and workers >= 1):\n'
                          "        raise StoreError('INVALID_WORKERS', repr(workers))\n    if detail not in DETAILS:\n",
                          '    if not (type(workers) is int and workers >= 1):\n'
                          "        raise StoreError('INVALID_WORKERS', repr(workers))\n    pass\n    if detail not in DETAILS:\n")
        self.assertEqual(self.changed(after), [])
        after = self.edit('patterns/execution_store.py', "DERIVED_RULES = {\n", "UNUSED = 1\nDERIVED_RULES = {\n")
        self.assertEqual(self.changed(after), [])

    def test_the_engine_counts_only_where_the_build_validates_with_it(self):
        after = self.edit('patterns/trajectory_graph.py', 'def patient_times(patient, trusted=False):\n',
                          'def patient_times(patient, trusted=False):\n    pass\n')
        self.assertEqual(self.changed(after), ['patterns/trajectory_graph.py'])
        after = self.edit('patterns/trajectory_graph.py', 'def applied(query, relaxations):\n',
                          'def applied(query, relaxations):\n    pass\n')
        self.assertEqual(self.changed(after), ['patterns/trajectory_graph.py'])  # only the earlier edit
        self.before = after
        after = self.edit('patterns/trajectory_graph.py', 'def match_prepared(', 'def match_prepared_(')
        self.assertEqual(self.changed(after), [])

    def test_the_benchmarks_own_build_code_counts_and_its_query_code_does_not(self):
        after = self.edit('tools/benchmark_execution_store.py', 'def shifted(patient, k):\n',
                          'def shifted(patient, k):\n    pass\n')
        self.assertEqual(self.changed(after), ['tools/benchmark_execution_store.py'])
        self.before = after
        after = self.edit('tools/benchmark_execution_store.py', 'def query_statistics(result, patients):\n',
                          'def query_statistics(result, patients):\n    pass\n')
        self.assertEqual(self.changed(after), [])

    def test_a_name_reached_only_through_a_string_is_refused(self):
        target = self.root / 'patterns/execution_store.py'
        target.write_text(target.read_text().replace('def _derive(db, relation, parts, on_stage=None):\n',
                                                     "def _derive(db, relation, parts, on_stage=None):\n    getattr(db, 'x')\n"))
        with self.assertRaises(bf.FingerprintError) as raised:
            bf.fingerprints(ROOTS, self.root)
        self.assertEqual(raised.exception.code, 'DYNAMIC_REFERENCE')

    def test_an_unknown_root_is_refused(self):
        with self.assertRaises(bf.FingerprintError) as raised:
            bf.fingerprint('patterns/execution_store.py', ['NoSuchBuilder'], self.root)
        self.assertEqual(raised.exception.code, 'UNKNOWN_ROOT')


if __name__ == '__main__':
    unittest.main()
