"""The recorded totals must be derived from loadable suites, and drift must fail."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from tools.check_test_counts import (ROOT, STATUS, REPORT, START, END, CountError, count_suites,
                                     oracle_counts, render_status, main)

LABELS = {'test_alpha': 'Alpha tests', 'test_beta': 'Beta tests'}


def fixture_root(tmp, *, alpha=2, beta=3, cases=2, properties=3, extra_module=None, broken=False):
    root = Path(tmp)
    (root / 'patterns').mkdir()
    (root / 'patterns/__init__.py').write_text('')
    for name, n in (('alpha', alpha), ('beta', beta)):
        body = '\n'.join(f'    def test_{i}(self): pass' for i in range(n))
        (root / f'patterns/test_{name}.py').write_text(
            f'import unittest\nclass T(unittest.TestCase):\n{body}\n')
    if extra_module:
        (root / f'patterns/{extra_module}.py').write_text(
            'import unittest\nclass T(unittest.TestCase):\n    def test_x(self): pass\n')
    if broken:
        (root / 'patterns/test_alpha.py').write_text('import no_such_module_anywhere\n')
    (root / 'examples').mkdir(); (root / 'verification').mkdir(); (root / 'docs').mkdir()
    (root / 'examples/cases.json').write_text(json.dumps([{}] * cases))
    (root / 'verification/reference-report.json').write_text(json.dumps({'properties': ['p'] * properties}))
    (root / STATUS).write_text(textwrap.dedent(f'''\
        # Status

        **Summary: <!-- test-counts:total -->0<!-- /test-counts:total --> contract checks. Full-product completion remains false.**

        ## Verification results

        {START}

        | Check | Result |
        |---|---|
        | Oracle cases | {cases} passed |
        | Alpha tests | 0 passed |
        | Beta tests | 0 passed |
        | **Total** | **0 contract checks** |
        | Release manifest digests | 9 verified |

        The total is 0 suite tests plus 0 oracle cases and zero oracle properties; other checks are not added again.

        {END}
        '''))
    return root


class CountTests(unittest.TestCase):
    def test_counts_load_without_running_and_derive_oracle_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp)
            self.assertEqual(count_suites(root, LABELS), {'test_alpha': 2, 'test_beta': 3})
            self.assertEqual(oracle_counts(root), (2, 3))

    def test_render_regenerates_rows_total_and_sentence_but_preserves_other_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp)
            text, total = render_status((root / STATUS).read_text(), {'test_alpha': 2, 'test_beta': 3}, 2, 3, LABELS)
            self.assertEqual(total, 10)
            self.assertIn('| Alpha tests | 2 passed |', text)
            self.assertIn('| Beta tests | 3 passed |', text)
            self.assertIn('| **Total** | **10 contract checks** |', text)
            self.assertIn('The total is 5 suite tests plus 2 oracle cases and three oracle properties;', text)
            self.assertIn('<!-- test-counts:total -->10<!-- /test-counts:total -->', text)
            # Rows the tool does not own survive untouched and in place.
            self.assertIn('| Release manifest digests | 9 verified |', text)
            self.assertIn('| Oracle cases | 2 passed |', text)
            self.assertLess(text.index('| Beta tests |'), text.index('| **Total** |'))
            self.assertLess(text.index('| **Total** |'), text.index('| Release manifest'))

    def test_check_fails_on_stale_doc_and_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp)
            with mock.patch('tools.check_test_counts.LABELS', LABELS), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--root', str(root)]), 1)
                self.assertEqual(main(['--root', str(root), '--write']), 0)
                self.assertEqual(main(['--root', str(root)]), 0)
                first = (root / STATUS).read_text()
                self.assertEqual(main(['--root', str(root), '--write']), 0)
                self.assertEqual((root / STATUS).read_text(), first)
            written = json.loads((root / REPORT).read_text())
            self.assertEqual(written['contract_checks'], 10)
            self.assertEqual(written['suites'], {'test_alpha': 2, 'test_beta': 3})
            self.assertEqual(written['method'], 'unittest loader; no tests executed')

    def test_unlabelled_suite_fails_and_names_the_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp, extra_module='test_gamma')
            with self.assertRaises(CountError) as ctx:
                count_suites(root, LABELS)
            self.assertIn('test_gamma', str(ctx.exception))

    def test_stale_label_for_removed_suite_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp)
            with self.assertRaises(CountError) as ctx:
                count_suites(root, {**LABELS, 'test_removed': 'Removed tests'})
            self.assertIn('test_removed', str(ctx.exception))

    def test_import_failure_is_an_error_not_a_count_of_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp, broken=True)
            with self.assertRaises(CountError) as ctx:
                count_suites(root, LABELS)
            self.assertIn('test_alpha', str(ctx.exception))
            self.assertIn('no_such_module_anywhere', str(ctx.exception))

    def test_missing_markers_fail_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp)
            text = (root / STATUS).read_text().replace(START, '')
            with self.assertRaises(CountError):
                render_status(text, {'test_alpha': 2, 'test_beta': 3}, 2, 3, LABELS)

    def test_committed_status_is_consistent_with_loadable_suites(self):
        """The same guard CI runs: the committed docs/status.md must match today's suites."""
        run = subprocess.run([sys.executable, 'tools/check_test_counts.py'], cwd=ROOT,
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertTrue(payload['consistent'])
        self.assertEqual(payload['suite_tests'], sum(count_suites().values()))
        self.assertEqual(payload['contract_checks'], payload['suite_tests'] + sum(oracle_counts()))

    def test_counting_leaves_sys_modules_and_path_untouched(self):
        """Counting a fixture root must not shadow or evict the real `patterns` package."""
        import patterns.test_pro_solid as real  # noqa: F401  (the real module, cached)
        before_modules = {k for k in sys.modules if k.startswith('patterns')}
        before_path = list(sys.path)
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture_root(tmp)
            self.assertEqual(count_suites(root, LABELS), {'test_alpha': 2, 'test_beta': 3})
        self.assertEqual({k for k in sys.modules if k.startswith('patterns')}, before_modules)
        self.assertIs(sys.modules['patterns.test_pro_solid'], real)
        self.assertEqual(sys.path, before_path)


if __name__ == '__main__':
    unittest.main()
