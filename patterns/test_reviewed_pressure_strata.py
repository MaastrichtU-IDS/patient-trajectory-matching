"""Provenance and comparability gates for the three separately reviewed demo strata."""
from copy import deepcopy
import json
import unittest
from . import reviewed_source_query as r, verify_indexed_source_windows as windows

ROOT = r.p.ei.ROOT
STRATA = {
    'arterial': ('220052', 2022, 17, 964),
    'noninvasive': ('220181', 1784, 25, 962),
    'art': ('225312', 1042, 4, 944),
}


def read(path):
    return json.loads((ROOT/path).read_text())


def report(name):
    return read(f'verification/reviewed-{name}-demo-report.json')


class ReviewedPressureStrataTests(unittest.TestCase):
    def test_all_reports_bind_current_code_pinned_sources_and_separate_declarations(self):
        contexts = set()
        for name, (_, claims, calendars, _) in STRATA.items():
            with self.subTest(stratum=name):
                observed = report(name)
                declaration = read(f'data/{name}-source-fidelity-review.json')
                self.assertEqual(observed['review_declaration'], declaration)
                self.assertEqual(observed['review_declaration_sha256'], r.p.cr.digest(declaration))
                for key in ('package_context_id', 'audit_context_id'):
                    self.assertEqual(observed[key], declaration[key])
                    contexts.add(observed[key])
                self.assertEqual(declaration['unique_claims'], claims)
                self.assertEqual(declaration['calendar_reviews'], calendars)
                self.assertEqual(set(observed['artifacts']), set(r.FILES))
                for path, digest in observed['artifacts'].items():
                    self.assertEqual(digest, r.p.ei.digest((ROOT/path).read_text()), path)
                windows.validate_summary(observed['source_selection_summary'], 'demo-'+name)
                self.assertEqual(observed['audit_summary']['claims_verified'], claims)
                self.assertEqual(observed['audit_summary']['claims_failed'], 0)
                self.assertEqual(observed['audit_summary']['acceptance_decisions_created'], 0)
                self.assertEqual(observed['review_summary']['unique_accepted_claims'], claims)
                self.assertTrue(observed['review_summary']['review_complete'])
                for key in ('clinical_mapping_verified', 'reviewer_identity_verified',
                            'source_history_verified', 'physical_elapsed_time_verified',
                            'causal_effect_estimated', 'full_mixed_owl_reasoning_verified',
                            'patient_rows_or_identifiers_included'):
                    self.assertIs(observed[key], False)
                self.assertNotIn('bindings', observed)
                self.assertNotIn('certain_patient_ids', observed)
        self.assertEqual(len(contexts), 6)

    def test_requests_differ_only_in_stratum_identity_and_measurement_item(self):
        normalized = []
        for name, (item, _, _, _) in STRATA.items():
            request = deepcopy(report(name)['request'])
            self.assertEqual(request, read(f'examples/indexed-source-windows/demo-{name}-request.json'))
            request.pop('id')
            for slot in ('baseline', 'followup'):
                self.assertEqual(request['query'][slot].pop('item_ids'), [item])
            normalized.append(request)
        self.assertTrue(all(request == normalized[0] for request in normalized))
        # Common source anchors and complete stay population; measurement coverage may differ.
        self.assertTrue(all(report(name)['package_summary']['anchors'] == 944 for name in STRATA))
        self.assertTrue(all(report(name)['package_summary']['stays'] == 140 for name in STRATA))

    def test_complete_accounting_and_candidate_plan_agree_with_each_execution(self):
        plan = read('data/clinical-candidate-plan.json')
        self.assertIsNone(plan['clinical_review'])
        self.assertFalse(plan['pool_measurement_items'])
        entries = plan['technical_pressure_strata_execution']['strata']
        self.assertEqual(set(entries), set(STRATA))
        for name, (_, claims, _, batches) in STRATA.items():
            with self.subTest(stratum=name):
                observed = report(name)
                self.assertEqual(observed['status'], 'COMPLETED_PARTITIONED_QUERY')
                self.assertTrue(observed['search_complete_over_selected_records'])
                self.assertTrue(observed['real_source_mixed_query_executed'])
                self.assertEqual(observed['batch_outcomes'], {'COMPLETED_BATCH': batches})
                self.assertEqual(observed['anchors_verified_against_unpartitioned_sql'], 944)
                self.assertEqual(sum(observed['anchor_outcomes'].values()), 944)
                self.assertEqual(sum(observed['stay_outcomes'].values()), 140)
                self.assertNotIn('BLOCKED_STAY', observed['stay_outcomes'])
                metrics = observed['metrics']
                self.assertEqual(sum(metrics['delta_signs_per_binding'].values()), metrics['followup_bindings'])
                self.assertLessEqual(metrics['eligible_pairs_without_selected_followup'], metrics['eligible_treatment_baseline_pairs'])
                self.assertLessEqual(metrics['matched_patients'], observed['stay_outcomes'].get('VERIFIED_RECORD_MATCH', 0))
                self.assertEqual(entries[name]['report'], f'verification/reviewed-{name}-demo-report.json')
                self.assertEqual(entries[name]['declaration'], f'data/{name}-source-fidelity-review.json')
                self.assertEqual(entries[name]['real_source_claims_accepted'], claims)
                self.assertEqual(entries[name]['metrics'], metrics)


if __name__ == '__main__':
    unittest.main()
