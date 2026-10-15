"""Hand staged MIMIC input segments to the point-anchor matcher, or refuse by name.

`mimic_inputevents` stages rows and stops, reporting `STAGED_NOT_MATCHER_READY` with
five named blockers. Nothing has ever carried those segments the rest of the way:
`pro_solid.build_graph` is the only route to the matcher and it reads constructed
synthetic JSON, so no source row has ever reached a cohort answer. That is S1.

This profile closes the mechanism and leaves the judgements where they belong. Each of
the five blockers is discharged by one explicitly declared field, or it is reported
outstanding together with the external dependency that would supply it. Two are
dischargeable here, because the repository already owns what they need: the
patient-local clock bridge exists, and an occurrence policy is a statement someone
writes down. The other three require a clinical reviewer or the source owner, and no
declaration written in this repository can stand in for them.

A review may declare itself `synthetic`. The path then executes end to end so that it
can be tested, and every artifact it produces carries `review_basis: synthetic` and
`clinical_validity_claim: false`. This mirrors the `stipulated` basis in
`reference_oracle.uncovered_demands`: the mechanism stays honest by naming what decided
it, never by pretending the decision was not made.
"""
from datetime import datetime
from pathlib import Path
import argparse
import json
import sys

from . import mimic_inputevents as staging

PROFILE = 'mimic-matcher-handoff-1.0'
ROOT = Path(__file__).resolve().parents[1]
US = 1000000

# Each blocker, the declaration field that discharges it, and who supplies that field.
# `dependency` is None where the repository can discharge it on its own; the completion
# register's dependency IDs are used verbatim so the two accounts cannot drift.
BLOCKERS = {
    'PATIENT_LOCAL_CLOCK_BRIDGE_REQUIRED': ('clock_bridge', None),
    'OCCURRENCE_PRECISION_POLICY_REQUIRED': ('occurrence_policy', None),
    'CLINICAL_MAPPING_REVIEW_REQUIRED': ('clinical_review', 'D-CLINICAL'),
    'SOURCE_AVAILABILITY_UNVERIFIED': ('source_availability', 'D-HISTORY'),
    'SOURCE_REVISION_HISTORY_UNAVAILABLE': ('revision_history', 'D-HISTORY'),
}
CLOCK_POLICY = 'patient-local-calendar-microseconds-v1'
OCCURRENCE_POLICY = 'recorded-endpoints-as-occurrence-bounds-v1'


class HandoffError(ValueError):
    """The declaration or staging bundle is unusable; no handoff claim is made."""


def require(condition, code):
    if not condition:
        raise HandoffError(code)


def outstanding(declaration):
    """Which blockers this declaration leaves standing, with what would discharge them.

    A field discharges its blocker only by being present and well formed. Absent, null,
    or malformed are one case deliberately: a declaration that carries
    `"clinical_review": {}` has not reviewed anything, and treating that as discharge is
    exactly the failure mode S2 found in `source_search_complete`.
    """
    remaining = []
    for blocker, (field, dependency) in sorted(BLOCKERS.items()):
        value = declaration.get(field)
        if not isinstance(value, dict) or not value:
            remaining.append({'blocker': blocker, 'needs': field, 'dependency': dependency})
    return remaining


def check_dischargeable(declaration):
    """Validate the two fields this repository can legitimately supply.

    These are checked rather than trusted because they are the two the project can get
    wrong on its own. The other three are not checked here at all: their content is a
    reviewer's statement, and inventing a schema for it would suggest this profile can
    tell a sound clinical review from an unsound one, which it cannot.
    """
    bridge = declaration.get('clock_bridge')
    if isinstance(bridge, dict) and bridge:
        require(bridge.get('policy') == CLOCK_POLICY, 'UNSUPPORTED_CLOCK_POLICY')
        for field in ('clock_id', 'origin'):
            require(bool(str(bridge.get(field, '')).strip()), 'CLOCK_' + field.upper() + '_REQUIRED')
        # Parsed here so a malformed origin fails the declaration, not the projection.
        datetime.fromisoformat(bridge['origin'])
    policy = declaration.get('occurrence_policy')
    if isinstance(policy, dict) and policy:
        require(policy.get('policy') == OCCURRENCE_POLICY, 'UNSUPPORTED_OCCURRENCE_POLICY')
        # An occurrence policy that cites nothing is a preference, not a justification.
        require(len(str(policy.get('justification', '')).strip()) >= 16,
                'OCCURRENCE_POLICY_JUSTIFICATION_REQUIRED')


def review_basis(declaration):
    """`synthetic` when the review says so, `declared` otherwise; never inferred."""
    review = declaration.get('clinical_review') or {}
    return 'synthetic' if review.get('synthetic') is True else 'declared'


def offset_us(local_datetime, origin):
    """Signed microseconds from the declared origin, on the patient-local calendar.

    The bridge's own contract: an origin is a fixed coordinate reference, never an
    inferred first-ever clinical event, and no timezone is assumed. Both values are
    naive local datetimes from the same shifted patient calendar, so the difference is
    a calendar offset and not a claim about physical elapsed time.
    """
    return round((datetime.fromisoformat(local_datetime)
                  - datetime.fromisoformat(origin)).total_seconds() * US)


def searched_span(result, origin):
    """The offsets spanned by every row staging reconciled, not only the staged ones.

    An unsupported or invalid row was still looked at. Taking the span from the whole
    reconciliation is what makes this a measurement of the search rather than a
    description of its output -- a window drawn around the staged segments alone would
    narrow every time a row was rejected, which is backwards.
    """
    bounds = []
    for entry in result['reconciliation']:
        raw = entry.get('raw') or {}
        for field in ('starttime', 'endtime'):
            value = (raw.get(field) or '').strip().replace(' ', 'T')
            if value:
                bounds.append(offset_us(value, origin))
    require(bool(bounds), 'NO_RECONCILED_ROW_CARRIES_A_RECORDED_TIME')
    return min(bounds), max(bounds)


def rows_for(result, declaration):
    """Project staged segments into the row shape `pro_solid.build_graph` admits.

    Only `STAGED_SEGMENT` rows are carried. An unsupported, invalid, or duplicate row is
    not quietly dropped: staging has already given each one an outcome, and the coverage
    warrant is derived from that complete reconciliation, so what was excluded stays
    visible rather than becoming an unexplained absence.
    """
    concepts = declaration['item_concepts']
    rows, skipped = [], []
    for segment in result['segments']:
        item = str(segment['item']['itemid'])
        if item not in concepts:
            skipped.append({'segment_id': segment['id'], 'itemid': item,
                            'reason': 'ITEM_NOT_IN_REVIEWED_MAPPING'})
            continue
        suffix = segment['id'].rsplit(':', 1)[1]
        rows.append({
            'patient_id': segment['patient_id'], 'episode_id': segment['icu_stay_id'],
            'event_id': 'seg-' + suffix, 'record_id': 'source-' + suffix,
            'event_kind': 'administration', 'source_code': concepts[item],
            # A recorded component segment is admitted as `performed` only under the
            # declared occurrence policy. Staging itself never makes this claim.
            'status': 'performed',
            'datetime': segment['start']['local_datetime'] + 'Z',
            'value': None, 'unit': None})
    return rows, skipped


def coverage_warrant(result, declaration, origin):
    """The S2 warrant, derived from staging rather than declared by the caller.

    S2 left one thing open: declaring a window does not establish that the search of
    that window was exhaustive. For this source it can be established, because staging
    gives every input row an outcome and says so in `reconciliation_complete`. The
    warrant is therefore computed, and refuses to exist if that flag is false -- the one
    case where the window would be a claim rather than a measurement.
    """
    require(result['summary']['reconciliation_complete'] is True, 'RECONCILIATION_INCOMPLETE')
    start, end = searched_span(result, origin)
    return {'basis': 'coverage', 'clock_id': declaration['clock_bridge']['clock_id'],
            'from_us': start, 'to_us': end,
            'concepts': sorted(set(declaration['item_concepts'].values())),
            'derived_from': {'profile': staging.PROFILE,
                             'staging_context_id': result['summary']['context_id'],
                             'input_rows': result['summary']['input_rows'],
                             'reconciliation_complete': True}}


def manifests_for(rows, declaration, warrant):
    """One PRO/SOLID manifest per patient, each carrying the derived warrant.

    Emitting the manifest here is what makes the handoff complete: the caller runs
    `project(build_graph(rows), manifest)` and reaches the matcher without inventing any
    further field. `age` is taken from the declaration because it is a reviewed
    judgement about the source, not something to derive -- MIMIC-IV top-codes ages above
    89 as 91, which is a de-identification category rather than an age.
    """
    ages = declaration['ages']
    manifests = {}
    for row in rows:
        patient = row['patient_id']
        require(patient in ages, 'AGE_NOT_DECLARED_FOR_PATIENT:' + patient)
        manifests[patient] = {
            'profile': 'pro-solid-2.4', 'dataset_id': declaration['dataset_id'],
            'snapshot_id': declaration['staging_context_id'],
            'clock_id': declaration['clock_bridge']['clock_id'],
            'origin': declaration['clock_bridge']['origin'] + 'Z',
            'patient_id': patient, 'episode_id': row['episode_id'],
            'age': ages[patient], 'source_search': warrant}
    return manifests


def handoff(result, declaration):
    """Hand off, or refuse and say exactly what is missing and who supplies it."""
    require(declaration.get('profile') == PROFILE, 'UNSUPPORTED_HANDOFF_PROFILE')
    require(declaration.get('staging_context_id') == result['summary']['context_id'],
            'DECLARATION_NOT_BOUND_TO_THIS_STAGING_RUN')
    check_dischargeable(declaration)
    remaining = outstanding(declaration)
    report = {'profile': PROFILE, 'staging_profile': staging.PROFILE,
              'staging_context_id': result['summary']['context_id'],
              'discharged': sorted(b for b in BLOCKERS if b not in {r['blocker'] for r in remaining}),
              'outstanding': remaining,
              'clinical_validity_claim': False}
    if remaining:
        # Refusal is the normal outcome today and must stay a first-class one: no rows,
        # no warrant, and no partial bundle a caller could mistake for a handoff.
        report.update(status='REFUSED_NOT_MATCHER_READY', review_basis=None,
                      rows=[], source_search=None, skipped=[], manifests={})
        return report
    require(isinstance(declaration.get('item_concepts'), dict) and declaration['item_concepts'],
            'REVIEWED_ITEM_MAPPING_REQUIRED')
    require(isinstance(declaration.get('ages'), dict), 'DECLARED_AGES_REQUIRED')
    origin = declaration['clock_bridge']['origin']
    rows, skipped = rows_for(result, declaration)
    warrant = coverage_warrant(result, declaration, origin)
    report.update(status='HANDED_OFF', review_basis=review_basis(declaration), rows=rows,
                  source_search=warrant, skipped=skipped,
                  manifests=manifests_for(rows, declaration, warrant),
                  staged_segments=result['summary']['staged_segments'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging', type=Path,
                        default=ROOT / 'verification/mimic-inputevents-run/result.json')
    parser.add_argument('--declaration', type=Path,
                        default=ROOT / 'examples/mimic-matcher-handoff/declaration.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'verification/mimic-matcher-handoff-run/result.json')
    args = parser.parse_args(argv)
    try:
        report = handoff(json.loads(args.staging.read_text()),
                         json.loads(args.declaration.read_text()))
    except (HandoffError, KeyError, ValueError) as error:
        print(json.dumps({'status': 'FAILED', 'error': str(error)}), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'discharged': len(report['discharged']),
                      'outstanding': [r['blocker'] for r in report['outstanding']],
                      'rows': len(report['rows']), 'review_basis': report['review_basis']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
