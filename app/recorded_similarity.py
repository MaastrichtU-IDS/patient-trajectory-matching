"""Source-backed pre-index profiles for recorded query-by-example.

This is a descriptive demonstration profile, not a clinically validated similarity
measure. Charted time is used; availability at the treatment start is not asserted.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext

from app import feature_profiles

PROFILE = 'recorded-preindex-pressure-distance-1.0'
SCHEMA = 'recorded-query-by-example-1'
EVIDENCE_FIELDS = ('event_id', 'claim_id', 'claim_sha256', 'kind', 'value', 'unit',
                   'item_id', 'label', 'time', 'source', 'clock', 'decisions')


def _scope_profile(snapshot):
    baseline = snapshot['query_context']['query']['baseline']
    if len(baseline['item_ids']) != 1 or baseline['unit_lexical'] != 'mmHg':
        raise ValueError('Recorded similarity requires one reviewed pressure item in mmHg')
    return {
        'id': PROFILE,
        'label': 'Latest pre-index recorded pressure',
        'item_id': baseline['item_ids'][0],
        'unit': baseline['unit_lexical'],
        'baseline_window_us': baseline['max_before_start_us'],
        **({'baseline_min_before_start_us': baseline['min_before_start_us']}
           if baseline.get('min_before_start_us', 1) > 1 else {}),
        'selection': 'Latest finite same-item/unit measurement strictly before the treatment start '
                     'within the baseline window; equal times use ascending event ID.',
        'distance': 'Exact absolute difference in mmHg; no normalization or imputation.',
        'patient_selection': 'Closest feature-bearing anchor per patient; ties use patient, episode, then anchor token.',
        'reference_exclusion': 'All stays and anchors of the reference patient are excluded.',
        'time_basis': 'Charted measurement time only; availability at index is not established.',
        'excluded_inputs': ['pressure threshold', 'temporal match status', 'follow-up measurements'],
        'clinical_validation': 'Unvalidated single-feature demonstration; not a clinical similarity score.',
        'top_k_scope': 'Highlights ranked peers only; the full temporal result is preserved.',
    }


# Public legacy metadata helper retained for existing callers.
feature_profile = _scope_profile


def _feature(detail, profile):
    start = datetime.fromisoformat(detail['anchor']['start'])
    lower = start - timedelta(microseconds=profile['baseline_window_us'])
    upper = start - timedelta(microseconds=profile.get('baseline_min_before_start_us', 1))
    candidates = []
    for measurement in detail['measurements']:
        if measurement.get('item_id') != profile['item_id'] or measurement.get('unit') != profile['unit']:
            continue
        try:
            value = Decimal(measurement['value'])
            timestamp = datetime.fromisoformat(measurement['time'])
            if not value.is_finite() or not lower <= timestamp <= upper:
                continue
        except (InvalidOperation, TypeError, ValueError, KeyError):
            continue
        candidates.append((timestamp, measurement['event_id'], measurement))
    if not candidates:
        return None
    # Stable ascending event ID breaks a tie at the latest charted timestamp.
    latest = max(entry[0] for entry in candidates)
    chosen = min((entry for entry in candidates if entry[0] == latest), key=lambda entry: entry[1])[2]
    return {key: deepcopy(chosen[key]) for key in EVIDENCE_FIELDS if key in chosen}


def references(snapshot, feature_profile=None):
    profile = _scope_profile(snapshot)
    if feature_profile is not None:
        profile = feature_profiles.compile_profile(feature_profile, profile)
    anchors = []
    for detail in snapshot['details']:
        anchor = {key: deepcopy(detail['anchor'][key]) for key in
                  ('token', 'patient_id', 'episode_id', 'start', 'end')}
        anchor['feature'] = _feature(detail, profile)
        anchor['feature_status'] = 'AVAILABLE' if anchor['feature'] is not None else 'MISSING_PREINDEX_FEATURE'
        if feature_profile is not None:
            anchor.update(feature_profiles.extract(detail, profile))
            anchor['feature_status'] = 'AVAILABLE' if anchor['coverage']['complete'] else 'MISSING_PREINDEX_FEATURE'
        anchor['temporal_status'] = detail['anchor']['status']
        anchors.append(anchor)
    anchors.sort(key=lambda anchor: (anchor['patient_id'], anchor['episode_id'], anchor['token']))
    return {'schema': 'recorded-reference-options-1', 'profile': snapshot['profile'],
            **({'job_id': snapshot['job_id']} if 'job_id' in snapshot else {}),
            'feature_profile': profile, 'anchors': anchors,
            'roster': deepcopy(snapshot['temporal_result']['roster']),
            'source_context': deepcopy(snapshot['source_context']),
            'query_context': deepcopy(snapshot['query_context'])}


def _distance(first, second):
    first, second = Decimal(first), Decimal(second)
    with localcontext() as context:
        context.prec = max(first.adjusted(), second.adjusted()) - min(
            first.as_tuple().exponent, second.as_tuple().exponent) + 3
        return abs(first - second)


def _decimal_string(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def compare(snapshot, reference_token, top_k, feature_profile=None):
    if not isinstance(reference_token, str):
        raise ValueError('Invalid reference anchor token')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError('top_k must be an integer from 1 to 20')
    options = references(snapshot, feature_profile)
    reference = next((anchor for anchor in options['anchors'] if anchor['token'] == reference_token), None)
    if reference is None:
        raise ValueError('Reference anchor is not in this completed recorded query')
    if reference['feature_status'] != 'AVAILABLE':
        raise ValueError('Reference anchor has no finite same-item/unit pre-index feature for every requested feature')
    patient = reference['patient_id']
    roster = options['roster']
    eligible = sorted({row['patient_id'] for row in roster} - {patient})
    ranked, unresolved = [], []
    for candidate in eligible:
        anchors = [anchor for anchor in options['anchors'] if anchor['patient_id'] == candidate]
        available = [anchor for anchor in anchors if anchor['feature_status'] == 'AVAILABLE']
        if not available:
            unresolved.append({'patient_id': candidate,
                               'reason': 'MISSING_PREINDEX_FEATURE' if anchors else 'NO_ADMITTED_ANCHOR',
                               'episode_ids': sorted({row['episode_id'] for row in roster if row['patient_id'] == candidate})})
            if feature_profile is not None:
                unresolved[-1]['feature_coverage'] = [
                    {'token': anchor['token'], 'episode_id': anchor['episode_id'],
                     'coverage': deepcopy(anchor['coverage']),
                     'missing_feature_ids': list(anchor['missing_feature_ids']),
                     'features': deepcopy(anchor['features'])} for anchor in anchors]
            continue
        if feature_profile is None:
            choices = [(_distance(reference['feature']['value'], anchor['feature']['value']), anchor)
                       for anchor in available]
        else:
            choices = [(feature_profiles.distance(reference, anchor, options['feature_profile'])[0], anchor)
                       for anchor in available]
        distance, selected = min(choices, key=lambda choice:
                                 (choice[0], choice[1]['patient_id'], choice[1]['episode_id'], choice[1]['token']))
        ranked.append({'patient_id': candidate, 'distance': feature_profiles.display(distance) if feature_profile is not None else _decimal_string(distance),
                       'unit': 'normalized' if feature_profile is not None else options['feature_profile']['unit'], 'selected_anchor': selected,
                       'temporal_status': 'MATCH' if any(row['status'] == 'MATCH' for row in roster
                                                        if row['patient_id'] == candidate)
                                          else 'NO_SELECTED_MATCH'})
        if feature_profile is not None:
            ranked[-1]['distance_exact'] = feature_profiles.exact(distance)
            ranked[-1]['feature_contributions'] = feature_profiles.distance(reference, selected, options['feature_profile'])[1]
    ranked.sort(key=lambda row: (feature_profiles.restore(row['distance_exact']) if feature_profile is not None else Decimal(row['distance']), row['patient_id'],
                                 row['selected_anchor']['episode_id'], row['selected_anchor']['token']))
    for index, row in enumerate(ranked, 1):
        row.update(rank=index, highlighted=index <= top_k)
    return {'schema': SCHEMA, 'profile': snapshot['profile'],
            **({'job_id': snapshot['job_id']} if 'job_id' in snapshot else {}),
            'feature_profile': options['feature_profile'],
            'source_context': deepcopy(snapshot['source_context']),
            'query_context': deepcopy(snapshot['query_context']),
            'reference': reference, 'ranked_patients': ranked,
            'unresolved_patients': unresolved, 'eligible_patient_ids': eligible,
            'top_k': top_k, 'temporal_result': deepcopy(snapshot['temporal_result'])}


# Replay uses the same pure computation after removing the ephemeral job ID.
compare_snapshot = compare
