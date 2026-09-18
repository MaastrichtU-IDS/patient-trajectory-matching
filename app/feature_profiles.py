"""Explicit, versioned pre-index feature profiles over one reviewed measurement.

Fractions determine rankings exactly. Decimal display values use twelve places,
round-half-even; they do not determine tie order. These are descriptive features,
not a clinically validated similarity measure.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import re

SCHEMA = 'recorded-similarity-features-1'
VERSION = 'recorded-preindex-multifeature-1.0'
DISPLAY_PLACES = 12
NUMBER = re.compile(r'(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?\Z')
CATALOGUE = (
    {'id': 'latest_value', 'label': 'Latest pre-index pressure', 'unit': 'mmHg',
     'description': 'Latest finite same-item/unit measurement before index.'},
    {'id': 'value_change', 'label': 'Pre-index pressure change', 'unit': 'mmHg',
     'description': 'Latest minus earliest value; requires at least two distinct charted times.'},
    {'id': 'measurement_count', 'label': 'Pre-index measurement count', 'unit': 'count',
     'description': 'Count of finite same-item/unit measurement events in the baseline window.'},
    {'id': 'latest_recency', 'label': 'Latest measurement recency', 'unit': 'minutes',
     'description': 'Minutes between the latest charted measurement and treatment start.'},
)
EVIDENCE_FIELDS = ('event_id', 'claim_id', 'claim_sha256', 'kind', 'value', 'unit',
                   'item_id', 'label', 'time', 'source', 'clock', 'decisions')


def metadata():
    return {'schema': SCHEMA, 'version': VERSION, 'features': deepcopy(list(CATALOGUE)),
            'default': {'schema': SCHEMA, 'features': [{'id': 'latest_value', 'weight': '1', 'scale': '1'}]},
            'max_features': 4, 'weight_and_scale': {'minimum_exclusive': '0', 'maximum': '1000000',
                                                  'maximum_fraction_digits': 6},
            'missing_policy': 'All requested features are required; no imputation.',
            'distance': 'sum(weight * abs(candidate - reference) / scale) / sum(weight)',
            'display_decimal_places': DISPLAY_PLACES, 'display_rounding': 'half-even',
            'ranking_arithmetic': 'Exact rational arithmetic; display rounding does not create ties.'}


def _number(value):
    if not isinstance(value, str) or NUMBER.fullmatch(value) is None:
        raise ValueError('Feature weight and scale must be positive decimal strings with at most six fractional digits')
    decimal = Decimal(value)
    if not 0 < decimal <= 1000000:
        raise ValueError('Feature weight and scale must be greater than zero and at most 1000000')
    return format(decimal, 'f').rstrip('0').rstrip('.') if '.' in value else value


def compile_profile(value, scope):
    if not isinstance(value, dict) or set(value) != {'schema', 'features'} or value['schema'] != SCHEMA:
        raise ValueError('Invalid recorded feature profile schema or fields')
    features = value['features']
    if not isinstance(features, list) or not 1 <= len(features) <= len(CATALOGUE):
        raise ValueError('Feature profile requires one to four features')
    allowed = {entry['id'] for entry in CATALOGUE}
    compiled = {}
    for feature in features:
        if not isinstance(feature, dict) or set(feature) != {'id', 'weight', 'scale'}:
            raise ValueError('Each feature requires only id, weight and scale')
        identity = feature['id']
        if not isinstance(identity, str) or identity not in allowed or identity in compiled:
            raise ValueError('Feature IDs must be supported and unique')
        compiled[identity] = {'id': identity, 'weight': _number(feature['weight']), 'scale': _number(feature['scale'])}
    definition = {'schema': SCHEMA, 'features': [compiled[entry['id']] for entry in CATALOGUE if entry['id'] in compiled]}
    binding = {'version': VERSION, 'definition': definition,
               'item_id': scope['item_id'], 'unit': scope['unit'], 'baseline_window_us': scope['baseline_window_us'],
               'baseline_min_before_start_us': scope.get('baseline_min_before_start_us', 1)}
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return {**deepcopy(scope), **binding, 'id': VERSION, 'sha256': digest,
            'label': 'Explicit pre-index measurement feature profile',
            'selection': 'Finite same-item/unit events in the baseline window strictly before treatment start; '
                         'earliest/latest timestamp ties use ascending event ID.',
            'excluded_inputs': ['pressure threshold', 'temporal match status', 'follow-up measurements', 'treatment end'],
            'distance': metadata()['distance'], 'missing_policy': metadata()['missing_policy'],
            'ranking_arithmetic': metadata()['ranking_arithmetic'],
            'display_decimal_places': DISPLAY_PLACES, 'display_rounding': 'half-even',
            'clinical_validation': 'Unvalidated descriptive measurement profile; not a clinical similarity score.'}


def exact(value):
    fraction = Fraction(value)
    return {'numerator': str(fraction.numerator), 'denominator': str(fraction.denominator)}


def restore(value):
    return Fraction(int(value['numerator']), int(value['denominator']))


def display(value):
    """Round a rational without depending on the process Decimal context."""
    value = Fraction(value)
    sign = '-' if value < 0 else ''
    numerator = abs(value.numerator) * 10 ** DISPLAY_PLACES
    integer, remainder = divmod(numerator, value.denominator)
    if remainder * 2 > value.denominator or (remainder * 2 == value.denominator and integer % 2):
        integer += 1
    whole, fractional = divmod(integer, 10 ** DISPLAY_PLACES)
    result = str(whole) + ('.' + str(fractional).zfill(DISPLAY_PLACES).rstrip('0') if fractional else '')
    return sign + result if integer else '0'


def _candidates(detail, scope):
    start = datetime.fromisoformat(detail['anchor']['start'])
    lower = start - timedelta(microseconds=scope['baseline_window_us'])
    upper = start - timedelta(microseconds=scope.get('baseline_min_before_start_us', 1))
    candidates = []
    for measurement in detail['measurements']:
        if measurement.get('item_id') != scope['item_id'] or measurement.get('unit') != scope['unit']:
            continue
        try:
            value = Decimal(measurement['value'])
            timestamp = datetime.fromisoformat(measurement['time'])
            if not value.is_finite() or not lower <= timestamp <= upper:
                continue
        except (InvalidOperation, TypeError, ValueError, KeyError):
            continue
        candidates.append((timestamp, measurement['event_id'], measurement))
    return sorted(candidates, key=lambda entry: (entry[0], entry[1]))


def extract(detail, profile):
    candidates = _candidates(detail, profile)
    # One event is one observation; duplicate serialization must not inflate count.
    candidates = list({entry[1]: entry for entry in reversed(candidates)}.values())
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    chosen = None
    if candidates:
        latest_time = candidates[-1][0]
        chosen = next(entry for entry in candidates if entry[0] == latest_time)
    definitions = {entry['id']: entry for entry in CATALOGUE}
    result = []
    for feature in profile['definition']['features']:
        identity = feature['id']
        evidence, value, reason = [], None, 'NO_PREINDEX_MEASUREMENTS'
        if chosen is not None:
            evidence = [chosen[2]]
            if identity == 'latest_value':
                value = Fraction(Decimal(chosen[2]['value']))
            elif identity == 'measurement_count':
                value, evidence = Fraction(len(candidates)), [entry[2] for entry in candidates]
            elif identity == 'latest_recency':
                delta = datetime.fromisoformat(detail['anchor']['start']) - chosen[0]
                value = Fraction((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds, 60000000)
            elif identity == 'value_change':
                earliest = candidates[0]
                if earliest[0] == chosen[0]:
                    reason = 'NEEDS_TWO_DISTINCT_PREINDEX_TIMES'
                else:
                    value = Fraction(Decimal(chosen[2]['value'])) - Fraction(Decimal(earliest[2]['value']))
                    evidence = [earliest[2], chosen[2]]
        row = {key: definitions[identity][key] for key in ('id', 'label', 'unit')}
        row.update(value=display(value) if value is not None else None,
                   value_exact=exact(value) if value is not None else None,
                   status='AVAILABLE' if value is not None else 'MISSING',
                   evidence=[{key: deepcopy(record[key]) for key in EVIDENCE_FIELDS if key in record} for record in evidence])
        if value is None:
            row['reason'] = reason
        result.append(row)
    missing = [row['id'] for row in result if row['status'] != 'AVAILABLE']
    return {'features': result, 'missing_feature_ids': missing,
            'coverage': {'available': len(result) - len(missing), 'requested': len(result), 'complete': not missing}}


def distance(reference, candidate, profile):
    ref = {entry['id']: entry for entry in reference['features']}
    peer = {entry['id']: entry for entry in candidate['features']}
    total_weight = sum(Fraction(Decimal(feature['weight'])) for feature in profile['definition']['features'])
    total, contributions = Fraction(0), []
    for feature in profile['definition']['features']:
        identity = feature['id']
        difference = abs(restore(ref[identity]['value_exact']) - restore(peer[identity]['value_exact']))
        contribution = Fraction(Decimal(feature['weight'])) * difference / Fraction(Decimal(feature['scale'])) / total_weight
        total += contribution
        contributions.append({'feature_id': identity, 'label': ref[identity]['label'], 'unit': ref[identity]['unit'],
                              'reference_value': ref[identity]['value'], 'candidate_value': peer[identity]['value'],
                              'weight': feature['weight'], 'scale': feature['scale'],
                              'absolute_difference': display(difference), 'absolute_difference_exact': exact(difference),
                              'contribution': display(contribution), 'contribution_exact': exact(contribution)})
    return total, contributions
