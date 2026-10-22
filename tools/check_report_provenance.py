"""Every artifact digest a committed report binds must still describe the file.

Reports record which implementation files produced them. That binding is only worth
something if something checks it, and most of it was checked by nothing: three reports
carrying over a hundred bindings between them had no reader at all, so they drifted
through several releases without a single test noticing. A binding that cannot fail is
decoration.

This walks every tracked report, finds the maps from repository-relative paths to
sha256 digests, and requires each one to still match its file.

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


def artifact_maps(node, found):
    """Every dict that maps existing repository paths to 64-hex digests.

    Recognized by shape rather than by key name, because producers call these
    `artifacts`, `mapping_files`, `fixture_sha256` and more; keying off a name would
    quietly skip whichever one gets added next.
    """
    if isinstance(node, dict):
        keys = list(node)
        if keys and all(isinstance(k, str) and isinstance(node[k], str)
                        and len(node[k]) == 64 and (ROOT / k).is_file() for k in keys):
            found.append(node)
        for value in node.values():
            artifact_maps(value, found)
    elif isinstance(node, list):
        for value in node:
            artifact_maps(value, found)
    return found


def tracked_reports():
    listing = subprocess.run(['git', 'ls-files', 'verification'], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    return sorted(name for name in listing.stdout.split() if name.endswith('.json'))


def main():
    problems, checked, bound = [], 0, 0
    for name in tracked_reports():
        if name in HISTORICAL:
            continue
        try:
            report = json.loads((ROOT / name).read_text())
        except (ValueError, UnicodeDecodeError):
            continue
        maps = artifact_maps(report, [])
        if not maps:
            continue
        checked += 1
        for mapping in maps:
            for path, recorded in mapping.items():
                bound += 1
                if recorded not in digests(path):
                    problems.append(f'{name}: {path} no longer matches its recorded digest')
    for problem in sorted(set(problems)):
        print(f'::error::{problem}')
    print(f'report provenance: {checked} reports, {bound} bindings, '
          f'{len(set(problems))} stale')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
