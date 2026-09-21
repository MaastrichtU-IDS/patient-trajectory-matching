"""The two relaxation cost models price different things; this pins the declared difference.

Accepted decision D (docs/decisions/relaxation-cost-models.md) keeps both evaluators and
unifies only what a reader sees. Nothing else in the repository exercises both models
together, so a divergence in their documented meanings could drift unnoticed.
"""
import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from reference_oracle import evaluate
from . import robust_relaxation as rr

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


class RelaxationContractTests(unittest.TestCase):
    def setUp(self):
        self.pattern = load('examples/exemplar.pattern.json')
        self.taxonomy = load('ontology/toy-taxonomy.json')
        self.cases = {c['case_id']: c for c in load('examples/cases.json')}

    def oracle(self, case_id, *, max_total_cost=None):
        case = copy.deepcopy(self.cases[case_id])
        if max_total_cost is not None:
            case['budget_override'] = {'max_relaxed_constraints': 2, 'max_total_cost': max_total_cost}
        return evaluate(case, self.pattern, self.taxonomy)

    def catalogue(self, *, max_cost, options):
        policy = load('examples/robust-relaxation/policy.json')
        policy['max_cost'] = max_cost
        policy['options'] = options
        return rr.execute(load('examples/robust-relaxation/source.json'),
                          load('examples/robust-relaxation/query.json'), policy)

    def test_budget_key_names_are_disjoint_so_neither_can_be_read_as_the_other(self):
        oracle_keys = set(self.pattern['budget'])
        catalogue_keys = {k for k in load('examples/robust-relaxation/policy.json')
                          if k in ('max_cost', 'max_changed_targets')}
        self.assertEqual(oracle_keys, {'max_total_cost', 'max_relaxed_constraints'})
        self.assertEqual(catalogue_keys, {'max_cost', 'max_changed_targets'})
        self.assertFalse(oracle_keys & catalogue_keys, 'a shared key would invite reading one limit as the other')

    def test_oracle_sums_component_costs_across_targets(self):
        """C05 is the committed composition case: two targets, one summed total."""
        composed = self.oracle('C05')
        self.assertEqual(composed['accepted_as'], 'RELAXED')
        self.assertEqual(sorted(composed['relaxed_constraint_ids']), ['exposure', 'exposure_window'])
        components = {k: Decimal(v) for k, v in composed['components'].items()}
        self.assertEqual(sum(components.values()), Decimal(composed['total_cost']))
        self.assertGreater(len([v for v in components.values() if v > 0]), 1)
        # Every component is individually within a budget of 1, yet their sum is not.
        self.assertTrue(all(v <= 1 for v in components.values()))
        self.assertGreater(Decimal(composed['total_cost']), Decimal('1'))
        self.assertNotEqual(self.oracle('C05', max_total_cost='1')['accepted_as'], 'RELAXED')

    def test_catalogue_never_sums_costs_across_options(self):
        """Two options within budget individually still do not combine to reach a match."""
        # The recorded gap is 47-49 minutes, so certainty needs the limit at 49 minutes
        # (2 940 000 000 us). Each option below stops short of that on its own.
        halves = [{'id': 'half_a', 'cost': '0.5',
                   'changes': [{'target': 'gap', 'lower_us': 0, 'upper_us': 2900000000}]},
                  {'id': 'half_b', 'cost': '0.5',
                   'changes': [{'target': 'gap', 'lower_us': 0, 'upper_us': 2920000000}]}]
        split = self.catalogue(max_cost='1', options=halves)
        self.assertEqual(split['robust_patient_ids'], [],
                         'two insufficient options must not be combined into a sufficient one')
        self.assertEqual(split['excluded_by_budget'], [],
                         'both options are within the budget; they simply do not compose')
        whole = self.catalogue(max_cost='2', options=[
            {'id': 'widen', 'cost': '1.25',
             'changes': [{'target': 'gap', 'lower_us': 0, 'upper_us': 3000000000}]}])
        self.assertEqual(whole['robust_patient_ids'], ['P'],
                         'one authored option that reaches the same widening is accepted')

    def test_each_model_is_identifiable_from_its_own_result(self):
        self.assertEqual(self.catalogue(max_cost='2', options=load(
            'examples/robust-relaxation/policy.json')['options'])['profile'],
            'robust-temporal-relaxation-1.0')
        # The oracle result carries no profile; the decision records that its path is
        # recognised upstream, and that stamping it entails a fixture regeneration.
        self.assertNotIn('profile', self.oracle('C05'))

    def test_zero_cost_is_exact_so_the_oracle_needs_no_original_first_rule(self):
        exact = self.oracle('C01')
        self.assertEqual(exact['accepted_as'], 'EXACT')
        self.assertEqual(Decimal(exact['total_cost']), Decimal('0'))
        self.assertEqual(exact['relaxed_constraint_ids'], [])
        # Non-negative authored costs are what make zero the floor, and the floor is what
        # makes an unrelaxed binding win without an explicit original-first rule.
        for relaxation in self.pattern['relaxations']:
            declared = relaxation.get('cost') or relaxation.get('maximum_cost')
            self.assertGreaterEqual(Decimal(declared), 0)


if __name__ == '__main__':
    unittest.main()
