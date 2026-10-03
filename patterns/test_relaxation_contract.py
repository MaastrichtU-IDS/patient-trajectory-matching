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
from . import exact_intervals as ei, robust_relaxation as rr

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
        # The oracle result itself still carries no profile, deliberately: the identifier
        # is stamped by the caller that chose the cost model, demo.cohort.run_cohort, not
        # by evaluate, which prices whatever pattern it is handed. Stamping it here would
        # change a file the release manifest pins, for a reporting field.
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


DAY = 86400 * 10 ** 6
MINUTE = 60 * 10 ** 6
# The exemplar's exposure_window: seven days from exposure to follow-up measurement.
LIMIT = 7 * DAY
BT = 'https://example.org/trajectory/bounded/'


class OneClinicalQuestionThroughBothSurfaces(unittest.TestCase):
    """The adoption item decision D left open: one question, both surfaces, compared.

    The question is the temporal core the two surfaces share -- *was there an
    administration followed, within seven days, by a creatinine measurement?* -- and both
    fixtures are generated from one timeline specification so that a divergence below is
    a divergence between the models and not between two hand-written fixtures.
    `test_both_surfaces_are_given_the_same_gap` is what earns that claim; the rest of this
    class is only meaningful while it holds.

    The question is the shared core and not the whole exemplar. The oracle also constrains
    a baseline-to-follow-up value delta and selects a baseline by minimum value, and the
    bounded profile expresses neither. Those parts are held fixed at the values C01 uses
    rather than compared, because there is nothing on the other surface to compare them to.
    """

    def setUp(self):
        self.pattern = load('examples/exemplar.pattern.json')
        self.taxonomy = load('ontology/toy-taxonomy.json')
        self.base = {c['case_id']: c for c in load('examples/cases.json')}['C01']

    def oracle_case(self, low_days, high_days):
        """C01 with the exposure moved; everything the bounded profile cannot say is kept."""
        case = copy.deepcopy(self.base)
        exposure = next(e for e in case['events'] if e['event_kind'] == 'administration')
        exposure['time'].update(start_min_us=-round(high_days * DAY), start_max_us=-round(low_days * DAY),
                                end_min_us=-round(high_days * DAY), end_max_us=-round(low_days * DAY))
        return case

    def bounded_source(self, low_days, high_days):
        """The same timeline as bounded intervals: follow-up at zero, exposure before it.

        The profile requires proper intervals (start + 1 <= end), so the two instants the
        oracle records as points are given a one-minute extent. Only the endpoints the gap
        is measured between -- the exposure's end and the follow-up's start -- carry the
        offsets under test, so the added extent cannot move the quantity being compared.
        """
        def variable(name, lower, upper):
            return {'id': name, 'lower_us': round(lower), 'upper_us': round(upper), 'patient_id': 'P',
                    'episode_id': 'E', 'clock_id': 'c', 'source_key': 'shared:' + name}

        def event(name, kind, start, end):
            return {'id': name, 'record_id': 'R-' + name, 'patient_id': 'P', 'episode_id': 'E',
                    'event_kind': kind, 'status': 'performed', 'start_var': start,
                    'end_var': end, 'source_key': 'shared:' + name}

        return {'profile': 'bounded-interval-1.0', 'dataset_id': 'shared-question',
                'snapshot_id': 'shared-question-v1',
                'clocks': [{'clock_id': 'c', 'origin': '2026-02-15T00:00:00Z', 'scope': 'patient:P',
                            'policy': 'offset-datetime-microseconds-v1'}],
                'variables': [variable('xs', -high_days * DAY - MINUTE, -low_days * DAY - MINUTE),
                              variable('xe', -high_days * DAY, -low_days * DAY),
                              variable('fs', 0, 0), variable('fe', MINUTE, MINUTE)],
                'events': [event('x', 'infusion', 'xs', 'xe'),
                           event('f', 'specimen_collection', 'fs', 'fe')],
                'constraints': []}

    def bounded_query(self):
        return {'profile': 'bounded-interval-query-1.0', 'id': 'exposure_then_followup',
                'slots': [{'id': 'exposure', 'class_iri': BT + 'Infusion'},
                          {'id': 'followup', 'class_iri': BT + 'SpecimenCollection'}],
                'constraints': [{'id': 'exposure_window', 'left': 'exposure', 'right': 'followup',
                                 'operator': 'gap', 'min_gap_us': 1, 'max_gap_us': LIMIT}]}

    def ask_oracle(self, low_days, high_days=None, *, budget):
        case = self.oracle_case(low_days, low_days if high_days is None else high_days)
        case['budget_override'] = {'max_relaxed_constraints': 2, 'max_total_cost': budget}
        return evaluate(case, self.pattern, self.taxonomy)

    def ask_catalogue(self, low_days, high_days=None, *, budget, options=()):
        policy = {'profile': rr.PROFILE, 'kind': 'bounded', 'relaxable_targets': ['exposure_window'],
                  'max_cost': budget, 'max_changed_targets': 1, 'options': list(options)}
        return rr.execute(self.bounded_source(low_days, low_days if high_days is None else high_days),
                          self.bounded_query(), policy)

    @staticmethod
    def widen_to(option_id, cost, days):
        return {'id': option_id, 'cost': cost,
                'changes': [{'target': 'exposure_window', 'lower_us': 1, 'upper_us': round(days * DAY)}]}

    @staticmethod
    def accepted(result):
        return result['robust_patient_ids'] == ['P']

    def test_both_surfaces_are_given_the_same_gap(self):
        """Read the gap back out of each generated fixture rather than trusting the input."""
        for low, high in ((7, 7), (9, 9), (6.5, 7.5)):
            with self.subTest(low=low, high=high):
                case = self.oracle_case(low, high)
                followup = next(e for e in case['events'] if e['event_id'] == 'F1')['time']
                exposure = next(e for e in case['events'] if e['event_kind'] == 'administration')['time']
                oracle_gap = (followup['start_min_us'] - exposure['start_max_us'],
                              followup['start_min_us'] - exposure['start_min_us'])
                variables = {v['id']: v for v in self.bounded_source(low, high)['variables']}
                # The bounded gap runs from the exposure's end to the follow-up's start.
                bounded_gap = (variables['fs']['lower_us'] - variables['xe']['upper_us'],
                               variables['fs']['upper_us'] - variables['xe']['lower_us'])
                self.assertEqual(oracle_gap, bounded_gap)
                self.assertEqual(oracle_gap, (round(low * DAY), round(high * DAY)))

    def test_the_two_surfaces_agree_when_no_declared_difference_is_in_play(self):
        """One day over the limit, each priced 0.5 on its own terms: both admit the patient.

        Agreement here is what makes the disagreements below evidence about the models.
        """
        relaxed = self.ask_oracle(8, budget='0.5')
        self.assertEqual(relaxed['accepted_as'], 'RELAXED')
        self.assertEqual(Decimal(relaxed['total_cost']), Decimal('0.5'))
        robust = self.ask_catalogue(8, budget='0.5', options=[self.widen_to('to_nine', '0.5', 9)])
        self.assertTrue(self.accepted(robust))
        self.assertEqual(robust['best_robust_matches'][0]['cost'], '0.5')

    def test_the_same_budget_number_selects_different_patients(self):
        """Two days over the limit at a stated budget of 0.5: in on one surface, out on the other.

        This is difference 2 reaching a cohort. The oracle prices the overrun in proportion
        to its size, so two days costs 1 and exceeds the budget. The catalogue prices the
        authored option, not the overrun, so the same patient is admitted at 0.5 by the very
        option that admitted the one-day overrun in the test above. Nothing is wrong with
        either answer; they are answers to the same question under the same number.
        """
        refused = self.ask_oracle(9, budget='0.5')
        self.assertEqual(refused['accepted_as'], 'NONE')
        self.assertEqual(Decimal(self.ask_oracle(9, budget='1')['total_cost']), Decimal('1'))
        admitted = self.ask_catalogue(9, budget='0.5', options=[self.widen_to('to_nine', '0.5', 9)])
        self.assertTrue(self.accepted(admitted))
        self.assertEqual(admitted['best_robust_matches'][0]['cost'], '0.5')
        self.assertEqual(admitted['excluded_by_budget'], [],
                         'the budget admitted the option; it is the pricing that differs, not the limit')

    def test_the_catalogue_prices_two_overruns_the_oracle_separates(self):
        """Difference 2 stated as pricing rather than as membership."""
        option = [self.widen_to('to_nine', '0.5', 9)]
        costs = []
        for days in (8, 9):
            robust = self.ask_catalogue(days, budget='0.5', options=option)
            self.assertTrue(self.accepted(robust), f'the option should still reach {days} days')
            costs.append(robust['best_robust_matches'][0]['cost'])
        self.assertEqual(costs, ['0.5', '0.5'], 'one authored option has one price')
        priced = [self.ask_oracle(days, budget='1')['total_cost'] for days in (8, 9)]
        self.assertEqual(priced, ['0.5', '1'], 'the oracle doubles the cost when the overrun doubles')

    def test_an_uncertain_exposure_time_splits_the_two_answers(self):
        """Difference 4. The same recorded uncertainty is admitted by one model and not the other.

        The exposure is recorded as somewhere between six and a half and seven and a half
        days before the follow-up, so the limit falls inside the recorded range. The oracle
        prices its worst case -- the half day past the limit costs 0.25 -- and admits the
        patient, while reporting the uncertainty in exact_status rather than in accepted_as.
        The catalogue refuses, because the binding is certain only in some feasible
        timelines, and reports the patient as possible instead. A reader comparing the two
        'matches' is not comparing like with like, which is why each result has to say which
        model produced it.
        """
        priced = self.ask_oracle(6.5, 7.5, budget='0.5')
        self.assertEqual(priced['accepted_as'], 'RELAXED')
        self.assertEqual(Decimal(priced['total_cost']), Decimal('0.25'))
        self.assertEqual(priced['exact_status'], 'INDETERMINATE')
        quantified = self.ask_catalogue(6.5, 7.5, budget='0.5')
        self.assertFalse(self.accepted(quantified))
        self.assertEqual(quantified['possible_patient_ids'], ['P'])
        self.assertEqual(quantified['certainty_semantics'],
                         'exists_catalogue_modification_exists_named_binding_forall_source_timelines')

    def test_a_free_option_never_displaces_the_unrelaxed_answer(self):
        """Difference 5: the same outcome by the two routes the decision records."""
        free = self.ask_catalogue(7, budget='0.5', options=[self.widen_to('free', '0', 9)])
        self.assertTrue(self.accepted(free))
        self.assertEqual(free['best_robust_matches'][0]['option_id'], 'original',
                         'the catalogue breaks the zero-cost tie by an explicit original-first rule')
        unrelaxed = self.ask_oracle(7, budget='0.5')
        self.assertEqual(unrelaxed['accepted_as'], 'EXACT')
        self.assertEqual(unrelaxed['relaxed_constraint_ids'], [],
                         'the oracle reaches the same outcome because zero is the floor, not by a rule')
        self.assertEqual(Decimal(unrelaxed['total_cost']),
                         Decimal(free['best_robust_matches'][0]['cost']))

    def test_the_catalogue_cannot_express_the_oracle_semantic_component(self):
        """Why difference 1 cannot be shown symmetrically on this question.

        The oracle's summed total reaches 2 on C05 by adding a concept substitution to a
        temporal overrun. A catalogue option relaxes a metric constraint, so the concept
        half of that sum has no target to be authored against and the policy is rejected
        before any budget is considered. Composition is therefore a difference in what the
        two vocabularies can say, not only in how they add up what they say.
        """
        policy = {'profile': rr.PROFILE, 'kind': 'bounded', 'relaxable_targets': ['exposure'],
                  'max_cost': '2', 'max_changed_targets': 1, 'options': []}
        with self.assertRaises(ei.ContractError) as refusal:
            rr.variants(self.bounded_query(), policy)
        self.assertIn('INVALID_RELAXABLE_TARGET', str(refusal.exception))

    def test_the_bounded_profile_refuses_the_instants_the_oracle_records(self):
        """The surfaces also differ before any cost is computed.

        The oracle's events are instants; the bounded profile requires a proper interval
        and rejects a zero-length one as an inconsistent source. The one-minute extent the
        builder adds is a translation step, not a shared representation, and a comparison
        that forgot it would be comparing a timeline neither surface was given.
        """
        source = self.bounded_source(7, 7)
        for name in ('xs', 'xe'):
            next(v for v in source['variables'] if v['id'] == name).update(lower_us=0, upper_us=0)
        blocked = rr.execute(source, self.bounded_query(),
                             {'profile': rr.PROFILE, 'kind': 'bounded', 'relaxable_targets': [],
                              'max_cost': '0', 'max_changed_targets': 0, 'options': []})
        self.assertEqual(blocked['status'], 'BLOCKED')
        self.assertIn('INCONSISTENT_SOURCE', blocked['reason'])

if __name__ == '__main__':
    unittest.main()
