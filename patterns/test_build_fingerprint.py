"""The build fingerprint (D34): it changes exactly when code a build reaches changes, and refuses what it cannot follow.

Each test copies the modules into a scratch tree, edits it, and compares the fingerprints of the scaling benchmark's
build phase (`tools/benchmark_execution_store.py`, `BUILD_ROOTS`) before and after. The bypasses the D33 review found
each have a test.
"""
import ast
from pathlib import Path
import shutil
import tempfile
import unittest

from . import build_fingerprint as bf

ROOT = Path(__file__).resolve().parents[1]
ROOTS = {'tools/scaling_build_worker.py': ['main'],
         'tools/benchmark_execution_store.py': ['build_worker', 'build_size', 'run_phase', 'current_rss', 'limit_bytes']}


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

    def write(self, path, old, new):
        target = self.root / path
        text = target.read_text(encoding='utf-8')
        self.assertEqual(text.count(old), 1, old)
        target.write_text(text.replace(old, new), encoding='utf-8')

    def edit(self, path, old, new):
        self.write(path, old, new)
        return bf.fingerprints(ROOTS, self.root)[0]

    def changed(self, after):
        return sorted(p for p in set(self.before) | set(after) if self.before.get(p) != after.get(p))

    def refused(self, path, old, new):
        self.write(path, old, new)
        with self.assertRaises(bf.FingerprintError) as raised:
            bf.fingerprints(ROOTS, self.root)
        return raised.exception.code

    def test_the_tools_roots_are_these(self):
        from tools import benchmark_execution_store as bench
        self.assertEqual(bench.BUILD_ROOTS, ROOTS)
        self.assertEqual(sorted(self.files), sorted(bench.BUILD_FILES))

    def test_it_reaches_the_store_the_converter_and_the_engine_validation(self):
        self.assertEqual(sorted(self.before), ['patterns/_resources.py', 'patterns/execution_store.py',
                                               'patterns/series_values.py', 'patterns/synthetic_ingest.py',
                                               'patterns/trajectory_graph.py', 'tools/benchmark_execution_store.py',
                                               'tools/scaling_build_worker.py'])
        self.assertIn('StoreBuilder', self.before['patterns/execution_store.py']['roots'])
        self.assertIn('patient_times', self.before['patterns/trajectory_graph.py']['roots'])
        self.assertNotIn('match', self.before['patterns/execution_store.py']['roots'])
        self.assertNotIn('query_worker', self.before['tools/benchmark_execution_store.py']['roots'])

    def test_the_build_modules_re_export_nothing(self):
        """A name imported from a module of this repository is a definition of that module, never itself an import
        there, so no re-export goes unfollowed (the fingerprint's stated limit)."""
        followed = 0
        for path in self.before:
            tree = ast.parse((self.root / path).read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias, other, name in bf._imports(node, path, self.root):
                        if name is not None:
                            followed += 1
                            self.assertIn(name, bf._Module(other, self.root).defs, (path, alias, other))
        self.assertGreater(followed, 0)

    # The D33 review's bypasses (must-fix 1): each now changes the fingerprint or is refused.

    def test_a_conditional_reassignment_at_top_level_changes_it(self):
        after = self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', 'BUCKETS = 8\nif True:\n    BUCKETS = 9\n')
        self.assertEqual(self.changed(after), ['patterns/execution_store.py'])

    def test_a_top_level_write_through_globals_is_refused(self):
        self.assertEqual(self.refused('patterns/execution_store.py', 'BUCKETS = 8\n',
                                      "BUCKETS = 8\nglobals()['BUCKETS'] = 9\n"), 'DYNAMIC_REFERENCE')

    def test_an_edit_in_the_build_process_entry_point_changes_it(self):
        old = '    marker = args.worker_output.with_suffix(\'.progress\')\n'
        after = self.edit('tools/scaling_build_worker.py', old,
                          old + '    from patterns import execution_store as es\n    es.BUCKETS = 9\n')
        self.assertIn('tools/scaling_build_worker.py', self.changed(after))
        self.tearDown()
        self.setUp()
        after = self.edit('tools/scaling_build_worker.py', 'args.memory_limit, marker, args.staging)',
                          "args.memory_limit, marker, 'ndjson')")
        self.assertEqual(self.changed(after), ['tools/scaling_build_worker.py'])

    def test_the_benchmarks_own_dispatch_cannot_reach_a_build(self):
        """The benchmark's `main` is not a root, and needs not be: it no longer runs the build worker, which runs in its
        own script, so an edit there changes no build."""
        from tools import benchmark_execution_store as bench
        self.assertNotIn('build', [a for a in bench.main.__code__.co_consts if isinstance(a, str)])
        after = self.edit('tools/benchmark_execution_store.py', "        if args.worker == 'verify':",
                          "        BUCKETS = 9\n        if args.worker == 'verify':")
        self.assertEqual(self.changed(after), [])

    def test_an_import_swapped_in_a_module_the_build_reaches_changes_it(self):
        text = (self.root / 'patterns/series_values.py').read_text()
        line = next(l for l in text.splitlines() if l.startswith(('from decimal import', 'import decimal')))
        after = self.edit('patterns/series_values.py', line + '\n', line.replace('decimal', '_pydecimal', 1) + '\n')
        self.assertEqual(self.changed(after), ['patterns/series_values.py'])

    def test_a_change_to_how_a_build_is_measured_changes_it(self):
        after = self.edit('tools/benchmark_execution_store.py', "record = {'wall_seconds': seconds, 'peak_rss_bytes': max(maxrss, peak)}",
                          "record = {'wall_seconds': seconds, 'peak_rss_bytes': peak}")
        self.assertEqual(self.changed(after), ['tools/benchmark_execution_store.py'])

    # The earlier cases.

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

    def test_names_reached_through_strings_are_refused(self):
        anchor = 'def _derive(db, relation, parts, on_stage=None):\n'
        for line in ("    getattr(db, 'x')\n", "    setattr(db, 'x', 1)\n", "    import importlib\n    importlib.import_module('x')\n",
                     "    sys.modules['x']\n", "    tg.__dict__['x']\n", "    from operator import attrgetter\n    attrgetter('x')(tg)\n"):
            with self.subTest(line=line.strip()):
                self.assertEqual(self.refused('patterns/execution_store.py', anchor, anchor + line), 'DYNAMIC_REFERENCE')
                self.write('patterns/execution_store.py', anchor + line, anchor)

    def test_attrgetter_and_dict_on_objects_are_allowed(self):
        anchor = 'def _derive(db, relation, parts, on_stage=None):\n'
        after = self.edit('patterns/execution_store.py', anchor,
                          anchor + "    from operator import attrgetter\n    attrgetter('x')(db)\n    db.__dict__\n")
        self.assertEqual(self.changed(after), ['patterns/execution_store.py'])

    def test_an_unknown_root_is_refused(self):
        with self.assertRaises(bf.FingerprintError) as raised:
            bf.fingerprints({'patterns/execution_store.py': ['NoSuchBuilder']}, self.root)
        self.assertEqual(raised.exception.code, 'UNKNOWN_ROOT')


if __name__ == '__main__':
    unittest.main()
