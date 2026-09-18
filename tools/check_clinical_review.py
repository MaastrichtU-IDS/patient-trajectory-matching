"""Validate version-bound human clinical decisions, independently of technical tests.

A valid pending review permits technical work but cannot support a clinical claim.
This checks recorded attestations, not a signer's identity or clinical qualification.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'recorded-clinical-review-1'
DECISIONS = ('scientific_question', 'treatment_anchor', 'measurement_identity',
             'eligibility', 'baseline', 'followup', 'time_and_availability',
             'similarity', 'validation_target')
HASH = re.compile(r'[a-f0-9]{64}\Z')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact_keys(value, fields, label):
    require(isinstance(value, dict) and set(value) == set(fields),
            f'{label} has missing or unexpected fields')


def nonempty(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 8000


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    data = Path(path).read_bytes()
    require(len(data) <= 1024 * 1024, 'Review input exceeds one MiB')
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON value')))


def bound_artifact(binding, root):
    exact_keys(binding, ('path', 'sha256'), 'Artifact binding')
    value = binding['path']
    require(isinstance(value, str) and value and '\\' not in value,
            'Artifact path must be repository relative')
    relative = PurePosixPath(value)
    require(not relative.is_absolute() and '..' not in relative.parts,
            'Artifact path must remain inside repository')
    target = (Path(root) / value).resolve()
    require(target.is_relative_to(Path(root).resolve()) and target.is_file(),
            'Artifact is missing or outside repository')
    digest = binding['sha256']
    require(isinstance(digest, str) and HASH.fullmatch(digest), 'Invalid artifact digest')
    require(hashlib.sha256(target.read_bytes()).hexdigest() == digest,
            f'Artifact changed since review: {value}')
    return target


def attestation(value, protocol_sha256):
    exact_keys(value, ('name', 'role', 'organization', 'date', 'protocol_sha256', 'rationale'),
               'Reviewer attestation')
    for key in ('name', 'role', 'organization', 'rationale'):
        require(nonempty(value[key]), f'Reviewer {key} is required')
        require(value[key].strip().lower() not in {'todo', 'tbd', 'pending', 'unknown'},
                f'Reviewer {key} is a placeholder')
    require(isinstance(value['date'], str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value['date']),
            'Reviewer date must be ISO YYYY-MM-DD')
    require(date.fromisoformat(value['date']) <= date.today(), 'Reviewer date is in the future')
    require(value['protocol_sha256'] == protocol_sha256,
            'Reviewer attestation targets a different protocol version')


def validate_review(review, root=ROOT):
    exact_keys(review, ('schema', 'review_id', 'status', 'dataset_id', 'artifacts',
                       'decisions', 'signoff'), 'Clinical review')
    require(review['schema'] == SCHEMA, 'Unsupported clinical review schema')
    require(nonempty(review['review_id']) and nonempty(review['dataset_id']),
            'Review and dataset identifiers are required')
    artifacts = review['artifacts']
    require(isinstance(artifacts, dict) and {'protocol', 'source_pin', 'candidate_plan'} <= set(artifacts),
            'Review must bind protocol, source pin and candidate plan')
    paths = {name: bound_artifact(value, root) for name, value in artifacts.items()}
    protocol = read_json(paths['protocol'])
    pin = read_json(paths['source_pin'])
    candidate = read_json(paths['candidate_plan'])
    require(all(isinstance(value, dict) for value in (protocol, pin, candidate)), 'Review artifacts must be objects')
    require(all(value.get('dataset_id') == review['dataset_id'] for value in (protocol, pin, candidate)),
            'Review artifacts disagree on source release')
    require(protocol.get('schema') == 'recorded-clinical-protocol-1', 'Unsupported clinical protocol')
    require(protocol.get('artifacts') == {key: value for key, value in artifacts.items() if key != 'protocol'},
            'Protocol must bind every source and definition artifact reviewed')
    require(set(protocol.get('decisions', {})) == set(DECISIONS), 'Protocol decision set is incomplete')
    require(all(nonempty(value) for value in protocol['decisions'].values()), 'Empty protocol decision material')
    require(protocol.get('permitted_claim') == 'reviewed_descriptive_protocol',
            'Review cannot authorize causal, clinical efficacy or real-time availability claims')
    require(isinstance(pin.get('files'), dict) and pin['files'], 'Source pin has no file hashes')
    for entry in pin['files'].values():
        require(isinstance(entry, dict) and isinstance(entry.get('file_sha256'), str)
                and HASH.fullmatch(entry['file_sha256']), 'Invalid source file hash')
    decisions = review['decisions']
    require(isinstance(decisions, dict) and set(decisions) == set(DECISIONS),
            'Review decision set is incomplete or contains unknown decisions')
    protocol_hash = artifacts['protocol']['sha256']
    statuses = []
    for key, value in decisions.items():
        exact_keys(value, ('decision', 'reviewer', 'requested_changes'), f'Decision {key}')
        state = value['decision']
        require(state in ('PENDING', 'ACCEPT', 'REVISE', 'REJECT'), f'Unknown decision for {key}')
        require(value['requested_changes'] is None or nonempty(value['requested_changes']),
                f'Invalid requested changes for {key}')
        if state == 'PENDING':
            require(value['reviewer'] is None, 'Pending decision must not contain an attestation')
        else:
            attestation(value['reviewer'], protocol_hash)
        if state == 'ACCEPT':
            require(value['requested_changes'] is None, 'Acceptance with unincorporated changes is not allowed')
        if state == 'REVISE':
            require(nonempty(value['requested_changes']), 'Revision requires concrete requested changes')
        statuses.append(state)
    derived = ('REJECTED' if 'REJECT' in statuses else
               'REVISION_REQUIRED' if 'REVISE' in statuses else
               'ACCEPTED' if all(value == 'ACCEPT' for value in statuses) and review['signoff'] is not None
               else 'PENDING')
    require(review['status'] == derived, f'Review status must be {derived}')
    if review['signoff'] is not None:
        require(derived == 'ACCEPTED', 'Sign-off requires acceptance of every decision')
        attestation(review['signoff'], protocol_hash)
        require(all(review['signoff']['date'] >= value['reviewer']['date'] for value in decisions.values()),
                'Final sign-off precedes a decision')
    return {'schema': 'clinical-review-check-1', 'review_id': review['review_id'],
            'status': derived, 'dataset_id': review['dataset_id'],
            'clinical_approval_recorded': derived == 'ACCEPTED',
            'clinical_protocol_sha256': protocol_hash,
            'source_file_sha256': {name: entry['file_sha256'] for name, entry in pin['files'].items()},
            'pending_or_unaccepted_decisions': [key for key, value in decisions.items() if value['decision'] != 'ACCEPT'],
            'identity_and_qualification_independently_verified': False,
            'interpretation': 'Checks supplied review records; does not establish clinical usefulness or reviewer identity.'}


def clinical_claim_gate(review, report, root=ROOT):
    """Require approval for this exact source/protocol; never infer it from test success.

    A passed gate allows only a claim that the descriptive protocol has recorded
    approval. Clinical validity, retrieval quality and treatment effect need their
    own empirical evidence. Technical reports need not call this gate.
    """
    checked = validate_review(review, root)
    require(checked['clinical_approval_recorded'], 'Clinical approval is not recorded')
    require(isinstance(report, dict), 'Report must be an object')
    for key in ('dataset_id', 'clinical_protocol_sha256', 'source_file_sha256'):
        require(report.get(key) == checked[key], f'Report does not match reviewed {key}')
    return {'review_id': checked['review_id'], 'clinical_protocol_sha256': checked['clinical_protocol_sha256'],
            'permitted_claim': 'reviewed_descriptive_protocol',
            'clinical_usefulness_established': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review', type=Path)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--require-accepted', action='store_true')
    parser.add_argument('--report', type=Path, help='Check clinical-claim binding for an evaluation report')
    args = parser.parse_args()
    try:
        review = read_json(args.review)
        result = validate_review(review, args.root)
        if args.require_accepted:
            require(result['clinical_approval_recorded'], 'Clinical approval is not recorded')
        if args.report:
            result['report_gate'] = clinical_claim_gate(review, read_json(args.report), args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(2, f'Clinical review check failed: {error}\n')


if __name__ == '__main__':
    main()
