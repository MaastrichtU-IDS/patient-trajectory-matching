"""Letter mentions timed from their letter's text (E0): the normaliser (`mention_time`) and the converter wrapper
(`synthetic_mentions`, rule `normalised_v1`).

- The normaliser's rules on constructed letters, and every age and relative time the generator's writer can print
  (`age_text`, `when_text`) read so that the range holds the day written about; the planted defects fail there.
- Truth-free: neither module names the generator's truth, its cases, a truth link, the time-expression annotation or a
  record's mentions (on the AST); the wrapper's output is the same with all of those but the mentions removed.
- The default is unchanged: with any other mention timing the wrapper returns the converter's output (or the gene
  wrapper's) byte for byte; no module a build, a benchmark or the page server runs imports either module.
- On the synthetic cohort: every narrowed range lies within D30's and meets the mention's true day (this test reads the
  truth, to score; the modules never do), the provenance names the rule and counts every mention, and each planted
  defect gives a miss.
- A store built through the wrapper (needs DuckDB, skipped without it): the default build's snapshot is the
  converter's; the normalised one differs, and holds the narrowed starts.
"""
import ast
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import mention_time as mt
from . import store_conditions_gene as gc
from . import synthetic_ingest as si
from . import synthetic_mentions as sm

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover - the store extra is optional
    duckdb = None

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000_000
BANNED = ('truth', 'truth_event_id', 'quality', 'time_expression', 'mentions')


def letter(day_text):
    return {'earliest': f'{day_text}T00:00', 'latest': f'{day_text}T23:59'}


def read(text, mention, on='2021-06-15', birth='2019-03-10', previous=None):
    """The normaliser on `text`, for the first occurrence of `mention`."""
    s = text.index(mention)
    return mt.normalise(text, [s, s + len(mention)], letter(on), birth, previous and letter(previous))


def days(got, birth='2019-03-10'):
    b = date.fromisoformat(birth)
    return (date.fromisoformat(got['earliest'][:10]) - b).days, (date.fromisoformat(got['latest'][:10]) - b).days


def generator():
    sys.path.insert(0, str(ROOT / 'demos/mockups/synthetic'))
    try:
        import generate
    finally:
        sys.path.pop(0)
    return generate


class RuleTests(unittest.TestCase):
    def test_explicit_dates_are_read_day_first_or_iso(self):
        for text in ('Gezien op 19-10-2019 met een koortsstuip van 20 minuten.', 'hospitalisé pour un koortsstuip le 19/10/2019.',
                     'A koortsstuip on 19.10.2019.', 'A koortsstuip on 2019-10-19.'):
            got = read(text, 'koortsstuip')
            self.assertEqual((got['earliest'], got['latest'], got['rule'], got['anchor']),
                             ('2019-10-19T00:00', '2019-10-19T23:59', 'date', 'calendar'), text)
        self.assertEqual(read('A koortsstuip on 31-02-2019.', 'koortsstuip'), {'reason': 'invalid_date'})

    def test_relative_to_the_letter(self):
        got = read('Since the last review: a seizure (this week). ', 'a seizure')
        self.assertEqual((got['earliest'], got['latest'], got['anchor']), ('2021-06-09T00:00', '2021-06-15T23:59', 'letter'))
        got = read('Also a seizure, lasting about 20 minutes (about a week ago). ', 'a seizure')
        self.assertEqual((got['earliest'], got['latest'], got['rule']), ('2021-06-04T00:00', '2021-06-12T23:59', 'weeks_ago'))
        got = read('Also a seizure (about 3 weeks ago). ', 'a seizure')
        self.assertEqual((got['earliest'], got['latest']), ('2021-05-21T00:00', '2021-05-29T23:59'))

    def test_ages_from_birth(self):
        self.assertEqual(days(read('Background: a seizure at 45 days. ', 'a seizure')), (44, 46))
        got = read('Also a seizure (at around 8 months). ', 'a seizure')
        self.assertEqual(got['rule'], 'age_months')
        lo, hi = days(got)
        self.assertLessEqual(lo, int(7.5 * 30.44))
        self.assertGreaterEqual(hi, int(8.5 * 30.44) + 1)
        self.assertLess(hi - lo, 40)
        got = read('Background: a seizure at 2 years 3 months; ataxia at 4 years. ', 'a seizure')
        self.assertEqual(got['rule'], 'age_years_months')
        lo, hi = days(got)
        self.assertTrue(lo <= 730 + int(2.5 * 30.44) and hi >= 730 + int(3.5 * 30.44) + 1 and hi - lo < 40)

    def test_n_years_is_the_whole_year_of_age_under_either_reading(self):
        got = read('Background: a seizure at 1 years 3 months; ataxia at 4 years. ', 'ataxia', birth='2016-02-29')
        self.assertEqual(got['rule'], 'age_years')
        lo, hi = days(got, '2016-02-29')
        self.assertEqual(lo, min(4 * 365, (date(2020, 2, 29) - date(2016, 2, 29)).days))
        self.assertEqual(hi, max(4 * 365 + 364, (date(2021, 2, 28) - date(2016, 2, 29)).days - 1))
        with patch.object(mt, '_DEFECT', 'naive_years'):
            self.assertLess(days(read('Ataxia at 4 years. ', 'Ataxia'))[1] - days(read('Ataxia at 4 years. ', 'Ataxia'))[0], 40)

    def test_the_clause_bounds_what_is_read(self):
        text = 'Background: a febrile seizure at 8 months; myoclonic jerks at 1 years 3 months. Valproate was started. '
        self.assertEqual(read(text, 'a febrile seizure')['expression'], 'at 8 months')
        self.assertEqual(read(text, 'myoclonic jerks')['expression'], 'at 1 years 3 months')
        self.assertEqual(read(text, 'Valproate', previous='2021-02-01'),
                         {'earliest': '2021-02-01T00:00', 'latest': '2021-06-15T23:59', 'rule': 'since_previous_letter',
                          'anchor': 'previous_letter', 'expression': 'was started'})
        self.assertEqual(read('Also a seizure, lasting about 20 minutes. ', 'a seizure'), {'reason': 'no_expression'})

    def test_cues_since_the_previous_letter(self):
        for text, mention in (('This confirms Dravet syndrome. ', 'Dravet syndrome'), ('Gestart met valproïnezuur. ', 'valproïnezuur'),
                              ('Valproate was started by the regional hospital. ', 'Valproate')):
            self.assertEqual(read(text, mention, previous='2021-01-04')['earliest'], '2021-01-04T00:00', text)
            self.assertEqual(read(text, mention), {'reason': 'no_previous_letter'}, text)
        # A date in the clause is more specific than the cue.
        self.assertEqual(read('Gestart met valproïnezuur op 12-03-2020. ', 'valproïnezuur', previous='2019-01-04')['rule'], 'date')
        # Not cues: a genetic result and a reclassification (timed by the sample in the store).
        for text, mention in (('Gene panel result: pathogenic SCN1A variant. ', 'pathogenic SCN1A variant'),
                              ('The laboratory has reclassified the SCN1A variant as pathogenic. ', 'SCN1A variant as pathogenic'),
                              ('Current medication: valproic acid, clobazam. ', 'clobazam')):
            self.assertEqual(read(text, mention, previous='2021-01-04'), {'reason': 'no_expression'}, text)

    def test_refusals(self):
        self.assertEqual(read('Valproate was stopped about 3 weeks ago because seizures worsened. ', 'Valproate'),
                         {'reason': 'stop_cue'})
        self.assertEqual(read('A seizure at 8 months and again at 2 years. ', 'A seizure'), {'reason': 'several_expressions'})
        # The same range written twice is one expression's.
        self.assertEqual(read('A seizure (this week), this week. ', 'A seizure')['rule'], 'this_week')

    def test_add_months_clamps_the_day(self):
        self.assertEqual(mt.add_months(date(2019, 1, 31), 1), date(2019, 2, 28))
        self.assertEqual(mt.add_months(date(2016, 2, 29), 12), date(2017, 2, 28))
        self.assertEqual(mt.add_months(date(2019, 11, 30), 14), date(2021, 1, 30))


class WriterTests(unittest.TestCase):
    """Every age and relative time the generator prints holds the day it was printed for, under the normaliser."""
    BIRTHS = ('2019-03-10', '2016-02-29', '2018-12-31', '2020-01-31')

    def ages_missed(self):
        g = generator()
        missed = []
        for birth in self.BIRTHS:
            b = date.fromisoformat(birth)
            on = (b + timedelta(days=8000)).isoformat()
            for day in range(0, 7300):
                for text in (f'Background: a seizure at {g.age_text(day)}. ', f'Also a seizure (at around {g.age_text(day)}). '):
                    lo, hi = days(read(text, 'a seizure', on=on, birth=birth), birth)
                    if not lo <= day <= hi:
                        missed.append((birth, day, g.age_text(day)))
        return missed

    def test_every_age_the_generator_prints_holds_its_day(self):
        self.assertEqual(self.ages_missed(), [])

    def test_every_relative_time_the_generator_prints_holds_its_day(self):
        g = generator()
        visit = 2000
        for birth in self.BIRTHS:
            on = (date.fromisoformat(birth) + timedelta(days=visit)).isoformat()
            for gap in range(0, 400):
                text = f'Also a seizure ({g.when_text(None, {"day": visit - gap}, visit)}). '
                lo, hi = days(read(text, 'a seizure', on=on, birth=birth), birth)
                self.assertTrue(lo <= visit - gap <= hi, (birth, gap, text))

    def test_the_planted_age_defects_miss(self):
        for name in ('naive_years', 'month_off_by_one'):
            with patch.object(mt, '_DEFECT', name):
                self.assertTrue(self.ages_missed(), name)


class TruthFreeTests(unittest.TestCase):
    def test_the_modules_name_no_truth_case_link_annotation_or_mentions(self):
        for module in (mt, sm):
            tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
            names = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            for word in BANNED:
                self.assertNotIn(word, names, f'{module.__name__} names {word}')
            imports = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
            self.assertNotIn('generate', imports)

    def test_the_output_is_the_same_without_the_truth(self):
        cohort = _STATE['cohort']
        stripped = json.loads(json.dumps(cohort))
        for p in stripped['patients']:
            p.pop('truth')
            p['quality'] = []
            for r in p['records']:
                r.pop('truth_event_id', None)
                for m in r.get('mentions', []):
                    m.pop('truth_event_id', None)
                    m.pop('time_expression', None)
        self.assertEqual(json.dumps(sm.synthetic_ingest(stripped, sm.NORMALISED), sort_keys=True),
                         json.dumps(_STATE['normalised'], sort_keys=True))


class DefaultTests(unittest.TestCase):
    def test_any_other_timing_is_the_converter_byte_for_byte(self):
        cohort = _STATE['cohort']
        for timing in ('at_or_before_letter', 'letter'):
            self.assertEqual(json.dumps(sm.synthetic_ingest(cohort, timing)), json.dumps(si.synthetic_ingest(cohort, timing)))
        self.assertEqual(json.dumps(sm.synthetic_ingest(cohort, series=True, base=gc)),
                         json.dumps(gc.synthetic_ingest(cohort, series=True)))
        with self.assertRaises(ValueError):
            sm.synthetic_ingest(cohort, 'normalised_v2')

    def test_no_build_benchmark_or_page_module_imports_the_option(self):
        allowed = {'patterns/synthetic_mentions.py', 'patterns/test_synthetic_mentions.py', 'tools/e0_measure.py',
                   'tools/test_e0_measure.py'}
        for path in sorted([*ROOT.glob('patterns/*.py'), *ROOT.glob('tools/*.py'), *ROOT.glob('demos/mockups/*.py')]):
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding='utf-8'))
            names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
            names |= {n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            if rel not in allowed:
                self.assertFalse({x for x in names if x.split('.')[-1] in ('synthetic_mentions', 'mention_time')}, rel)


class CohortTests(unittest.TestCase):
    def test_narrowed_ranges_lie_within_d30_and_meet_the_true_day(self):
        """E0 rule 1 on the page's cohort. This test reads the truth, to score."""
        misses, counts = score(_STATE['cohort'], _STATE['normalised'])
        self.assertEqual(misses, [])
        self.assertEqual(counts['mentions'], 2444)
        self.assertEqual(counts['narrowed'], 849)

    def test_the_provenance_names_the_rule_and_counts_every_mention(self):
        prov = _STATE['normalised']['provenance']
        self.assertEqual(prov['mention_time'], sm.RULE_TEXT)
        self.assertEqual(prov['converter_sha256'], si.synthetic_provenance(_STATE['cohort'], {})['converter_sha256'])
        self.assertEqual(set(prov['mention_normaliser']['modules']), set(sm.MODULES))
        self.assertEqual(sum(prov['mention_normaliser']['counts'].values()), 2444)
        self.assertEqual(prov['skipped'], si.synthetic_ingest(_STATE['cohort'])['provenance']['skipped'])

    def test_only_mention_starts_change(self):
        plain = si.synthetic_ingest(_STATE['cohort'])
        for a, b in zip(plain['patients'], _STATE['normalised']['patients']):
            self.assertEqual([e['event_id'] for e in a['events']], [e['event_id'] for e in b['events']])
            self.assertEqual(a['coverage'], b['coverage'])
            for x, y in zip(a['events'], b['events']):
                if x['kind'] != 'mention':
                    self.assertEqual(x, y)
                    continue
                self.assertLessEqual(x['start'][0], y['start'][0])
                self.assertLessEqual(y['start'][0], y['start'][1])
                self.assertLessEqual(y['start'][1], x['start'][1])
                y = {**y, 'evidence': {k: v for k, v in y['evidence'].items() if k != 'mention_time'}}
                self.assertEqual({**x, 'start': None}, {**y, 'start': None})

    def test_a_range_outside_d30_falls_back(self):
        cohort = json.loads(json.dumps(_STATE['cohort']))
        p = cohort['patients'][0]
        note = next(r for r in p['records'] if r['kind'] == 'note' and r['text'].find('This confirms ') >= 0)
        s = note['text'].index('This confirms ') + len('This confirms ')
        note['text'] = note['text'][:s] + note['text'][s:].replace('. ', ' on 01-01-1990. ', 1)
        counts = {}
        out, _ = sm.convert_patient(p, si._codes(cohort), sm.NORMALISED, counts=counts)
        self.assertGreater(counts.get('fallback_outside_d30', 0), 0)
        plain, _ = si.convert_patient(p, si._codes(cohort))
        self.assertTrue(all(x['start'][0] <= y['start'][0] <= y['start'][1] <= x['start'][1]
                            for x, y in zip(plain['events'], out['events'])))

    def test_each_planted_defect_misses(self):
        for name in mt.DEFECTS:
            with patch.object(mt, '_DEFECT', name):
                misses, _ = score(_STATE['cohort'], sm.synthetic_ingest(_STATE['cohort'], sm.NORMALISED))
            self.assertTrue(misses, name)


@unittest.skipUnless(duckdb, 'needs DuckDB (patterns/requirements-store.lock.txt)')
class StoreTests(unittest.TestCase):
    def test_the_default_build_is_the_converters_and_the_normalised_one_differs(self):
        from . import execution_store as es
        cohort = _STATE['cohort']
        with tempfile.TemporaryDirectory(prefix='e0-store-') as tmp:
            plain = Path(es.build(si.synthetic_ingest(cohort), Path(tmp) / 'a')).name
            same = Path(es.build(sm.synthetic_ingest(cohort), Path(tmp) / 'b')).name
            narrowed = es.build(_STATE['normalised'], Path(tmp) / 'c')
            self.assertEqual(plain, same)
            self.assertNotEqual(plain, Path(narrowed).name)
            event = next(e for p in _STATE['normalised']['patients'] for e in p['events'] if 'mention_time' in e['evidence'])
            with es.open_store(narrowed) as store:
                got = store.query('SELECT start_lo, start_hi FROM events WHERE event_id = ?', [event['event_id']])
            self.assertEqual([list(r) for r in got], [event['start']])


def score(cohort, ingest):
    """(misses, counts): narrowed mentions whose range misses the truth day (D30's test of the bound)."""
    misses, counts = [], {'mentions': 0, 'narrowed': 0}
    for p, converted in zip(sorted(cohort['patients'], key=lambda p: p['patient_id']), ingest['patients']):
        truth = {e['event_id']: e for e in p['truth']['events']}
        records = {r['record_id']: r for r in p['records']}
        birth = datetime.fromisoformat(p['birth_date'][:10] + 'T00:00')
        for e in converted['events']:
            if e['kind'] != 'mention':
                continue
            counts['mentions'] += 1
            if 'mention_time' not in e['evidence']:
                continue
            counts['narrowed'] += 1
            record_id, i = e['event_id'].split('/', 1)[1].split('#m')
            day = truth[records[record_id]['mentions'][int(i)]['truth_event_id']]['day']
            lo = si.micros((birth + timedelta(days=day)).isoformat(timespec='minutes'))
            if not (e['start'][0] <= lo + DAY - 1 and lo <= e['start'][1]):
                misses.append(e['event_id'])
    return misses, counts


_STATE = {}


def setUpModule():
    with tempfile.TemporaryDirectory(prefix='e0-cohort-') as tmp:
        subprocess.run([sys.executable, str(ROOT / 'demos/mockups/synthetic/generate.py'), '--output', f'{tmp}/cohort.json'],
                       check=True, capture_output=True, cwd=ROOT)
        _STATE['cohort'] = json.loads(Path(f'{tmp}/cohort.json').read_text(encoding='utf-8'))
    _STATE['normalised'] = sm.synthetic_ingest(_STATE['cohort'], sm.NORMALISED)


if __name__ == '__main__':
    unittest.main()
