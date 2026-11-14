"""Every artifact digest a committed report binds must still describe the file.

Reports record which implementation files produced them. That binding is only worth
something if something checks it, and most of it was checked by nothing: three reports
carrying over a hundred bindings between them had no reader at all, so they drifted
through several releases without a single test noticing. A binding that cannot fail is
decoration.

This walks every tracked report, finds the maps from repository-relative paths to
sha256 digests, and requires each one to still match its file -- and to still exist.

A map is recognized by the shape of its keys, not by whether the files are present.
An earlier version only accepted a map when every key was an existing file, so moving
one file made its whole map invisible and the gate passed with fewer bindings: moving
`demo/` would have dropped 863 of them in silence. A path that a report names and the
tree no longer has is now a failure like any stale digest.

Two digest conventions are in use across the repository -- some producers hash the
file's bytes, others hash `read_text()`, which normalizes CRLF -- so a digest matching
either is accepted. That is deliberately permissive: this gate exists to catch stale
provenance, and it must not fail a report whose producer simply chose the other
convention. Unifying them is a separate change, recorded as R4 in docs/issues.md.

A report may also bind part of a file by fingerprint (D33): a map from repository paths to
`{"rule": "build_closure_v1", "roots": [...], "sha256": ...}`, the digest of the source of every
definition reachable from the roots (`patterns/build_fingerprint.py`). The scaling report binds its
build phase this way, so that a change to matching alone does not stale the builds. Each such entry
is recomputed from the file as it is now and must match, exactly as a digest must; an unknown rule
fails.
"""
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Superseded release manifests describe the tree as it stood at that release, which is
# the point of keeping them. `check_manifest.py` makes the same exemption for the same
# reason; this list is not a place to park a report that has merely gone stale.
HISTORICAL = {'verification/v24-release-manifest.json', 'verification/v25-release-manifest.json',
              # D32 froze two closed records of earlier decisions, each produced on a tree whose
              # `patterns/execution_store.py` D32 then changed. Regenerating either on later code would
              # change what it records, not refresh it:
              # - D30's attribution of every moved result to a stage, produced on the D31 tree (7f5930e);
              #   a closed record, superseded by nothing;
              # - D31's paired latency verdict, produced on the D31 tree (7f5930e); superseded for the
              #   current tree by `verification/d32-paired-verdict.json`.
              'verification/d30-attribution-report.json', 'verification/d31-paired-verdict.json',
              # D33 froze a third, for the same reason: D32's paired latency verdict, produced on the
              # D32 branch (against main at 7f5930e), binds `tools/benchmark_execution_store.py`, which
              # D33's build fingerprint changed. It records D32's decision; for the current tree the
              # matcher report's `paired_confirmation` supersedes it.
              'verification/d32-paired-verdict.json'}


def digests(path):
    """Both conventions, so a producer's choice is never mistaken for staleness."""
    raw = (ROOT / path).read_bytes()
    answers = {hashlib.sha256(raw).hexdigest()}
    try:
        answers.add(hashlib.sha256((ROOT / path).read_text().encode('utf-8')).hexdigest())
    except (UnicodeDecodeError, ValueError):
        pass
    return answers


HEX = set('0123456789abcdef')

# Roots of files that live outside this repository but are bound by digest: the pinned
# MIMIC-IV demo layout. Listing them, rather than inferring repository paths from what
# git tracks today, is deliberate. Inferring failed exactly when it mattered: once a
# whole top-level folder moved, its old name was no longer tracked, so every binding
# under it stopped looking like a repository path and was skipped again. An unknown
# external root now fails the gate loudly instead, and belongs on this list.
EXTERNAL_ROOTS = {'icu', 'hosp'}


def repository_path(key):
    """Whether a key names a file in this repository, present or not.

    Decided by shape: relative, inside the tree, with a file suffix, and not under a
    known external root.
    """
    if not isinstance(key, str) or not key or key.startswith('/') or '://' in key:
        return False
    path = pathlib.PurePosixPath(key)
    return '..' not in path.parts and bool(path.suffix) and path.parts[0] not in EXTERNAL_ROOTS


def is_digest(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def artifact_maps(node, found):
    """Every dict that maps repository paths to 64-hex digests, whether or not each
    path still exists.

    Recognized by shape rather than by key name, because producers call these
    `artifacts`, `mapping_files`, `fixture_sha256` and more; keying off a name would
    quietly skip whichever one gets added next.
    """
    if isinstance(node, dict):
        keys = list(node)
        if keys and all(repository_path(k) and is_digest(node[k]) for k in keys):
            found.append(node)
        for value in node.values():
            artifact_maps(value, found)
    elif isinstance(node, list):
        for value in node:
            artifact_maps(value, found)
    return found


FINGERPRINT_KEYS = {'rule', 'roots', 'definitions', 'sha256'}


def fingerprint_maps(node, found):
    """Every dict that maps repository paths to fingerprint records (D33), found by shape like `artifact_maps`."""
    if isinstance(node, dict):
        keys = list(node)
        if keys and all(repository_path(k) and isinstance(node[k], dict) and {'rule', 'roots', 'sha256'} <= set(node[k])
                        and set(node[k]) <= FINGERPRINT_KEYS for k in keys):
            found.append(node)
        for value in node.values():
            fingerprint_maps(value, found)
    elif isinstance(node, list):
        for value in node:
            fingerprint_maps(value, found)
    return found


def fingerprint_problem(path, record):
    """None when the recorded fingerprint still describes the file, else why not."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from patterns import build_fingerprint as bf
    if record['rule'] != bf.RULE:
        return f'{path} is bound by an unknown fingerprint rule {record["rule"]!r}'
    if not (ROOT / path).is_file():
        return f'{path} is bound but no longer in the repository'
    try:
        now = bf.fingerprint(path, record['roots'])
    except bf.FingerprintError as exc:
        return f'{path} can no longer be fingerprinted ({exc})'
    return None if now == record['sha256'] else f'{path} no longer matches its recorded build fingerprint'


def records_failure(report):
    """Whether a report is an account of a run that did not succeed.

    Several producers deliberately overwrite a stale success with a failure record, so
    that a previous VERIFIED report cannot sit there implying the current state is good.
    That is the right call, and it leaves this gate a problem: the stub's artifact
    digests are computed fresh at the moment of failure, so every binding matches and a
    digest comparison reports the report as current. The profiler's stub carries a full
    map of 73 bindings, so it is not merely skipped -- it is counted as healthy, and the
    gate falls silent about the measurement that was just replaced.

    Recognized by shape rather than by one producer's vocabulary, like artifact_maps
    above: a status naming a failure, an explicit `passed: False`, or a populated
    failure field. A report that merely carries `failure: None` has not failed.
    """
    if not isinstance(report, dict):
        return False
    status = report.get('status')
    if isinstance(status, str) and 'FAIL' in status.upper():
        return True
    if report.get('passed') is False:
        return True
    return any(report.get(key) for key in ('failure', 'failure_code'))


def loaded(name):
    """The parsed report, or None when it is not readable JSON."""
    try:
        return json.loads((ROOT / name).read_text())
    except (ValueError, UnicodeDecodeError):
        return None


def tracked_reports():
    listing = subprocess.run(['git', 'ls-files', 'verification'], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    return sorted(name for name in listing.stdout.split() if name.endswith('.json'))


def main():
    problems, checked, bound = [], 0, 0
    for name in tracked_reports():
        if name in HISTORICAL:
            continue
        report = loaded(name)
        if report is None:
            continue
        # Checked before the digests, and independently of whether this report binds
        # anything: a failure stub's bindings are fresh by construction, so comparing
        # them proves only that the failure was recorded recently.
        if records_failure(report):
            problems.append(f'{name}: records a failed run and is not current verification')
            checked += 1
            continue
        maps = artifact_maps(report, [])
        prints = fingerprint_maps(report, [])
        if not maps and not prints:
            continue
        checked += 1
        for mapping in maps:
            for path, recorded in mapping.items():
                bound += 1
                if not (ROOT / path).is_file():
                    problems.append(f'{name}: {path} is bound but no longer in the repository')
                elif recorded not in digests(path):
                    problems.append(f'{name}: {path} no longer matches its recorded digest')
        for mapping in prints:
            for path, record in mapping.items():
                bound += 1
                problem = fingerprint_problem(path, record)
                if problem:
                    problems.append(f'{name}: {problem}')
    for problem in sorted(set(problems)):
        print(f'::error::{problem}')
    print(f'report provenance: {checked} reports, {bound} bindings, '
          f'{len(set(problems))} stale')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
