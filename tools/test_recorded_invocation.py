"""A report has to record how it was produced, not only what produced it.

A provenance digest proves which files were present. It cannot say which flags were
used, so a regeneration under different options passes every digest check while
describing a different run. That nearly happened: regenerating the integrated
evaluation with the tool's defaults would have replaced a three-worker measurement
with a one-worker one under the same filename, and dropped the fields that recorded
the difference, because they were attached by hand rather than emitted.
"""
import inspect
import json
import unittest

from tools import check_report_provenance as provenance
from tools.evaluate_integrated_recorded import execution_options

REPORT = provenance.ROOT / 'verification/integrated-recorded-evaluation.json'


class RecordedInvocationTests(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(REPORT.read_text())

    def test_the_committed_report_matches_the_options_it_records(self):
        """The cross-check that catches a report regenerated under other flags: the
        measurement and the recorded invocation have to describe the same run.
        """
        options = self.report['recorded_execution_options']
        self.assertEqual(self.report['concurrent_isolated_workers'],
                         min(options['workers'], len(options['strata'])))
        self.assertEqual([s['stratum'] for s in self.report['strata']], options['strata'])

    def test_the_committed_report_carries_the_command_that_made_it(self):
        expected = execution_options(
            self.report['recorded_execution_options']['strata'],
            self.report['recorded_execution_options']['workers'])
        self.assertEqual(self.report['reproduce_recorded_execution'],
                         expected['reproduce_recorded_execution'])

    def test_the_options_are_emitted_rather_than_attached(self):
        """If these stop being produced by the tool, they go back to being lost on
        every regeneration, which is what this exists to prevent.
        """
        for field in ('recorded_execution_options', 'reproduce_recorded_execution'):
            self.assertIn(field, execution_options(['arterial'], 1), field)

    def test_the_arguments_reach_the_recorded_options(self):
        """Checking the committed report alone cannot see this: the report already
        holds the right values, so a tool that stopped propagating its arguments would
        keep passing until the next regeneration quietly wrote the wrong ones.
        """
        options = execution_options(['art', 'noninvasive'], 3)['recorded_execution_options']
        self.assertEqual(options, {'workers': 3, 'strata': ['art', 'noninvasive']})
        self.assertEqual(execution_options(['arterial'], 1)['recorded_execution_options'],
                         {'workers': 1, 'strata': ['arterial']})

    def test_the_report_assembly_still_calls_the_helper(self):
        """`evaluate` needs Linux, so it cannot be exercised here. Removing the call
        would not fail any other test until a regeneration produced a report missing the
        fields, which is exactly the delay this change exists to remove.
        """
        from tools import evaluate_integrated_recorded as tool
        self.assertIn('execution_options(strata, workers)',
                      inspect.getsource(tool.evaluate))

    def test_a_different_schedule_is_visible_rather_than_silent(self):
        one = execution_options(['art', 'noninvasive', 'arterial'], 1)
        three = execution_options(['art', 'noninvasive', 'arterial'], 3)
        self.assertNotEqual(one, three, 'the worker count must reach the report')
        self.assertIn('--workers 3', three['reproduce_recorded_execution'])
        self.assertNotEqual(execution_options(['arterial'], 3),
                            execution_options(['art'], 3), 'the strata must reach it too')

    def test_the_generic_reproduce_command_is_kept_and_is_distinct(self):
        """It documents the default schedule, which is a different thing from what this
        run did; collapsing the two would lose one of them.
        """
        self.assertIn('reproduce', self.report)
        self.assertNotEqual(self.report['reproduce'],
                            self.report['reproduce_recorded_execution'])
        self.assertNotIn('--workers', self.report['reproduce'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
