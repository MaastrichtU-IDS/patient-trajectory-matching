"""Bounded, authored H0 history ranking and immutable refinement snapshots.

This module operates on a reviewed synthetic projection; it is not a source adapter,
clinical similarity model, ontology reasoner or production patient-record service.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from hashlib import sha256
from fractions import Fraction
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/patient-similarity'
COMPONENTS = ('age_band', 'baseline_creatinine', 'clinical_concepts')
HOUR = 3_600_000_000
MAX_PATIENTS = 1000
MAX_RECORDS = 20000
MAX_REVISIONS = 256


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def decimal(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('DECIMAL_STRING_REQUIRED')
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError('INVALID_DECIMAL') from error
    if not result.is_finite() or not -12 <= result.as_tuple().exponent <= 6 or result.copy_abs() > Decimal('1000000'):
        raise ValueError('DECIMAL_OUT_OF_BOUNDS')
    return result


def lexical(value):
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError('INVALID_FIELDS')


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError('INVALID_TEXT')


def _time(value):
    if type(value) is not int or abs(value) > 2**53-1:
        raise ValueError('INVALID_MICROSECOND_TIME')


def _read(value, default):
    if value is None:
        value = default
    if isinstance(value, (str, Path)):
        with Path(value).open('rb') as handle:
            raw = handle.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('SNAPSHOT_SIZE_LIMIT')
        value = json.loads(raw)
    copied = deepcopy(value)
    if len(json.dumps(copied, allow_nan=False)) > 8 * 1024 * 1024:
        raise ValueError('SNAPSHOT_SIZE_LIMIT')
    return copied


class SimilarityEngine:
    """An immutable admitted dataset and content-addressed revision repository."""
    def __init__(self, dataset=None, profile=None):
        self._dataset = _read(dataset, EXAMPLE / 'dataset.json')
        self._profile = _read(profile, EXAMPLE / 'profile.json')
        self._validate()
        self._patients = {p['patient_id']: p for p in self._dataset['patients']}
        self._features = {pid: self._extract(patient) for pid, patient in self._patients.items()}
        self._dataset_hash = digest(self._dataset)
        self._profile_hash = digest(self._profile)
        self._implementation_hash = sha256(Path(__file__).read_bytes()).hexdigest()
        self._revisions = {}

    def _validate(self):
        d, p = self._dataset, self._profile
        _keys(d, ('dataset_id', 'label', 'scope', 'clinical_validity_claim', 'index_rule', 'patients'), ('construction_origin',))
        for key in ('dataset_id', 'label', 'index_rule'):
            _text(d[key])
        if d['scope'] != 'authored_synthetic' or d['clinical_validity_claim'] is not False:
            raise ValueError('AUTHORED_SYNTHETIC_PROFILE_ONLY')
        _keys(p, ('profile_id', 'clinical_validity_claim', 'weights', 'minimum_coverage', 'history_us', 'creatinine_window_us', 'creatinine_scale', 'creatinine_unit', 'concept_ancestors', 'mapping_review'))
        _text(p['profile_id'])
        if p['clinical_validity_claim'] is not False or p['mapping_review'] != 'authored_synthetic_review':
            raise ValueError('AUTHORED_MAPPING_REVIEW_REQUIRED')
        self._weights(p['weights'])
        if not 0 < decimal(p['minimum_coverage']) <= 1:
            raise ValueError('INVALID_MINIMUM_COVERAGE')
        for key in ('history_us', 'creatinine_window_us'):
            _time(p[key])
            if not 0 < p[key] <= 365 * 24 * HOUR:
                raise ValueError('INVALID_HISTORY_WINDOW')
        if p['creatinine_window_us'] > p['history_us'] or decimal(p['creatinine_scale']) <= 0 or p['creatinine_unit'] != 'mg/dL':
            raise ValueError('UNSUPPORTED_MEASUREMENT_PROFILE')
        if not isinstance(p['concept_ancestors'], dict) or len(p['concept_ancestors']) > 100:
            raise ValueError('INVALID_CONCEPT_CATALOGUE')
        for concept, ancestors in p['concept_ancestors'].items():
            _text(concept)
            if not isinstance(ancestors, list) or len(ancestors) > 100 or len(set(ancestors)) != len(ancestors):
                raise ValueError('INVALID_ANCESTORS')
            for ancestor in ancestors:
                _text(ancestor)
        if not isinstance(d['patients'], list) or not 2 <= len(d['patients']) <= MAX_PATIENTS:
            raise ValueError('PATIENT_BOUND')
        if 'construction_origin' in d:
            origin = d['construction_origin']
            _keys(origin, ('path', 'sha256', 'availability_policy', 'original_availability_policy', 'scope'))
            for value in origin.values():
                _text(value)
            if len(origin['sha256']) != 64 or any(c not in '0123456789abcdef' for c in origin['sha256']):
                raise ValueError('INVALID_SOURCE_DIGEST')
        patients, records, count = set(), set(), 0
        for patient in d['patients']:
            _keys(patient, ('patient_id', 'label', 'episode_id', 'clock', 'index_us', 'index_rule', 'records'), ('bearer_id',))
            for key in ('patient_id', 'label', 'episode_id', 'clock'):
                _text(patient[key])
            pid = patient['patient_id']
            if pid in patients:
                raise ValueError('DUPLICATE_PATIENT')
            patients.add(pid)
            _time(patient['index_us'])
            if patient['index_rule'] != d['index_rule']:
                raise ValueError('INCONSISTENT_INDEX_RULE')
            if not isinstance(patient['records'], list):
                raise ValueError('INVALID_RECORDS')
            for record in patient['records']:
                count += 1
                _keys(record, ('id', 'component', 'value', 'occurrence_us', 'available_at_us', 'clock', 'source', 'process', 'role'), ('unit', 'coverage_complete'))
                _text(record['id'])
                if record['id'] in records:
                    raise ValueError('DUPLICATE_RECORD')
                records.add(record['id'])
                _time(record['occurrence_us'])
                if record['available_at_us'] is not None:
                    _time(record['available_at_us'])
                if record['clock'] != patient['clock']:
                    raise ValueError('INCOMPARABLE_PATIENT_CLOCK')
                _keys(record['source'], ('document', 'row'), ('original_value', 'original_unit', 'source_record_sha256', 'availability_policy', 'binding'))
                _text(record['source']['document']); _text(record['source']['row'])
                _keys(record['process'], ('id', 'hasParticipant'))
                _keys(record['role'], ('id', 'isFeatureOf'))
                _text(record['process']['id']); _text(record['role']['id'])
                if record['process']['hasParticipant'] != record['role']['id'] or record['role']['isFeatureOf'] != patient.get('bearer_id', pid):
                    raise ValueError('INVALID_PRO_PARTICIPATION')
                component = record['component']
                if component == 'age_band':
                    _text(record['value'])
                elif component == 'baseline_creatinine':
                    _keys(record['value'], ('hasValue',))
                    if decimal(record['value']['hasValue']) < 0 or record.get('unit') != p['creatinine_unit']:
                        raise ValueError('INCOMPATIBLE_MEASUREMENT')
                elif component == 'clinical_concepts':
                    if not isinstance(record['value'], list) or len(record['value']) > 100 or type(record.get('coverage_complete')) is not bool:
                        raise ValueError('CONCEPT_COVERAGE_REQUIRED')
                    for concept in record['value']:
                        if concept not in p['concept_ancestors']:
                            raise ValueError('UNREVIEWED_CONCEPT')
                else:
                    raise ValueError('UNSUPPORTED_COMPONENT')
                if component != 'baseline_creatinine' and 'unit' in record or component != 'clinical_concepts' and 'coverage_complete' in record:
                    raise ValueError('INAPPLICABLE_RECORD_FIELD')
        if count > MAX_RECORDS:
            raise ValueError('RECORD_BOUND')

    @staticmethod
    def _weights(weights):
        _keys(weights, COMPONENTS)
        values = {key: decimal(value) for key, value in weights.items()}
        if any(value < 0 or value > 100 for value in values.values()) or sum(values.values()) <= 0:
            raise ValueError('INVALID_WEIGHTS')
        return values

    def metadata(self):
        return {'dataset_id': self._dataset['dataset_id'], 'dataset_sha256': self._dataset_hash,
                'label': self._dataset['label'], 'profile_id': self._profile['profile_id'],
                'profile_sha256': self._profile_hash, 'clinical_validity_claim': False,
                'patients': [{key: p[key] for key in ('patient_id', 'label', 'index_us', 'episode_id')} for p in self._dataset['patients']],
                'default_reference_patient_id': self._dataset['patients'][0]['patient_id'],
                'source_dataset_sha256': self._dataset.get('construction_origin', {}).get('sha256'),
                'availability_policy': self._dataset.get('construction_origin', {}).get('availability_policy', 'Explicit per-record authored availability'),
                'components': list(COMPONENTS), 'default_weights': deepcopy(self._profile['weights']),
                'scope': 'exhaustive_authored_synthetic_pool',
                'limits': {'patients': MAX_PATIENTS, 'records': MAX_RECORDS, 'revisions': MAX_REVISIONS}}

    def _extract(self, patient):
        index, eligible, ignored = patient['index_us'], [], []
        for record in patient['records']:
            reasons = []
            if record['occurrence_us'] >= index:
                reasons.append('AT_OR_AFTER_INDEX')
            if record['occurrence_us'] < index - self._profile['history_us']:
                reasons.append('OUTSIDE_HISTORY')
            if record['available_at_us'] is None:
                reasons.append('UNKNOWN_AVAILABILITY')
            elif record['available_at_us'] > index:
                reasons.append('AVAILABLE_AFTER_INDEX')
            if record['component'] == 'baseline_creatinine' and record['occurrence_us'] < index - self._profile['creatinine_window_us']:
                reasons.append('OUTSIDE_CREATININE_WINDOW')
            if reasons:
                ignored.append({'record_id': record['id'], 'reasons': reasons, 'source': deepcopy(record['source'])})
            else:
                eligible.append(record)
        result = {'features': {}, 'ignored': ignored, 'index_us': index, 'clock': patient['clock'], 'episode_id': patient['episode_id']}
        for component in COMPONENTS:
            records = [r for r in eligible if r['component'] == component]
            selected, value = [], None
            if records and component == 'age_band':
                latest = max(r['occurrence_us'] for r in records)
                selected = [r for r in records if r['occurrence_us'] == latest]
                # Conflicting equally recent declarations are unresolved, not tie-broken.
                values = {r['value'] for r in selected}
                if len(values) == 1:
                    value = next(iter(values))
            elif records and component == 'baseline_creatinine':
                minimum = min(decimal(r['value']['hasValue']) for r in records)
                selected = [r for r in records if decimal(r['value']['hasValue']) == minimum]
                value = lexical(minimum)
            elif records and component == 'clinical_concepts':
                selected = records
                concepts = {c for r in records for c in r['value']}
                if concepts or any(r['coverage_complete'] for r in records):
                    value = sorted(concepts | {a for c in concepts for a in self._profile['concept_ancestors'][c]})
            complete = component != 'clinical_concepts' or any(r['coverage_complete'] for r in records)
            observed = value is not None and complete
            result['features'][component] = {'value': value, 'observed': observed, 'coverage_complete': complete,
                'evidence': deepcopy(sorted(selected, key=lambda r: r['id'])),
                'missing_reason': None if observed else ('INCOMPLETE_CONCEPT_SET' if value is not None and not complete else 'CONFLICTING_AGE_BANDS' if selected and component == 'age_band' else 'NO_ELIGIBLE_OBSERVED_FEATURE')}
        return result

    def _score(self, reference, candidate, weights):
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            return self._score_fixed(reference, candidate, weights)

    def _score_fixed(self, reference, candidate, weights):
        weights = {key: Fraction(value) for key, value in weights.items()}
        total, observed, numerator, components = sum(weights.values()), Fraction(0), Fraction(0), []
        def display(value):
            return lexical(Decimal(value.numerator) / Decimal(value.denominator))
        for component in COMPONENTS:
            a, b = reference['features'][component], candidate['features'][component]
            distance = None
            if a['observed'] and b['observed']:
                if component == 'age_band':
                    distance = Fraction(a['value'] != b['value'])
                elif component == 'baseline_creatinine':
                    distance = min(Fraction(1), abs(Fraction(decimal(a['value']))-Fraction(decimal(b['value']))) / Fraction(decimal(self._profile['creatinine_scale'])))
                else:
                    aa, bb = set(a['value']), set(b['value'])
                    distance = Fraction(1) - Fraction(len(aa & bb), len(aa | bb)) if aa | bb else Fraction(0)
                observed += weights[component]
                numerator += weights[component] * distance
            components.append({'component': component, 'weight': display(weights[component]),
                'distance': None if distance is None else display(distance),
                'reference_value': deepcopy(a['value']), 'candidate_value': deepcopy(b['value']),
                'reference_record_ids': [r['id'] for r in a['evidence']],
                'candidate_record_ids': [r['id'] for r in b['evidence']],
                'missing': distance is None})
        coverage = observed / total
        # Missing mass has distance one. Exact fractions determine ordering;
        # decimal strings are only the fixed-precision display representation.
        ranking = (total-observed+numerator)/total
        return {'ranking_distance': display(ranking) if observed else None,
                'ranking_fraction': [str(ranking.numerator), str(ranking.denominator)] if observed else None,
                'observed_distance': display(numerator/observed) if observed else None,
                'coverage': display(coverage), 'components': components,
                'rankable': bool(observed and coverage >= Fraction(decimal(self._profile['minimum_coverage'])))}

    def _predicate(self, predicate):
        _keys(predicate, ('component', 'operator', 'value'))
        component, operator, value = predicate['component'], predicate['operator'], predicate['value']
        if component == 'age_band' and operator == 'eq':
            _text(value)
        elif component == 'clinical_concepts' and operator == 'contains':
            _text(value)
            allowed = set(self._profile['concept_ancestors']) | {x for v in self._profile['concept_ancestors'].values() for x in v}
            if value not in allowed:
                raise ValueError('UNREVIEWED_FILTER_CONCEPT')
        elif component == 'baseline_creatinine' and operator == 'between':
            _keys(value, ('min', 'max'))
            if not 0 <= decimal(value['min']) <= decimal(value['max']):
                raise ValueError('INVALID_FILTER_RANGE')
        else:
            raise ValueError('UNSUPPORTED_HARD_FILTER')

    def _filter(self, candidate, predicates):
        failures, unknown = [], []
        for predicate in predicates:
            feature = candidate['features'][predicate['component']]
            if predicate['operator'] == 'contains' and feature['value'] is not None and predicate['value'] in feature['value']:
                continue  # A positive witness is valid even when the complete concept set is unknown.
            if not feature['observed']:
                unknown.append({'predicate': deepcopy(predicate), 'reason': 'MISSING_ELIGIBLE_FEATURE'})
                continue
            actual, expected = feature['value'], predicate['value']
            operator = predicate['operator']
            passes = actual == expected if operator == 'eq' else expected in actual if operator == 'contains' else decimal(expected['min']) <= decimal(actual) <= decimal(expected['max'])
            if not passes:
                failures.append({'predicate': deepcopy(predicate), 'reason': 'PREDICATE_FALSE', 'actual': deepcopy(actual)})
        return ('excluded', failures) if failures else ('unresolved', unknown) if unknown else ('eligible', [])

    def initial(self, patient_id, top_k=5):
        if patient_id not in self._patients:
            raise ValueError('UNKNOWN_PATIENT')
        if type(top_k) is not int or not 1 <= top_k <= MAX_PATIENTS:
            raise ValueError('INVALID_TOP_K')
        query = {'weights': deepcopy(self._profile['weights']), 'hard_filters': [], 'top_k': top_k,
                 'base_pool': sorted(pid for pid in self._patients if pid != patient_id),
                 'history_us': self._profile['history_us'], 'index_rule': self._dataset['index_rule']}
        return self._evaluate(patient_id, query, None, {'type': 'initial'})

    def refine(self, parent_revision_id, operation):
        parent = self.get_revision(parent_revision_id)
        operation = deepcopy(operation)
        query = deepcopy(parent['query'])
        if not isinstance(operation, dict):
            raise ValueError('INVALID_OPERATION')
        if operation.get('type') == 'set_weights':
            _keys(operation, ('type', 'weights'))
            self._weights(operation['weights'])
            query['weights'] = operation['weights']
        elif operation.get('type') == 'add_filter':
            _keys(operation, ('type', 'predicate'))
            self._predicate(operation['predicate'])
            if len(query['hard_filters']) >= 16:
                raise ValueError('HARD_FILTER_LIMIT')
            if operation['predicate'] in query['hard_filters']:
                raise ValueError('DUPLICATE_HARD_FILTER')
            query['hard_filters'].append(operation['predicate'])
        else:
            raise ValueError('UNSUPPORTED_OPERATION')
        return self._evaluate(parent['reference_patient_id'], query, parent, operation)

    def _evaluate(self, reference_id, query, parent, operation):
        weights = self._weights(query['weights'])
        ranked, eligible, excluded, unresolved, unrankable = [], [], [], [], []
        for pid in query['base_pool']:
            candidate = self._features[pid]
            state, reasons = self._filter(candidate, query['hard_filters'])
            if state != 'eligible':
                (excluded if state == 'excluded' else unresolved).append({'patient_id': pid, 'reasons': reasons})
                continue
            eligible.append(pid)
            score = {'patient_id': pid, **self._score(self._features[reference_id], candidate, weights)}
            if score['rankable']:
                ranked.append(score)
            else:
                unrankable.append({**score, 'reason': 'NO_OBSERVED_COMPONENTS' if score['ranking_distance'] is None else 'BELOW_MINIMUM_COVERAGE'})
        ranked.sort(key=lambda row: (Fraction(*(int(x) for x in row['ranking_fraction'])), row['patient_id']))
        for rank, row in enumerate(ranked, 1):
            row['rank'] = rank
        old = set(parent['results']['eligible_patient_ids']) if parent else set()
        current = set(eligible)
        results = {'ranked': ranked, 'displayed': ranked[:query['top_k']], 'eligible_patient_ids': eligible,
                   'excluded': excluded, 'unresolved': unresolved, 'unrankable': unrankable,
                   'base_pool_count': len(query['base_pool']), 'search_complete': True}
        reasons = {row['patient_id']: row['reasons'] for row in excluded+unresolved}
        revision = {'profile': 'patient-similarity-revision-1.0', 'parent_revision_id': parent['revision_id'] if parent else None,
            'dataset_id': self._dataset['dataset_id'], 'dataset_sha256': self._dataset_hash,
            'profile_id': self._profile['profile_id'], 'profile_sha256': self._profile_hash,
            'implementation_sha256': self._implementation_hash, 'reference_patient_id': reference_id,
            'reference_episode_id': self._patients[reference_id]['episode_id'],
            'query': deepcopy(query), 'query_sha256': digest(query), 'operation': deepcopy(operation),
            'results': results, 'result_sha256': digest(results),
            'changes': {'added': sorted(current-old), 'removed': sorted(old-current),
                        'unresolved': [row['patient_id'] for row in unresolved], 'reasons': reasons,
                        'membership_basis': 'hard_eligibility_over_full_base_pool',
                        'displayed_added': sorted({x['patient_id'] for x in results['displayed']} - ({x['patient_id'] for x in parent['results']['displayed']} if parent else set())),
                        'displayed_removed': sorted(({x['patient_id'] for x in parent['results']['displayed']} if parent else set()) - {x['patient_id'] for x in results['displayed']})},
            'clinical_validity_claim': False, 'outcomes_used_for_matching': False,
            'outcome_window': None, 'outcomes_status': 'not_implemented',
            'scope': 'bounded_authored_synthetic_exhaustive_ranking'}
        revision['revision_id'] = digest(revision)
        if revision['revision_id'] not in self._revisions and len(self._revisions) >= MAX_REVISIONS:
            raise ValueError('REVISION_LIMIT')
        self._revisions[revision['revision_id']] = deepcopy(revision)
        return deepcopy(revision)

    def get_revision(self, revision_id):
        if not isinstance(revision_id, str) or revision_id not in self._revisions:
            raise ValueError('UNKNOWN_REVISION')
        return deepcopy(self._revisions[revision_id])

    def inspect(self, revision_id, candidate_id):
        revision = self.get_revision(revision_id)
        if candidate_id not in revision['query']['base_pool']:
            raise ValueError('PATIENT_OUTSIDE_CANDIDATE_POOL')
        candidate, reference = self._features[candidate_id], self._features[revision['reference_patient_id']]
        state, reasons = self._filter(candidate, revision['query']['hard_filters'])
        return {'revision_id': revision_id, 'patient_id': candidate_id,
                'reference_patient_id': revision['reference_patient_id'], 'eligibility': state, 'reasons': reasons,
                'candidate': deepcopy(candidate), 'reference': deepcopy(reference),
                'comparison': self._score(reference, candidate, self._weights(revision['query']['weights'])),
                'dataset_sha256': self._dataset_hash, 'profile_sha256': self._profile_hash,
                'clinical_validity_claim': False}

    def manifest(self, revision_id):
        chain, revision = [], self.get_revision(revision_id)
        while True:
            chain.append(revision)
            if revision['parent_revision_id'] is None:
                break
            revision = self.get_revision(revision['parent_revision_id'])
        return {'profile': 'patient-similarity-replay-1.0', 'dataset': deepcopy(self._dataset),
                'similarity_profile': deepcopy(self._profile), 'dataset_sha256': self._dataset_hash,
                'profile_sha256': self._profile_hash, 'implementation_sha256': self._implementation_hash,
                'revisions': list(reversed(chain)), 'clinical_validity_claim': False}

    @classmethod
    def replay(cls, manifest):
        _keys(manifest, ('profile', 'dataset', 'similarity_profile', 'dataset_sha256', 'profile_sha256', 'implementation_sha256', 'revisions', 'clinical_validity_claim'))
        if manifest['profile'] != 'patient-similarity-replay-1.0' or manifest['clinical_validity_claim'] is not False:
            raise ValueError('INVALID_REPLAY_PROFILE')
        engine = cls(manifest['dataset'], manifest['similarity_profile'])
        for key, expected in (('dataset_sha256', engine._dataset_hash), ('profile_sha256', engine._profile_hash), ('implementation_sha256', engine._implementation_hash)):
            if manifest[key] != expected:
                raise ValueError('REPLAY_CONTEXT_MISMATCH')
        if not isinstance(manifest['revisions'], list) or not 1 <= len(manifest['revisions']) <= MAX_REVISIONS:
            raise ValueError('INVALID_REPLAY_CHAIN')
        previous = None
        for saved in manifest['revisions']:
            if saved['parent_revision_id'] != previous:
                raise ValueError('INVALID_REPLAY_PARENT')
            actual = engine.initial(saved['reference_patient_id'], saved['query']['top_k']) if previous is None else engine.refine(previous, saved['operation'])
            if actual != saved:
                raise ValueError('REPLAY_RESULT_MISMATCH')
            previous = actual['revision_id']
        return engine
