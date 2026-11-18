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

A report may also bind its build phase by a runtime record (D34): `{"rule": "runtime_record_v1",
"modules": {path: digest}, "files": {path: digest}, "versions": {...}, "root": ...}`, every
repository file the build processes loaded or read (`patterns/build_record.py`). Its maps are artifact
maps, so each digest is recomputed and each path must still exist, as for any binding; the record's
rule and keys are checked too, so an unknown rule (the earlier static fingerprint included) fails.
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
              # D34 froze a third, a departure from its rule 2 signed off by the owner's delegate and
              # following the D30 and D31 precedent: D32's paired latency verdict, produced on the D32
              # branch (against main at 7f5930e), binds `tools/benchmark_execution_store.py`, which D34's
              # build record changed. It records D32's decision; for the current tree the matcher report's
              # `paired_confirmation` supersedes it.
              'verification/d32-paired-verdict.json',
              # D39 froze a fourth, on the same precedent, for the coordinator's sign-off: D38's paired latency
              # verdict binds `patterns/store_conditions.py`, the one entry point, into which D39 added its route
              # (profile 1.3), as D38 had provided. It records D38's decision on the D38 tree (against main at
              # e682817); for the current tree `verification/d39-paired-verdict.json` supersedes it.
              'verification/d38-paired-verdict.json',
              # D41 froze a fifth and a sixth, on the same precedent, for the coordinator's sign-off: D36's and D39's
              # paired latency verdicts bind `tools/benchmark_execution_store.py` and `tools/benchmark_matcher.py`, whose
              # queries D41 moved to the engine's default search budget. Each records its decision, measured with the
              # generator's drawn budgets (1, 40, 300) on its own tree (D36 against main at 81e812c, D39 against main
              # at 24cd863). Their code verdicts (each ratio against the main before it) stand as recorded; for the
              # current tree the matcher report's paired run and confirmation, at realistic budgets, supersede only
              # their absolute figures (the D21 target's p95, maximum and memory).
              # D42 changed three more files D39's verdict binds (`patterns/store_conditions.py`,
              # `store_conditions_gene.py`, `store_conditions_coded.py`: the joint profile's route and the shared hooks);
              # for the current tree `verification/d42-paired-verdict.json` supersedes it there too (coordinator's
              # sign-off as delegate).
              'verification/d36-paired-verdict.json', 'verification/d39-paired-verdict.json',
              # D43 did not ship (its paired verdict missed as written), so its code is not in the tree; its two reports
              # are kept as frozen records of that decision (coordinator's sign-off as delegate). Both bind
              # `patterns/store_conditions_counts.py` and the dispatcher with D43's hook, as at the retry base 6888d1e.
              'verification/d43-paired-verdict.json', 'verification/d43-coded-usefulness-report.json',
              # D43b, D43's retry, did not ship either (its count-request latency rule missed as written), so its code is
              # not in the tree; its two reports are kept as frozen records of that decision (coordinator's sign-off as
              # delegate). Both bind `patterns/store_conditions_counts.py`, the dispatcher with the hook, the parity
              # replay and the count-latency tool as at the retry base 0253f87.
              'verification/d43b-count-latency-verdict.json', 'verification/d43b-coded-usefulness-report.json',
              # D43c, the second retry, puts the hook back into `patterns/store_conditions.py` (and the post-pass into
              # `demos/mockups/store_parity.py`'s replay and `coded_usefulness.cjs`'s bindings), so it freezes the three
              # reports D43 and D43b froze while the hook was in the tree, on their precedent (signed off by the
              # coordinator as delegate at D43c's code checkpoint): D39's coded-only usefulness report (D39's decision
              # on the D39 tree, main at 24cd863), and D42's paired verdict and joint usefulness report (D42's decision
              # on the D42 tree, against main at f99e4f9). D43c's own reports supersede them for the current tree. Undone if the hook is removed.
              'verification/d39-coded-usefulness-report.json',
              'verification/d42-paired-verdict.json', 'verification/d42-gene-coded-usefulness-report.json',
              # D44 did not ship (its count-request latency rule, 6.2, missed as written), so its code is not in the
              # tree; its two latency verdicts are kept as frozen records of that decision (coordinator's sign-off as
              # delegate). Both bind `patterns/execution_store.py`, `patterns/store_conditions_counts.py` and D44's
              # tools as at the retry base 4218110.
              'verification/d44-count-latency-verdict.json', 'verification/d44-exclusions-latency-verdict.json',
              # D44b puts D44's code back into `patterns/execution_store.py`, which D43c's count-latency verdict binds;
              # it is frozen as historical (D43c's decision on the D43c tree, against main at a28c324), superseded for
              # the current tree by `verification/d44b-count-latency-verdict.json` (coordinator's sign-off as delegate).
              # Undone if D44b is reverted.
              'verification/d43c-count-latency-verdict.json'}


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


RECORD_RULE = 'runtime_record_v1'
RECORD_KEYS = {'rule', 'modules', 'files', 'versions', 'root', 'settings'}


def build_records(report, found):
    """The build record of a report (D34): its top-level `build_provenance`, named explicitly. Its `modules` and
    `files` are maps of repository paths to digests, checked by `artifact_maps` like any other; this returns the
    record so that its shape can be checked too."""
    record = report.get('build_provenance') if isinstance(report, dict) else None
    if record is not None:
        found.append(record if isinstance(record, dict) else {'rule': None})
    return found


def record_problems(record):
    """Why a build record is not one the gate can check, as strings: an unknown rule (the earlier static fingerprint
    included) or key, or a `modules` or `files` that is not a non-empty map of repository paths to digests."""
    if record.get('rule') != RECORD_RULE:
        return [f'a build record has an unknown rule {record.get("rule")!r}']
    if set(record) != RECORD_KEYS:
        return [f'a build record has keys {sorted(record)}, not {sorted(RECORD_KEYS)}']
    problems = []
    for key in ('modules', 'files'):
        value = record[key]
        if not (isinstance(value, dict) and value and all(repository_path(k) and is_digest(v) for k, v in value.items())):
            problems.append(f'a build record\'s {key} is not a map of repository paths to digests')
    return problems


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
        records = build_records(report, [])
        if not maps and not records:
            continue
        checked += 1
        for mapping in maps:
            for path, recorded in mapping.items():
                bound += 1
                if not (ROOT / path).is_file():
                    problems.append(f'{name}: {path} is bound but no longer in the repository')
                elif recorded not in digests(path):
                    problems.append(f'{name}: {path} no longer matches its recorded digest')
        for record in records:
            problems.extend(f'{name}: {problem}' for problem in record_problems(record))
    for problem in sorted(set(problems)):
        print(f'::error::{problem}')
    print(f'report provenance: {checked} reports, {bound} bindings, '
          f'{len(set(problems))} stale')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
