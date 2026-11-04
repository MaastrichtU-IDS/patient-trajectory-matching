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
HISTORICAL = {'verification/v24-release-manifest.json'}


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


def tracked_roots():
    """The top-level names git tracks: where a repository-relative path can begin."""
    if not hasattr(tracked_roots, 'cache'):
        listing = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True)
        tracked_roots.cache = {name.split('/', 1)[0] for name in listing.stdout.split()}
    return tracked_roots.cache


def repository_path(key):
    """Whether a key names a file in this repository, present or not.

    It must be relative, stay inside the tree, carry a file suffix, and begin at a
    tracked top-level name. That keeps out digests of external sources -- the pinned
    MIMIC files are keyed `icu/...` and `hosp/...` -- and identifiers that happen to be
    mapped to digests.
    """
    if not isinstance(key, str) or not key or key.startswith('/') or '://' in key:
        return False
    parts = pathlib.PurePosixPath(key).parts
    return '..' not in parts and bool(pathlib.PurePosixPath(key).suffix) and parts[0] in tracked_roots()


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
        if not maps:
            continue
        checked += 1
        for mapping in maps:
            for path, recorded in mapping.items():
                bound += 1
                if not (ROOT / path).is_file():
                    problems.append(f'{name}: {path} is bound but no longer in the repository')
                elif recorded not in digests(path):
                    problems.append(f'{name}: {path} no longer matches its recorded digest')
    for problem in sorted(set(problems)):
        print(f'::error::{problem}')
    print(f'report provenance: {checked} reports, {bound} bindings, '
          f'{len(set(problems))} stale')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
