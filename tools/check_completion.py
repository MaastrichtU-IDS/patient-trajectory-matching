#!/usr/bin/env python3
"""Audit completion accounting. Exit 0 means consistency, not project completion.
Referenced acceptance commands are NOT executed by this checker.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'supported', 'partial', 'blocked', 'specification-only'}


def _file(root, value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'Invalid repository evidence path: {value!r}')
    relative = Path(value)
    resolved = (root / relative).resolve()
    if relative.is_absolute() or '..' in relative.parts or not resolved.is_relative_to(root.resolve()):
        raise ValueError(f'Evidence must remain inside repository: {value!r}')
    if not resolved.is_file():
        raise ValueError(f'Missing evidence file: {value}')
    return resolved


def _strings(value, name, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f'{name} must be a list of nonempty strings')
    if len(value) != len(set(value)):
        raise ValueError(f'Duplicate values in {name}')
    return value


def validate(register, root=ROOT):
    root = Path(root).resolve()
    if register.get('profile') != 'completion-register-1.0':
        raise ValueError('Unsupported completion register profile')
    source = register['requirements_source']
    if source.get('path') != 'requirements.csv':
        raise ValueError('The original requirements.csv must remain the requirement authority')
    data = _file(root, source['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != source['sha256']:
        raise ValueError('Requirement source digest changed; explicitly reassess the register')
    legacy = list(csv.DictReader(data.decode('utf-8-sig').splitlines()))
    original = {row['requirement_id']: row for row in legacy}
    if len(original) != len(legacy):
        raise ValueError('Duplicate legacy requirement IDs')
    entries = register['requirements']
    ids = [entry['requirement_id'] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate completion requirement IDs')
    if set(ids) != set(original):
        raise ValueError(f'Requirement coverage mismatch; missing={sorted(set(original)-set(ids))}, extra={sorted(set(ids)-set(original))}')
    checks = register['checks']
    for key, check in checks.items():
        if not key or not check.get('command') or not check.get('scope') or check.get('kind') != 'executable_acceptance':
            raise ValueError(f'Invalid acceptance check: {key}')
        if check.get('last_run') != 'not_executed_by_completion_checker':
            raise ValueError(f'Completion checker must not fabricate execution: {key}')
    dependencies = register['dependencies']
    dep_ids = [item['id'] for item in dependencies]
    if len(dep_ids) != len(set(dep_ids)):
        raise ValueError('Duplicate dependency IDs')
    for dependency in dependencies:
        for key in ('kind','title','acceptance','intended_input_location','intended_evidence_location'):
            if not isinstance(dependency.get(key), str) or not dependency[key].strip():
                raise ValueError(f'Dependency lacks {key}: {dependency["id"]}')
        _file(root, dependency['existing_reference'])
    evidence = {source['path']}
    def references(item):
        for path in _strings(item['evidence'], 'evidence', nonempty=True):
            _file(root, path)
            evidence.add(path)
        for key in _strings(item['acceptance_checks'], 'acceptance_checks'):
            if key not in checks:
                raise ValueError(f'Unknown acceptance check: {key}')
        if not isinstance(item.get('scope'), str) or not item['scope'].strip():
            raise ValueError('Each assessed item needs an explicit scope')
    for entry in entries:
        key = entry['requirement_id']
        if entry.get('legacy') != original[key]:
            raise ValueError(f'Legacy requirement content changed: {key}')
        status = entry['status']
        if status not in STATUSES:
            raise ValueError(f'Unsupported status {status!r}: {key}')
        references(entry)
        remaining = _strings(entry['remaining'], 'remaining')
        deps = _strings(entry['dependencies'], 'dependencies')
        if set(deps)-set(dep_ids):
            raise ValueError(f'Unknown dependencies: {key}')
        if status == 'supported' and (remaining or deps or not entry['acceptance_checks']):
            raise ValueError(f'Supported requirement has open work or lacks executable acceptance: {key}')
        if status != 'supported' and not remaining:
            raise ValueError(f'Incomplete requirement must identify remaining work: {key}')
        if status == 'blocked' and not deps:
            raise ValueError(f'Blocked requirement needs a named external dependency: {key}')
    profiles = register['additional_profiles']
    profile_ids = [p['id'] for p in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError('Duplicate additional profile IDs')
    for profile in profiles:
        references(profile)
        if profile.get('closes_legacy_requirement_automatically') is not False:
            raise ValueError('Additional profiles cannot silently close legacy requirements')
    h0 = register['h0_acceptance']
    required = {'bounded_query_by_example','first_refinement','second_refinement','exact_vs_relaxed_cohort_comparison','source_evidence_inspection'}
    if set(_strings(h0['required_journey'],'required_journey')) != required:
        raise ValueError('H0 journey omits an addendum 2.1 acceptance obligation')
    if h0['status'] not in {'pending_integration_verification','bounded_synthetic_integration_verified'}:
        raise ValueError('Invalid H0 integration status')
    for path in _strings(h0['evidence'],'h0 evidence',True):
        _file(root,path)
        evidence.add(path)
    if h0['status'] == 'bounded_synthetic_integration_verified':
        report = json.loads(_file(root,h0['verification_report']).read_text())
        evidence.add(h0['verification_report'])
        if report.get('passed') is not True or set(report.get('verified_journey',[])) != required:
            raise ValueError('H0 status needs a passing report covering the complete journey')
        if report.get('profile') != 'research-prototype-journey-verification-1.0' or report.get('scope') != 'authored-synthetic-research-prototype' or report.get('clinical_validity_claim') is not False or report.get('replay_verified') is not True:
            raise ValueError('H0 report must retain bounded scope and replay verification')
        artifacts = report.get('artifacts')
        if not isinstance(artifacts, dict) or not artifacts:
            raise ValueError('H0 report lacks artifact identity')
        for path, expected in artifacts.items():
            actual = hashlib.sha256(_file(root,path).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f'Stale H0 acceptance evidence: {path}')
            evidence.add(path)
    counts = {status: sum(e['status']==status for e in entries) for status in sorted(STATUSES)}
    if register.get('project_complete') is not False or register.get('production_ready') is not False or register.get('clinical_validity_claim') is not False:
        raise ValueError('This audit cannot establish full product, production or clinical readiness')
    return {'profile':'completion-audit-1.0','audit_consistent':True,'acceptance_commands_executed':False,
            'project_complete':False,'production_ready':False,'clinical_validity_claim':False,
            'requirements_total':len(entries),'status_counts':counts,
            'additional_profile_count':len(profiles),'h0_status':h0['status'],
            'unsatisfied_external_dependencies':sorted(dep_ids),
            'requirements_sha256':source['sha256'],
            'register_sha256':hashlib.sha256(json.dumps(register,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'evidence_sha256':{p:hashlib.sha256(_file(root,p).read_bytes()).hexdigest() for p in sorted(evidence)},
            'interpretation':'Audit consistency only. Referenced tests are not rerun; partial, blocked and specification-only requirements remain incomplete.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register',type=Path,default=ROOT/'verification/completion-register.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        report=validate(json.loads(args.register.read_text()))
    except (KeyError,TypeError,ValueError,OSError) as exc:
        print(json.dumps({'profile':'completion-audit-1.0','audit_consistent':False,'project_complete':False,'error':str(exc)}),file=sys.stderr)
        return 2
    payload=json.dumps(report,indent=2,sort_keys=True)+'\n'
    if args.output:
        protected = {args.register.resolve(), Path(__file__).resolve()} | {(ROOT/path).resolve() for path in report['evidence_sha256']}
        if args.output.resolve() in protected:
            parser.error('Output cannot overwrite the register, authority, checker or referenced evidence')
        if args.output.exists():
            try:
                previous = json.loads(args.output.read_text())
            except (ValueError, OSError):
                previous = None
            if not isinstance(previous, dict) or previous.get('profile') != 'completion-audit-1.0':
                parser.error('Output may only replace an existing completion audit report')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',dir=args.output.parent,delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(payload)
        temporary.replace(args.output)
        print(json.dumps({key:report[key] for key in ('audit_consistent','project_complete','requirements_total','status_counts','h0_status')}))
    else:
        print(payload,end='')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
