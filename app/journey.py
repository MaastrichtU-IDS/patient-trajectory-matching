"""One bounded journey over the same authored patients from baseline to evidence."""
from collections import Counter, OrderedDict
from copy import deepcopy
from datetime import datetime
import hashlib
import json

from app.interval_editor import LIMITS as EDITOR_LIMITS, OPTION_COST, keys, compile_query, compile_policy, classifications
from app import pattern_builder, relaxation_catalogue
from app.temporal import ROOT, check_limits, digest
from patterns import bounded_intervals as bt, robust_relaxation as relax

BASELINE_PATH = 'examples/patient-journey/baseline.json'
SOURCE_PATH = 'examples/patient-journey/three-event-source.json'
LIMITS = {**EDITOR_LIMITS, 'events': 12, 'variables': 24, 'slots': 3,
          'query_constraints': 6, 'candidate_bindings_per_evaluation': 32,
          'catalogue_options': 3, 'evaluations': 4}
ARTIFACTS = ('app/pattern_builder.py', 'app/relaxation_catalogue.py', 'app/journey.py', 'app/interval_editor.py', 'app/temporal.py',
             'app/interval_explanations.py', 'app/temporal_replay.py',
             'app/journey.html', 'app/journey.js', 'app/journey.css', BASELINE_PATH, SOURCE_PATH)
DEFAULT_REQUEST = {'reference_patient_id': 'T03', 'top_k': 1, 'maximum_baseline': None,
                   'question': 'overlap', 'budget': OPTION_COST}
RANKING_METHOD = ('Absolute difference in an authored pre-index demonstration score; '
                  'similarity = 1 - distance / 100. Missing scores are unrankable and shown last; '
                  'ties use patient ID. No treatment, collection, or outcome data enter ranking. '
                  'Top k limits the displayed shortlist, never the temporal evaluation pool.')
QUESTIONS = [
    {'id': 'overlap', 'label': 'Collection during a ten-minute infusion, sharing at least three minutes',
     'option': 'Lower shared time from three to two minutes; retain contains and ten-minute duration.'},
    {'id': 'sequential', 'label': 'Collection starts zero to 48 minutes after infusion completion',
     'option': 'Widen the signed gap upper bound from 48 to 50 minutes; retain the zero lower bound.'},
    {'id': 'custom', 'label': 'Build a two- or three-event trajectory',
     'option': 'Declare up to three metric relaxation options; every option preserves the original source and Allen relations.'}]


def controls_for(request):
    controls = {'fixture': 'overlap', 'relation': 'contains', 'gap': None,
                'duration': {'minimum_minutes': '10', 'maximum_minutes': '10'},
                'minimum_overlap_minutes': '3',
                'relaxation': {'max_cost': request['budget'], 'gap': None, 'duration': None,
                               'minimum_overlap_minutes': '2'}}
    if request['question'] == 'sequential':
        controls.update(relation='gap', gap={'minimum_minutes': '0', 'maximum_minutes': '48'},
                        duration=None, minimum_overlap_minutes=None)
        controls['relaxation'].update(gap={'minimum_minutes': '0', 'maximum_minutes': '50'},
                                      minimum_overlap_minutes=None)
    return controls


def select_and_rank(patients, request):
    """Pure baseline-only selection: follow-up records are not an input."""
    reference = next(p for p in patients if p['patient_id'] == request['reference_patient_id'])
    rows, ranked = [], []
    for patient in patients:
        if patient['patient_id'] == reference['patient_id']:
            continue
        value, maximum = patient['baseline']['value'], request['maximum_baseline']
        status, reason = 'ELIGIBLE', 'No baseline eligibility filter is applied.'
        if maximum is not None:
            if value is None:
                status, reason = 'UNRESOLVED', 'Baseline score is missing; eligibility cannot be determined.'
            elif value > maximum:
                status, reason = 'EXCLUDED', 'Baseline score exceeds the selected maximum.'
            else:
                reason = 'Baseline score is at or below the selected maximum.'
        rows.append({'patient_id': patient['patient_id'], 'status': status, 'reason': reason,
                     'baseline': deepcopy(patient['baseline'])})
        if status == 'ELIGIBLE':
            ref_value = reference['baseline']['value']
            distance = None if value is None or ref_value is None else abs(value - ref_value)
            ranked.append({'patient_id': patient['patient_id'], 'baseline': deepcopy(patient['baseline']),
                           'distance': distance, 'similarity': None if distance is None else (100-distance)/100})
    ranked.sort(key=lambda row: (row['distance'] is None, row['distance'] or 0, row['patient_id']))
    for index, row in enumerate(ranked):
        row['rank'] = index + 1 if row['distance'] is not None else None
    # Unrankable histories remain in the evaluated pool but are not described as nearest neighbours.
    displayed = [row['patient_id'] for row in ranked if row['rank'] is not None][:request['top_k']]
    eligibility = {'rows': rows, 'maximum_baseline': request['maximum_baseline'],
                   **{name: [r['patient_id'] for r in rows if r['status'] == status]
                      for name, status in (('eligible_patient_ids', 'ELIGIBLE'),
                                           ('excluded_patient_ids', 'EXCLUDED'),
                                           ('unresolved_patient_ids', 'UNRESOLVED'))}}
    ranking = {'method': RANKING_METHOD, 'all_candidates': ranked, 'displayed_patient_ids': displayed,
               'top_k': request['top_k']}
    return deepcopy(reference), eligibility, ranking


def filter_source(source, eligible):
    if not eligible:
        return None
    selected = deepcopy(source)
    selected['events'] = [e for e in selected['events'] if e['patient_id'] in eligible]
    selected['variables'] = [v for v in selected['variables'] if v['patient_id'] in eligible]
    clocks = {v['clock_id'] for v in selected['variables']}
    selected['clocks'] = [c for c in selected['clocks'] if c['clock_id'] in clocks]
    # The admitted fixtures have no cross-record source constraints. Never silently prune them.
    if selected['constraints']:
        raise ValueError('Journey fixture must have no source constraints')
    return selected


class JourneyWorkspace:
    def __init__(self):
        self._baseline = json.loads((ROOT / BASELINE_PATH).read_text())
        self._source = json.loads((ROOT / SOURCE_PATH).read_text())
        self._reports = OrderedDict()
        ids = {p['patient_id'] for p in self._baseline['patients']}
        if len(ids) != len(self._baseline['patients']):
            raise ValueError('Duplicate authored baseline patient')
        for patient in self._baseline['patients']:
            baseline = patient['baseline']
            if baseline['value'] is not None:
                if type(baseline['value']) is not int or not 0 <= baseline['value'] <= 100:
                    raise ValueError('Invalid authored baseline score')
                index = datetime.fromisoformat(patient['index_at'])
                if any(datetime.fromisoformat(baseline[k]) >= index for k in ('observed_at', 'recorded_at')):
                    raise ValueError('Baseline evidence must be observed and recorded before the index')
        bt.validate(self._source)
        if {e['patient_id'] for e in self._source['events']} != ids or self._source['constraints']:
            raise ValueError('Journey fixture must describe the same patients without source constraints')

    def metadata(self):
        return {'scope': 'authored-patient-to-cohort-journey', 'patients': deepcopy(self._baseline['patients']),
                'default_request': deepcopy(DEFAULT_REQUEST), 'questions': deepcopy(QUESTIONS),
                'builder': pattern_builder.metadata(),
                'catalogue': relaxation_catalogue.metadata(),
                'budgets': ['0', OPTION_COST], 'limits': deepcopy(LIMITS), 'ranking_method': RANKING_METHOD,
                'baseline_provenance': self._baseline['provenance'],
                'source_sha256': digest(self._source),
                'source_note': 'All questions evaluate the same three-event authored patient histories; choosing a question never changes source times.'}

    def run(self, request):
        keys(request, (*DEFAULT_REQUEST, *(name for name in ('pattern', 'catalogue')
                                         if type(request) is dict and name in request)))
        if type(request['reference_patient_id']) is not str or request['reference_patient_id'] not in {
                p['patient_id'] for p in self._baseline['patients']}:
            raise ValueError('Select an authored reference patient')
        if type(request['top_k']) is not int or not 1 <= request['top_k'] <= 3:
            raise ValueError('Top k must be an integer from 1 to 3')
        maximum = request['maximum_baseline']
        if maximum is not None and (type(maximum) is not int or not 0 <= maximum <= 100):
            raise ValueError('Maximum baseline must be null or an integer from 0 to 100')
        if type(request['question']) is not str or request['question'] not in ('overlap', 'sequential', 'custom'):
            raise ValueError('Select overlap, sequential or custom')
        pattern = None
        if request['question'] == 'custom':
            if 'catalogue' not in request and request['budget'] != '0':
                raise ValueError('Custom patterns currently require budget 0')
            query = pattern_builder.compile_pattern(request.get('pattern'))
            pattern = pattern_builder.decompile_query(query)
            controls = None
            policy = relaxation_catalogue.compile_catalogue(
                query, request.get('catalogue', relaxation_catalogue.EMPTY_CATALOGUE), request['budget'])
        else:
            if 'pattern' in request or 'catalogue' in request:
                raise ValueError('Pattern and catalogue controls apply only to a custom question')
            if type(request['budget']) is not str or request['budget'] not in ('0', OPTION_COST):
                raise ValueError('Budget must be the string 0 or 1.25')
            controls = controls_for(request)
            query = compile_query(controls)
            policy = compile_policy(controls, query)
        reference, eligibility, ranking = select_and_rank(self._baseline['patients'], request)
        admitted_source = self._source
        source = filter_source(admitted_source, eligibility['eligible_patient_ids'])
        if source is not None:
            workload = check_limits(source, query, policy, limits=LIMITS)
            relaxation = relax.execute(source, query, policy)
            if relaxation.get('status') != 'COMPLETED' or relaxation.get('search_complete') is not True or any(
                    e['result'].get('search_complete') is not True for e in relaxation['evaluations']):
                raise ValueError('Incomplete journey evaluation; no result or export is available')
            result = relaxation['evaluations'][0]['result']
        else:
            workload = {name: 0 for name in LIMITS if name != 'saved_results'}
            result = {'search_complete': True, 'scope': 'empty-eligible-pool', 'trajectories': [],
                      'certain_patient_ids': [], 'possible_patient_ids': []}
            relaxation = {'status': 'COMPLETED', 'search_complete': True, 'reason': 'EMPTY_ELIGIBLE_POOL',
                          'evaluations': [], 'robust_patient_ids': [], 'possible_patient_ids': [],
                          'best_robust_matches': [], 'excluded_by_budget': relax.variants(query, policy)[1]}
        options = {r['patient_id']: r for e in relaxation['evaluations'][1:] for r in classifications(e['result'])}
        option_statuses = {e['option']['id']: {r['patient_id']: r['status'] for r in classifications(e['result'])}
                           for e in relaxation['evaluations'][1:]}
        best = {r['patient_id']: r for r in relaxation['best_robust_matches']}
        ranks = {r['patient_id']: r for r in ranking['all_candidates']}
        patients = []
        from app.interval_explanations import explain_patient
        for original in classifications(result):
            pid = original['patient_id']
            chosen = best.get(pid)
            records = [deepcopy(e) for e in source['events'] if e['patient_id'] == pid]
            patients.append({'patient_id': pid, 'episode_id': original['episode_id'],
                             'rank': ranks[pid]['rank'], 'baseline': deepcopy(ranks[pid]['baseline']),
                             'original_status': original['status'],
                             'option_status': None if request['question'] == 'custom' else options.get(pid, {}).get('status'),
                             'option_results': [
                                 {'option_id': option['id'], 'cost': option['cost'],
                                  'status': option_statuses.get(option['id'], {}).get(pid),
                                  'excluded_by_budget': option['id'] in relaxation['excluded_by_budget'],
                                  'selected': chosen is not None and chosen['option_id'] == option['id']}
                                 for option in policy['options']],
                             'selected_option': chosen['option_id'] if chosen else None,
                             'selected_cost': chosen['cost'] if chosen else None,
                             'evidence': {'treatment': '; '.join('Infusion ' + e['status'] + ' (authored record).'
                                                               for e in records if e['event_kind'] == 'infusion') or 'Not recorded.',
                                          'observation': '; '.join('Specimen collection ' + e['status'] + ' (authored record).'
                                                                   for e in records if e['event_kind'] == 'specimen_collection') or 'Not recorded.',
                                          'clinical_outcome': 'Not recorded in this authored fixture; collection is not an efficacy outcome.',
                                          'records': records},
                             'explanation': explain_patient(source, query, relaxation, pid, policy=policy)})
        report = {'format': 'patient-journey-export-1', 'scope': 'authored-patient-to-cohort-journey',
                  'request': deepcopy(request), 'reference_patient': reference,
                  'baseline_source': deepcopy(self._baseline), 'ranking': ranking, 'eligibility': eligibility,
                  'patients': patients, 'original_counts': dict(sorted(Counter(p['original_status'] for p in patients).items())),
                  'added_robust_patient_ids': sorted(set(relaxation['robust_patient_ids']) - set(result['certain_patient_ids'])),
                  'controls': controls, 'pattern': pattern, 'query': query, 'policy': policy, 'source': source,
                  'admitted_source_sha256': digest(admitted_source), 'result': result, 'relaxation': relaxation,
                  'limits': deepcopy(LIMITS), 'workload': workload,
                  'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS}}
        report['report_id'] = digest(report)
        self._reports[report['report_id']] = deepcopy(report)
        self._reports.move_to_end(report['report_id'])
        while len(self._reports) > LIMITS['saved_results']:
            self._reports.popitem(last=False)
        return report

    def export(self, report_id):
        return deepcopy(self._reports[report_id])


def verify(bundle):
    if type(bundle) is not dict or bundle.get('format') != 'patient-journey-export-1':
        raise ValueError('Unsupported patient journey export')
    if bundle.get('report_id') != digest({k: v for k, v in bundle.items() if k != 'report_id'}):
        raise ValueError('Patient journey export fingerprint differs')
    expected = JourneyWorkspace().run(bundle.get('request'))
    if expected != bundle:
        raise ValueError('Patient journey export differs from replay on admitted fixtures and implementation')
    return {'verified': True, 'report_id': expected['report_id'],
            'certain_patient_ids': expected['result']['certain_patient_ids'],
            'robust_patient_ids': expected['relaxation']['robust_patient_ids']}
