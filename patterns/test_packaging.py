"""Unit tests of the resource locator behind ``pro_solid.ROOT`` (decision D2).

The installed locator is simulated against a temporary directory laid out like
site-packages; the end-to-end check on a real wheel is in CI (package.yml).
"""
import os
import tempfile
import unittest
from pathlib import Path

from patterns import _resources, pro_solid


class CheckoutMode(unittest.TestCase):
    def test_checkout_root_is_the_plain_path(self):
        expected = Path(_resources.__file__).resolve().parents[1]
        self.assertEqual(_resources.ROOT, expected)
        self.assertIs(type(_resources.ROOT), type(expected))
        self.assertEqual(pro_solid.ROOT, expected)

    def test_checkout_is_detected_by_contract_directories(self):
        self.assertTrue(_resources.is_checkout())
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'patterns'
            package.mkdir()
            self.assertFalse(_resources.is_checkout(package))
            self.assertIsInstance(_resources.locate_root(package), _resources.InstalledRoot)
            (Path(tmp) / 'schemas').mkdir()
            (Path(tmp) / 'ontology').mkdir()
            self.assertTrue(_resources.is_checkout(package))
            self.assertEqual(_resources.locate_root(package), Path(tmp).resolve())


class InstalledMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.site = Path(self._tmp.name).resolve()
        self.package = self.site / 'patterns'
        contracts = self.package / '_contracts'
        (contracts / 'schemas').mkdir(parents=True)
        (contracts / 'ontology' / 'vendor').mkdir(parents=True)
        (self.package / 'data.txt').write_text('package')
        (contracts / 'schemas' / 'a.schema.json').write_text('{}')
        (contracts / 'ontology' / 'vendor' / 'x.ttl').write_text('vendor')
        self.root = _resources.InstalledRoot(self.package)

    def test_dispatch_on_the_first_path_part(self):
        contracts = self.package / '_contracts'
        self.assertEqual(self.root / 'patterns/data.txt', self.package / 'data.txt')
        self.assertEqual(self.root / 'schemas/a.schema.json', contracts / 'schemas' / 'a.schema.json')
        self.assertEqual(self.root / 'ontology/vendor/x.ttl', contracts / 'ontology' / 'vendor' / 'x.ttl')
        self.assertEqual((self.root / 'patterns/data.txt').read_text(), 'package')

    def test_chaining_keeps_working(self):
        chained = self.root / 'ontology' / 'vendor' / 'x.ttl'
        self.assertEqual(chained.read_text(), 'vendor')
        self.assertEqual(self.root / 'schemas' / 'a.schema.json', self.root / 'schemas/a.schema.json')

    def test_other_resources_are_refused(self):
        for path in ('examples/x.json', 'data/pin.json', 'verification/r.json', 'docs/a.md', '../x', '/etc/passwd'):
            with self.assertRaises(ValueError) as caught:
                self.root / path
            self.assertEqual(str(caught.exception), 'REPOSITORY_RESOURCE_UNAVAILABLE:' + path)

    def test_the_root_itself_is_the_directory_containing_the_package(self):
        self.assertEqual(os.fspath(self.root), str(self.site))
        self.assertEqual(str(self.root), str(self.site))
        self.assertEqual(self.root.resolve(), self.root)
        self.assertEqual(Path(self.root), self.site)

    def test_engine_style_use_never_writes(self):
        before = sorted(p.relative_to(self.site) for p in self.site.rglob('*'))
        (self.root / 'schemas/a.schema.json').read_text()
        with self.assertRaises(ValueError):
            self.root / 'verification/report.json'
        self.assertEqual(sorted(p.relative_to(self.site) for p in self.site.rglob('*')), before)


if __name__ == '__main__':
    unittest.main()
