"""The build's runtime record (D34): every bypass both reviews of the static fingerprint found is caught, by
construction (the record lists every repository file the build process loaded or read, by whole-file digest) or by a
refusal at runtime.

Each case copies the repository's code into a scratch tree, edits it, runs the scaling benchmark's build worker there
(`tools/scaling_build_worker.py`, isolated, one cohort of 48 patients) and compares its record with the unedited one.
Needs DuckDB.
"""
import importlib
import importlib.util
import json
import os
import py_compile
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


def build_phase(root):
    """The scratch tree's `tools/scaling_build_worker.py`, loaded as the benchmark loads it."""
    spec = importlib.util.spec_from_file_location(f'scaling_build_worker_{abs(hash(str(root)))}',
                                                  root / 'tools/scaling_build_worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(root, staging='parquet', threads=1, full=False):
    """(record or None, error message or None) of one build of 48 patients in `root`, started as the benchmark starts
    it (`build_size`: isolated, without `site`, a fresh bytecode cache, a fixed environment); with `full`, the build's
    fields too."""
    fields, built = build_phase(root).build_size(48, root / 'work', staging, threads, '1GB', 600)
    shutil.rmtree(root / 'work', ignore_errors=True)
    if built is None:
        return (None, fields['failure']['detail'], fields) if full else (None, fields['failure']['detail'])
    return (built['record'], None, fields) if full else (built['record'], None)


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class BuildRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.root = scratch_tree()
        cls.baseline, error, cls.fields = build(cls.root, full=True)
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
        self.assertEqual(self.baseline['settings']['process'],
                         {'isolated': True, 'no_site': True, 'fresh_bytecode_cache': True})
        self.assertEqual(build(self.root)[0], self.baseline)  # the same tree records the same

    # The first review (static closure): each edit now changes the record.

    def test_a_conditional_reassignment_at_top_level(self):
        self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', 'BUCKETS = 8\nif True:\n    BUCKETS = 8\n')
        self.assertEqual(self.changed(), ['patterns/execution_store.py'])

    def test_a_top_level_write_through_globals(self):
        self.edit('patterns/execution_store.py', 'BUCKETS = 8\n', "BUCKETS = 8\nglobals()['_UNUSED'] = 1\n")
        self.assertEqual(self.changed(), ['patterns/execution_store.py'])

    def test_an_edit_in_the_build_process_entry_point(self):
        self.edit('tools/scaling_build_worker.py', '    recorder.start(BUILD_FILES)\n',
                  '    execution_store.BUCKETS = execution_store.BUCKETS\n    recorder.start(BUILD_FILES)\n')
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

    # The third review: bytecode, the parent's settings.

    def doctored_bytecode(self, mode):
        """Compile `execution_store.py` with BUCKETS = 4 into the scratch tree's own `__pycache__`, then restore the
        source (and, for a timestamp `.pyc`, its whole-second modification time): the bytecode no longer matches."""
        source = self.root / 'patterns/execution_store.py'
        text, stat = source.read_text(), source.stat()
        cache = Path(importlib.util.cache_from_source(str(source)))
        self.edited.append((cache, cache.read_bytes() if cache.exists() else None))
        source.write_text(text.replace('BUCKETS = 8\n', 'BUCKETS = 4\n'))
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        py_compile.compile(str(source), cfile=str(cache), doraise=True, invalidation_mode=mode)
        source.write_text(text)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_doctored_bytecode_never_runs(self):
        for mode in (py_compile.PycInvalidationMode.UNCHECKED_HASH, py_compile.PycInvalidationMode.TIMESTAMP):
            with self.subTest(mode=mode.name):
                self.doctored_bytecode(mode)
                record, error, fields = build(self.root, full=True)
                self.assertIsNone(error)
                self.assertEqual(record, self.baseline)
                self.assertEqual(fields['snapshot_id'], self.fields['snapshot_id'])  # BUCKETS = 4 would change it
                self.tearDown()
                self.edited = []

    def test_a_process_started_otherwise_is_refused(self):
        """Run directly, without the fresh bytecode cache and without `-S`, the worker refuses to build; and a read of
        the repository's `__pycache__` is no longer exempt."""
        out = self.root / 'direct.json'
        done = subprocess.run([sys.executable, '-I', str(self.root / 'tools/scaling_build_worker.py'), '--worker', 'build',
                               '--worker-output', str(out), '--size', '48', '--work-dir', str(self.root / 'direct')],
                              cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('PROCESS_NOT_ISOLATED', done.stderr)
        with tempfile.TemporaryDirectory() as empty:  # a root no module of this process lies in
            recorder = br.Recorder(empty, python_dirs=[], hook=False)
            recorder.opened.add('patterns/__pycache__/execution_store.cpython-312.pyc')
            with self.assertRaises(br.RecordError) as raised:
                recorder.record(())
        self.assertEqual(raised.exception.code, 'UNRECORDED_FILE_READ')

    def test_the_settings_are_the_build_process_own(self):
        """ndjson staging with 2 threads builds the same snapshot as parquet with 1, but records different settings, so
        a reuse cannot take one for the other on the parent's word."""
        record, error, fields = build(self.root, staging='ndjson', threads=2, full=True)
        self.assertIsNone(error)
        self.assertEqual(fields['snapshot_id'], self.fields['snapshot_id'])
        self.assertEqual(record['modules'], self.baseline['modules'])
        self.assertEqual((record['settings']['staging'], record['settings']['threads']), ('ndjson', 2))
        self.assertNotEqual(record, self.baseline)

    def child(self, prefix_setup=None, extra_env=None):
        """Run the scratch tree's build worker as `build_size` does, with a planted cache or an extra variable."""
        module = build_phase(self.root)
        fresh = Path(tempfile.mkdtemp(prefix='scaling-build-test-'))
        try:
            if prefix_setup:
                prefix_setup(fresh / 'pycache')
            out = self.root / 'child.json'
            env = {**module.CHILD_ENVIRONMENT, 'TMPDIR': str(fresh), 'HOME': str(fresh), **(extra_env or {})}
            done = subprocess.run(module.build_command(fresh) + ['--worker', 'build', '--worker-output', str(out),
                                                                 '--size', '48', '--work-dir', str(self.root / 'cwork')],
                                  cwd=self.root, env=env, capture_output=True, text=True)
            (self.root / 'cwork').mkdir(exist_ok=True)
            shutil.rmtree(self.root / 'cwork', ignore_errors=True)
            return done, (json.loads(out.read_text()) if done.returncode == 0 else None)
        finally:
            shutil.rmtree(fresh, ignore_errors=True)

    def test_a_bytecode_cache_that_is_not_fresh_is_refused(self):
        def plant(prefix):
            cache = Path(prefix, *self.root.resolve().parts[1:], 'patterns', '__pycache__')
            cache.mkdir(parents=True)
            (cache / 'execution_store.cpython-312.pyc').write_bytes(b'planted')
        (self.root / 'cwork').mkdir(exist_ok=True)
        done, _ = self.child(plant)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('BYTECODE_CACHE_NOT_FRESH', done.stderr)

    def test_every_environment_variable_is_recorded(self):
        (self.root / 'cwork').mkdir(exist_ok=True)
        done, result = self.child(extra_env={'DUCKDB_D34_PROBE': '1'})
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        env = result['record']['settings']['environment']
        self.assertEqual(env['DUCKDB_D34_PROBE'], '1')
        self.assertEqual((env['TMPDIR'], env['HOME']), ('<fresh directory>', '<fresh directory>'))
        self.assertNotEqual(result['record'], self.baseline)
        self.assertNotIn('DUCKDB_D34_PROBE', self.baseline['settings']['environment'])

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
                recorder = br.Recorder(root, python_dirs=[], hook=False)  # no audit hook in the test process
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



class VirtualEnvironmentTests(unittest.TestCase):
    """The Python installation's directories are not repository code, even inside the checkout (the review's must-fix
    2: the main checkout keeps `.venv` in it)."""

    def test_a_virtual_environment_inside_the_root_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            site = root / '.venv/lib/python3/site-packages'
            (site / 'd34fakepkg').mkdir(parents=True)
            (site / 'd34fakepkg/__init__.py').write_text('X = 1\n')
            (site / 'd34fakepkg-1.0.dist-info').mkdir()
            (site / 'd34fakepkg-1.0.dist-info/METADATA').write_text('Name: d34fakepkg\nVersion: 1.0\n')
            sys.path.insert(0, str(site))
            try:
                importlib.import_module('d34fakepkg')
                inside = br.Recorder(root, python_dirs=[], hook=False)  # without them it would be repository code
                self.assertIn('.venv/lib/python3/site-packages/d34fakepkg/__init__.py', inside.modules())
                recorder = br.Recorder(root, python_dirs=[root / '.venv'], hook=False)
                recorder._hook('open', (str(site / 'd34fakepkg-1.0.dist-info/METADATA'), 'r', 0))
                self.assertEqual(recorder.opened, set())
                self.assertEqual(recorder.modules(), {})
                self.assertEqual(recorder.record(())['modules'], {})
            finally:
                sys.path.remove(str(site))
                sys.modules.pop('d34fakepkg', None)

    def test_the_installation_directories_include_the_prefix_and_site_packages(self):
        import site
        dirs = br.python_directories()
        self.assertIn(Path(sys.prefix).resolve(), dirs)
        for d in site.getsitepackages():
            self.assertIn(Path(d).resolve(), dirs)
        with self.assertRaises(br.RecordError) as raised:
            br.Recorder(sys.prefix, hook=False)
        self.assertEqual(raised.exception.code, 'ROOT_INSIDE_PYTHON_PREFIX')

    @unittest.skipUnless((Path(sys.prefix).resolve().parent / '.git').exists(), 'the interpreter is not in a checkout')
    def test_a_checkout_holding_the_interpreter(self):
        """Where the interpreter's environment lies inside a checkout (the main checkout and its `.venv`), a recorder
        rooted there records none of it while DuckDB and jsonschema load and read their metadata (read-only)."""
        script = ('import sys; from pathlib import Path; sys.path.insert(0, %r)\n'
                  'from patterns.build_record import Recorder\n'
                  'r = Recorder(Path(sys.prefix).resolve().parent)\n'
                  'import duckdb, jsonschema\n'
                  'jsonschema.Draft202012Validator({"type": "object"}).validate({})\n'
                  'rec = r.record(())\n'
                  'assert not [p for p in list(rec["modules"]) + sorted(r.opened) if p.startswith(".venv")]\n'
                  'print("ok")\n') % str(ROOT)
        done = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=tempfile.gettempdir())
        self.assertEqual(done.stdout.strip(), 'ok', done.stderr[-600:])


if __name__ == '__main__':
    unittest.main()
