"""The handoff must reach the matcher, or refuse by name. Nothing in between."""
from copy import deepcopy
from pathlib import Path
import json
import unittest

from patterns import mimic_inputevents as staging
from patterns import mimic_matcher_handoff as handoff
from patterns.pro_solid import build_graph, project
from reference_oracle import evaluate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/mimic-matcher-handoff'


def staged():
    source = ROOT / 'examples/mimic-inputevents'
    return staging.stage(*(source / f'{t}.csv' for t in ('inputevents', 'icustays', 'd_items')),
                         dataset_id='synthetic-mimic-demo-2.2')


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = staged()

    def setUp(self):
        self.refusing = json.loads((EXAMPLE / 'declaration.json').read_text())
        self.synthetic = json.loads((EXAMPLE / 'synthetic-review-declaration.json').read_text())

    def test_the_committed_declaration_refuses_and_names_who_must_supply_each_blocker(self):
        """The default state of this profile is refusal, and it must stay informative."""
        report = handoff.handoff(self.result, self.refusing)
        self.assertEqual(report['status'], 'REFUSED_NOT_MATCHER_READY')
        self.assertEqual(report['rows'], [])
        self.assertIsNone(report['source_search'], 'a refusal must not emit a warrant')
        self.assertEqual(report['manifests'], {})
        outstanding = {row['blocker']: row['dependency'] for row in report['outstanding']}
        self.assertEqual(outstanding, {'CLINICAL_MAPPING_REVIEW_REQUIRED': 'D-CLINICAL',
                                       'SOURCE_AVAILABILITY_UNVERIFIED': 'D-HISTORY',
                                       'SOURCE_REVISION_HISTORY_UNAVAILABLE': 'D-HISTORY'})
        self.assertEqual(report['discharged'], ['OCCURRENCE_PRECISION_POLICY_REQUIRED',
                                                'PATIENT_LOCAL_CLOCK_BRIDGE_REQUIRED'])

    def test_every_staging_blocker_has_a_declaration_field(self):
        """If staging grows a blocker, this profile must gain a way to discharge it
        rather than silently ignoring one."""
        self.assertEqual(sorted(handoff.BLOCKERS), sorted(staging.BLOCKERS))

    def test_an_empty_field_does_not_discharge_its_blocker(self):
        """The exact failure S2 found: presence is not evidence. A declaration carrying
        `"clinical_review": {}` has reviewed nothing."""
        for empty in ({}, None, 'reviewed', []):
            declaration = deepcopy(self.synthetic)
            declaration['clinical_review'] = empty
            report = handoff.handoff(self.result, declaration)
            self.assertEqual(report['status'], 'REFUSED_NOT_MATCHER_READY', repr(empty))

    def test_a_declaration_cannot_be_reused_against_another_staging_run(self):
        """A reviewed mapping is a judgement about one reconciled extract."""
        declaration = deepcopy(self.synthetic)
        declaration['staging_context_id'] = 'f' * 64
        with self.assertRaises(handoff.HandoffError) as caught:
            handoff.handoff(self.result, declaration)
        self.assertEqual(str(caught.exception), 'DECLARATION_NOT_BOUND_TO_THIS_STAGING_RUN')

    def test_a_synthetic_review_executes_the_path_and_says_so(self):
        report = handoff.handoff(self.result, self.synthetic)
        self.assertEqual(report['status'], 'HANDED_OFF')
        self.assertEqual(report['review_basis'], 'synthetic')
        self.assertIs(report['clinical_validity_claim'], False)
        self.assertEqual(len(report['rows']), 2)

    def test_a_declared_review_is_not_reported_as_synthetic(self):
        declaration = deepcopy(self.synthetic)
        declaration['clinical_review'] = {'reviewer': 'someone', 'decision': 'reviewed'}
        self.assertEqual(handoff.handoff(self.result, declaration)['review_basis'], 'declared')

    def test_the_warrant_spans_every_reconciled_row_not_only_the_staged_ones(self):
        """A window drawn around the staged segments would narrow each time a row was
        rejected, which inverts what coverage means: a rejected row was still searched.

        The committed fixture cannot show this on its own, because its rejected rows
        happen to fall inside the staged range. So a rejected row is moved earlier here
        and the warrant has to follow it; a span taken from staged segments alone would
        not move at all.
        """
        rejected = next(e for e in self.result['reconciliation']
                        if e['outcome'] != 'STAGED_SEGMENT' and (e.get('raw') or {}).get('starttime'))
        before = handoff.handoff(self.result, self.synthetic)['source_search']

        moved = deepcopy(self.result)
        entry = next(e for e in moved['reconciliation'] if e['record_id'] == rejected['record_id'])
        entry['raw']['starttime'] = '2149-12-25 08:00:00'
        after = handoff.handoff(moved, self.synthetic)['source_search']

        self.assertLess(after['from_us'], before['from_us'],
                        'a rejected row searched earlier must widen the searched window')
        self.assertEqual(after['from_us'],
                         handoff.offset_us('2149-12-25T08:00:00',
                                           self.synthetic['clock_bridge']['origin']))
        self.assertEqual(after['to_us'], before['to_us'], 'only the start should move')

    def test_the_warrant_is_refused_when_reconciliation_is_incomplete(self):
        """Without complete reconciliation the window is a claim, not a measurement."""
        result = deepcopy(self.result)
        result['summary']['reconciliation_complete'] = False
        with self.assertRaises(handoff.HandoffError) as caught:
            handoff.handoff(result, self.synthetic)
        self.assertEqual(str(caught.exception), 'RECONCILIATION_INCOMPLETE')

    def test_an_unmapped_item_is_skipped_visibly(self):
        declaration = deepcopy(self.synthetic)
        declaration['item_concepts'] = {'999999': 'ex:DrugA'}
        report = handoff.handoff(self.result, declaration)
        self.assertEqual(report['rows'], [])
        self.assertEqual({row['reason'] for row in report['skipped']},
                         {'ITEM_NOT_IN_REVIEWED_MAPPING'})
        self.assertEqual(len(report['skipped']), self.result['summary']['staged_segments'])

    def test_offsets_are_signed_calendar_microseconds_from_the_declared_origin(self):
        self.assertEqual(handoff.offset_us('2150-01-01T11:00:00', '2150-01-01T10:00:00'),
                         3600 * handoff.US)
        self.assertEqual(handoff.offset_us('2150-01-01T09:00:00', '2150-01-01T10:00:00'),
                         -3600 * handoff.US)

    def test_malformed_or_unsupported_declarations_are_refused_before_any_projection(self):
        for field, value, code in (
                ('clock_bridge', {'policy': 'other', 'clock_id': 'c', 'origin': '2150-01-01T00:00:00'},
                 'UNSUPPORTED_CLOCK_POLICY'),
                ('clock_bridge', {'policy': handoff.CLOCK_POLICY, 'clock_id': '', 'origin': '2150-01-01T00:00:00'},
                 'CLOCK_CLOCK_ID_REQUIRED'),
                ('occurrence_policy', {'policy': 'other', 'justification': 'x' * 40},
                 'UNSUPPORTED_OCCURRENCE_POLICY'),
                ('occurrence_policy', {'policy': handoff.OCCURRENCE_POLICY, 'justification': 'short'},
                 'OCCURRENCE_POLICY_JUSTIFICATION_REQUIRED')):
            declaration = deepcopy(self.synthetic)
            declaration[field] = value
            with self.assertRaises(handoff.HandoffError) as caught:
                handoff.handoff(self.result, declaration)
            self.assertEqual(str(caught.exception), code)

    def test_an_undeclared_age_is_refused_rather_than_derived(self):
        """MIMIC-IV top-codes ages above 89 as 91, a de-identification category rather
        than an age, so this is a reviewed judgement and never an inference."""
        declaration = deepcopy(self.synthetic)
        declaration['ages'] = {}
        with self.assertRaises(handoff.HandoffError) as caught:
            handoff.handoff(self.result, declaration)
        self.assertTrue(str(caught.exception).startswith('AGE_NOT_DECLARED_FOR_PATIENT:'))

    def test_the_handoff_reaches_the_matcher_and_carries_its_warrant_there(self):
        """S1's actual claim. The rows and manifest this profile emits are admitted by
        the existing PRO/SOLID projection and evaluated by the existing matcher, with no
        further field invented in between.

        The answer is UNRESOLVED, and that is the correct answer rather than a
        shortcoming: inputevents staging searched for administrations only, so the
        warrant records that no creatinine was ever looked for, and the matcher refuses
        to conclude anything about a pattern needing one. Before this path existed that
        gap was invisible; the warrant is what makes it speak.
        """
        report = handoff.handoff(self.result, self.synthetic)
        pattern = json.loads((ROOT / 'examples/exemplar.pattern.json').read_text())
        taxonomy = json.loads((ROOT / 'ontology/toy-taxonomy.json').read_text())
        self.assertTrue(report['manifests'])
        for patient, manifest in report['manifests'].items():
            rows = [row for row in report['rows'] if row['patient_id'] == patient]
            case, evidence = project(build_graph(rows), manifest)
            self.assertEqual(case['source_search'], report['source_search'])
            outcome = evaluate(case, pattern, taxonomy)
            self.assertEqual(outcome['accepted_as'], 'UNRESOLVED')
            self.assertIn('INCOMPLETE_SOURCE_SEARCH', outcome['reason_codes'])
            self.assertIn('concept ex:Creatinine never searched for',
                          outcome['uncovered_demands'])
            self.assertEqual(outcome['completeness_basis'], 'coverage',
                             'the warrant reaching the matcher must be the derived one')

    def test_a_measurement_search_would_be_required_before_any_answer(self):
        """The companion of the test above: even with every blocker discharged, this
        source cannot answer the exemplar. Adding the measurement concept to the mapping
        is not enough, because inputevents holds no measurement rows -- so the gap is a
        property of the extract, not of the declaration.
        """
        declaration = deepcopy(self.synthetic)
        declaration['item_concepts'] = {'1000': 'ex:Creatinine'}
        report = handoff.handoff(self.result, declaration)
        self.assertEqual({row['event_kind'] for row in report['rows']}, {'administration'},
                         'staging emits administration segments whatever the mapping says')


if __name__ == '__main__':
    unittest.main(verbosity=2)
