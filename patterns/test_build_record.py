"""The build's runtime record (D34): every bypass both reviews of the static fingerprint found is caught, by
construction (the record lists every repository file the build process loaded or read, by whole-file digest) or by a
refusal at runtime.

Each case copies the repository's code into a scratch tree, edits it, runs the scaling benchmark's build worker there
(`tools/scaling_build_worker.py`, isolated, one cohort of 48 patients) and compares its record with the unedited one.
Needs DuckDB.
"""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover
    duckdb = None

from . import build_record as br

ROOT = Path(__file__).resolve().parents[1]
DATA = ('demos/mockups/synthetic', 'schemas', 'ontology/sulo-pin.json')
BUILD_MODULES = ['patterns/__init__.py', 'patterns/_resources.py', 'patterns/build_record.py',
                 'patterns/execution_store.py', 'patterns/series_values.py', 'patterns/synthetic_ingest.py',
                 'patterns/trajectory_times.py', 'tools/scaling_build_worker.py']


def scratch_tree():
    tmp = tempfile.TemporaryDirectory(prefix='build-record-')
    root = Path(tmp.name)
    (root / 'patterns').mkdir()
    (root / 'tools').mkdir()
    for path in (ROOT / 'patterns').glob('*.py'):
        shutil.copy(path, root / 'patterns' / path.name)
    shutil.copy(ROOT / 'tools/scaling_build_worker.py', root / 'tools/scaling_build_worker.py')
    for item in DATA:
        source, target = ROOT / item, root / item
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copy(source, target)
    return tmp, root


def build(root):
    """(record or None, error message or None) of one isolated build of 48 patients in `root`."""
    out = root / 'out.json'
    (root / 'work').mkdir(exist_ok=True)
    done = subprocess.run([sys.executable, '-I', str(root / 'tools/scaling_build_worker.py'), '--worker', 'build',
                           '--worker-output', str(out), '--size', '48', '--work-dir', str(root / 'work')],
                          cwd=root, capture_output=True, text=True)
    shutil.rmtree(root / 'work', ignore_errors=True)
    if done.returncode:
        return None, (done.stderr.strip().splitlines() or ['no message'])[-1]
    return json.loads(out.read_text())['record'], None


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class BuildRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.root = scratch_tree()
        cls.baseline, error = build(cls.root)
        assert error is None, error

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.edited = []

    def tearDown(self):  # undo this test's edits
        for path, text in reversed(self.edited):
            if text is None:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            else:
                path.write_text(text)

    def write(self, rel, text):
        path = self.root / rel
        self.edited.append((path, path.read_text() if path.exists() else None))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def edit(self, rel, old, new):
        text = (self.root / rel).read_text()
        self.assertEqual(text.count(old), 1, old)
        self.write(rel, text.replace(old, new))

    def changed(self):
        record, error = build(self.root)
        self.assertIsNone(error)
        return sorted(p for p in set(record['modules']) | set(self.baseline['modules'])
                      if record['modules'].get(p) != self.baseline['modules'].get(p))

    def refused(self):
        record, error = build(self.root)
        self.assertIsNone(record)
        return error

    def test_the_build_loads_only_its_own_modules(self):
        self.assertEqual(sorted(self.baseline['modules']), BUILD_MODULES)
        self.assertEqual(sorted(self.baseline['files']), ['demos/mockups/synthetic/generate.py',
                                                         'schemas/execution-store-manifest.schema.json'])
        self.assertEqual(self.baseline['root'], 'checkout')
        self.assertEqual(set(self.baseline['versions']), {'python', 'duckdb', 'pyarrow', 'jsonschema'})
        self.assertEqual(build(self.root)[0], self.baseline)  # the same tree records the same

    # The first review (static closure): each edit now changes the record.

    def test_a_conditional_reassignment_at_top_level(self):
        self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', 'BUCKETS = 8\nif True:\n    BUCKETS = 8\n')
        self.assertEqual(self.changed(), ['patterns/execution_store.py'])

    def test_a_top_level_write_through_globals(self):
        self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', "BUCKETS = 8\nglobals()['_UNUSED'] = 1\n")
        self.assertEqual(self.changed(), ['patterns/execution_store.py'])

    def test_an_edit_in_the_build_process_entry_point(self):
        self.edit('tools/scaling_build_worker.py', '    recorder.start()\n',
                  '    execution_store.BUCKETS = execution_store.BUCKETS\n    recorder.start()\n')
        self.assertEqual(self.changed(), ['tools/scaling_build_worker.py'])

    def test_an_import_swapped_in_a_module_the_build_loads(self):
        self.edit('patterns/series_values.py', 'from decimal import Decimal, InvalidOperation',
                  'from _pydecimal import Decimal, InvalidOperation')
        self.assertEqual(self.changed(), ['patterns/series_values.py'])

    def test_a_change_to_how_a_build_is_measured(self):
        self.edit('tools/scaling_build_worker.py', "record = {'wall_seconds': seconds, 'peak_rss_bytes': max(maxrss, peak)}",
                  "record = {'wall_seconds': seconds, 'peak_rss_bytes': peak}")
        self.assertEqual(self.changed(), ['tools/scaling_build_worker.py'])

    # The second review: import-time side effects, unreached repository code, two-step lookups.

    def test_import_time_side_effects_inside_definitions(self):
        for old, new in (('BUCKETS = 8\n', 'BUCKETS = 8\n_unused = globals().update(_X=1)\n'),
                         ('BUCKETS = 8\n', 'BUCKETS = 8\nif (_W := 8):\n    pass\n'),
                         ('def _derive(db, relation, parts, on_stage=None):', 'def _derive(db, relation, parts, on_stage=globals().update(_Y=1)):')):
            with self.subTest(new=new.strip()[:40]):
                self.edit('patterns/execution_store.py', old, new)
                self.assertEqual(self.changed(), ['patterns/execution_store.py'])
                self.tearDown()
                self.edited = []

    def test_a_package_init_side_effect(self):
        self.write('patterns/__init__.py', (self.root / 'patterns/__init__.py').read_text() + 'import os\n')
        self.assertEqual(self.changed(), ['patterns/__init__.py'])

    def test_a_new_repository_module(self):
        self.write('patterns/extra_side.py', 'X = 1\n')
        self.write('patterns/__init__.py', (self.root / 'patterns/__init__.py').read_text() + 'from . import extra_side\n')
        self.assertEqual(self.changed(), ['patterns/__init__.py', 'patterns/extra_side.py'])

    def test_a_shadow_module_on_the_path(self):
        """A package `patterns/series_values/` takes the place of `patterns/series_values.py`."""
        text = (self.root / 'patterns/series_values.py').read_text()
        self.write('patterns/series_values/__init__.py', text)
        self.assertEqual(self.changed(), ['patterns/series_values.py', 'patterns/series_values/__init__.py'])

    def test_a_new_tools_package_init_is_recorded(self):
        """The build worker runs as a script and imports nothing from `tools`, so a new `tools/__init__.py` does not run
        in a build (here it would stop it); it is recorded all the same, as the `__init__.py` of a package on a
        recorded module's path, so a reuse sees it."""
        self.write('tools/__init__.py', 'raise SystemExit(9)\n')
        self.assertEqual(self.changed(), ['tools/__init__.py'])

    def test_two_step_lookups_are_in_recorded_files(self):
        for line in ("import builtins\n_B = builtins.getattr(sys.modules[__name__], 'BUCKETS', 8)\n",
                     "import pkgutil\n_P = pkgutil.resolve_name('patterns.series_values:decimal_of')\n",
                     "def __getattr__(name):\n    raise AttributeError(name)\n",
                     "if __name__ != '__main__':\n    _M = 1\n"):
            with self.subTest(line=line.split()[0]):
                self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', 'BUCKETS = 8\n' + line)
                self.assertEqual(self.changed(), ['patterns/execution_store.py'])
                self.tearDown()
                self.edited = []

    # Refusals at runtime.

    def test_matching_code_loaded_is_refused(self):
        self.edit('patterns/series_values.py', 'from decimal import Decimal, InvalidOperation',
                  'from decimal import Decimal, InvalidOperation\nfrom . import temporal_stn  # noqa: F401')
        self.assertIn('MATCHING_CODE_LOADED: patterns.temporal_stn', self.refused())

    def test_an_unpinned_repository_file_read_is_refused(self):
        self.edit('patterns/synthetic_ingest.py', 'from . import series_values\n',
                  "from . import series_values\n(Path(__file__).resolve().parents[1] / 'ontology/sulo-pin.json').read_text()\n")
        self.assertIn('UNRECORDED_FILE_READ: ontology/sulo-pin.json', self.refused())


class RecorderTests(unittest.TestCase):
    """The recorder in process: a file that changes while the build runs is refused."""

    def test_a_file_changed_during_the_build_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'd34probe').mkdir()
            (root / 'd34probe/__init__.py').write_text('')
            (root / 'd34probe/mod.py').write_text('X = 1\n')
            sys.path.insert(0, str(root))
            try:
                importlib.import_module('d34probe.mod')
                recorder = br.Recorder.__new__(br.Recorder)  # no audit hook in the test process
                recorder.root, recorder.opened, recorder.first = root.resolve(), set(), None
                recorder.start()
                self.assertEqual(sorted(recorder.first), ['d34probe/__init__.py', 'd34probe/mod.py'])
                (root / 'd34probe/mod.py').write_text('X = 2\n')
                with self.assertRaises(br.RecordError) as raised:
                    recorder.record(())
                self.assertEqual(raised.exception.code, 'FILE_CHANGED_DURING_BUILD')
            finally:
                sys.path.remove(str(root))
                for name in ('d34probe.mod', 'd34probe'):
                    sys.modules.pop(name, None)


if __name__ == '__main__':
    unittest.main()
