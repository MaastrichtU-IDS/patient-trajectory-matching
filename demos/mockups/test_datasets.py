"""The dataset registry (datasets.py): kinds, floors, and what a configuration may not do."""
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import access  # noqa: E402
import datasets  # noqa: E402


def entry(**changes):
    raw = {'id': 'synthetic-paediatric-rare', 'kind': 'synthetic-trajectory', 'title': 'Synthetic',
           'access': 'public', 'results': 'per_patient'}
    raw.update(changes)
    return {k: v for k, v in raw.items() if v is not None}


def mimic(**changes):
    return entry(**{'id': 'mimic-iv-demo', 'kind': 'mimic-demo-aggregate', 'title': 'MIMIC-IV demo',
                    'access': 'approved', 'results': 'aggregate', 'store': '/data/demo/store', **changes})


class RegistryTests(unittest.TestCase):
    def refused(self, document, code):
        with self.assertRaises(datasets.RegistryError) as caught:
            datasets.parse(document)
        self.assertEqual(str(caught.exception), code)

    def test_the_dev_registry_and_the_three_tiers_parse(self):
        parsed = datasets.parse({'datasets': [
            entry(), mimic(),
            entry(id='synthetic-behind-a-role', access={'role': 'dataset:test:read'}),
            mimic(id='mimic-demo-credentialed-only', access={'role': 'dataset:mimic-iv-demo:read'}, d_items='/d.csv')]})
        self.assertEqual([e.policy for e in parsed], [access.Policy('public'), access.Policy('approved'),
                                                       access.Policy('role', 'dataset:test:read'),
                                                       access.Policy('role', 'dataset:mimic-iv-demo:read')])
        self.assertEqual(parsed[1].settings, {'store': '/data/demo/store'})

    def test_no_configuration_lowers_a_kinds_floor(self):
        self.refused({'datasets': [mimic(access='public')]}, 'ACCESS_BELOW_THE_KINDS_FLOOR')

    def test_full_mimic_iv_is_refused_until_the_access_review(self):
        for policy in ({'role': 'dataset:mimic-iv:read'}, 'approved', 'public'):
            self.refused({'datasets': [entry(id='mimic-iv', kind='mimic-iv', access=policy)]},
                         'DATASET_KIND_NOT_HOSTABLE')

    def test_results_describe_the_backend_and_cannot_change_it(self):
        self.refused({'datasets': [mimic(results='per_patient')]}, 'RESULTS_NOT_WHAT_THE_BACKEND_PRODUCES')
        self.refused({'datasets': [entry(results='aggregate')]}, 'RESULTS_NOT_WHAT_THE_BACKEND_PRODUCES')

    def test_malformed_registries_are_refused_by_name(self):
        self.refused([], 'REGISTRY_MUST_BE_AN_OBJECT_WITH_DATASETS')
        self.refused({'datasets': [], 'extra': 1}, 'REGISTRY_MUST_BE_AN_OBJECT_WITH_DATASETS')
        self.refused({'datasets': ['x']}, 'DATASET_MUST_BE_AN_OBJECT')
        self.refused({'datasets': [entry(kind='nope')]}, 'UNKNOWN_DATASET_KIND')
        self.refused({'datasets': [entry(store='/x')]}, 'DATASET_FIELDS')          # a setting of another kind
        self.refused({'datasets': [mimic(store=None)]}, 'DATASET_FIELDS')         # a required setting missing
        self.refused({'datasets': [entry(title=None)]}, 'DATASET_FIELDS')
        self.refused({'datasets': [entry(id='Has Spaces')]}, 'DATASET_ID_INVALID_OR_REPEATED')
        self.refused({'datasets': [entry(), entry()]}, 'DATASET_ID_INVALID_OR_REPEATED')
        self.refused({'datasets': [entry(title='')]}, 'DATASET_TITLE')
        self.refused({'datasets': [entry(access='everyone')]}, 'ACCESS_MUST_BE_PUBLIC_APPROVED_OR_ROLE')
        self.refused({'datasets': [mimic(store=7)]}, 'DATASET_SETTING_MUST_BE_A_PATH')

    def test_a_registry_file_and_the_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'datasets.json'
            path.write_text(json.dumps({'datasets': [entry()]}))
            self.assertEqual([e.id for e in datasets.load(path)], ['synthetic-paediatric-rare'])
            path.write_text('{not json')
            with self.assertRaises(datasets.RegistryError):
                datasets.load(path)
            with self.assertRaises(datasets.RegistryError):
                datasets.load(Path(tmp) / 'missing.json')
        flags = datasets.from_flags(True, '/data/demo')
        self.assertEqual([(e.id, e.policy.access, e.results) for e in flags],
                         [('synthetic-paediatric-rare', 'public', 'per_patient'),
                          ('mimic-iv-demo', 'approved', 'aggregate')])
        self.assertEqual(datasets.from_flags(False, None), [])

    def test_the_deployed_registry_in_the_draft_manifests_parses(self):
        # deploy/ids3/ptm/dev/configmap.yaml carries the dev registry as a JSON block; it must stay loadable.
        text = (HERE.parents[1] / 'deploy/ids3/ptm/dev/configmap.yaml').read_text()
        block = text.split('datasets.json: |', 1)[1]
        lines = []
        for line in block.splitlines()[1:]:
            if line.strip() and not line.startswith('    '):
                break
            lines.append(line)
        document = json.loads(textwrap.dedent('\n'.join(lines)))
        parsed = datasets.parse(document)
        self.assertEqual([(e.id, e.policy.access) for e in parsed], [('synthetic-paediatric-rare', 'public')])


if __name__ == '__main__':
    unittest.main()
