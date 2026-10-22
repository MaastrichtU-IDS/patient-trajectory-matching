"""Three checks a SULO consumer needs, and what each one misses on its own.

Run with a SULO core and a target:

    python examples/sulo-validation-demo/check.py \
        --sulo ontology/vendor/sulo-0.2.14.ttl \
        --target examples/sulo-validation-demo/toy-extension.ttl \
        --robot /path/to/robot.jar

The two toy files are deliberately flawed in different ways. `toy-extension.ttl`
references `sulo:isLocatedIn`, which SULO removed at 0.2.8. `toy-data.ttl` asserts an
individual into two classes SULO declares disjoint. Neither file is malformed, and each
is accepted by the obvious way of checking it.

The order of the checks matters, because each later step destroys the evidence the
earlier one needs:

1. Dangling terms, read from the files. `robot merge` declares any entity it finds
   undeclared, so after a merge `sulo:isLocatedIn` looks like a property SULO defines.
   This has to run first or not at all.
2. OWL 2 DL profile, on the merged document.
3. Consistency, on the merged document. Checking a target by itself is the trap: its
   SULO properties are undeclared there, so OWLAPI reads them as annotation assertions,
   annotations carry no semantics, and the reasoner reports a contradiction consistent.
"""
import argparse
import pathlib
import subprocess
import sys
import tempfile

from rdflib import Graph, URIRef

SULO = 'https://w3id.org/sulo/'


def robot(jar, *args):
    return subprocess.run(['java', '-jar', str(jar), *args], capture_output=True, text=True)


def dangling_terms(sulo_path, target_path):
    """SULO IRIs the target uses that the core does not define.

    Deliberately not a reasoning step. It is a set difference over the two files,
    performed before anything can add declarations to either.
    """
    core = Graph().parse(sulo_path, format='turtle')
    defined = {str(s) for s in set(core.subjects()) if str(s).startswith(SULO)}
    target = Graph().parse(target_path, format='turtle')
    used = {str(n) for triple in target for n in triple
            if isinstance(n, URIRef) and str(n).startswith(SULO)}
    return sorted(u for u in used if u not in defined and not u.endswith('.ttl'))


def merged(jar, sulo_path, target_path):
    out = tempfile.NamedTemporaryFile(suffix='.ttl', delete=False).name
    run = robot(jar, 'merge', '--input', str(sulo_path), '--input', str(target_path),
                '--output', out)
    return (out, run) if run.returncode == 0 else (None, run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sulo', required=True, type=pathlib.Path)
    parser.add_argument('--target', required=True, type=pathlib.Path)
    parser.add_argument('--robot', required=True, type=pathlib.Path)
    args = parser.parse_args()
    problems = []

    print(f'target: {args.target}')
    print(f'core:   {args.sulo}\n')

    dangling = dangling_terms(args.sulo, args.target)
    print('1. dangling SULO terms (read from the files, before any merge)')
    for term in dangling:
        print(f'     NOT DEFINED IN THE CORE  {term}')
        problems.append(f'dangling term: {term}')
    if not dangling:
        print('     none')

    document, run = merged(args.robot, args.sulo, args.target)
    if document is None:
        print('\n   merge failed:', (run.stdout + run.stderr).strip()[:200])
        return 2

    # The same lookup against the merged document, to show why step 1 cannot be
    # deferred: merge supplies declarations for exactly the terms step 1 reported.
    after = Graph().parse(document, format='turtle')
    invented = [t for t in dangling if (URIRef(t), None, None) in after]
    if invented:
        print(f'     ...and after merging, {len(invented)} of them now appear declared,')
        print('        which is why this check runs first')

    print('\n2. OWL 2 DL profile (merged)')
    report = tempfile.NamedTemporaryFile(suffix='.txt', delete=False).name
    run = robot(args.robot, 'validate-profile', '--profile', 'DL',
                '--input', document, '--output', report)
    text = pathlib.Path(report).read_text().strip()
    if run.returncode == 0 and 'in profile' in text:
        print('     in profile')
    else:
        print('    ', text.splitlines()[0] if text else 'violation')
        problems.append('not in the OWL 2 DL profile')

    print('\n3. consistency under HermiT (merged)')
    run = robot(args.robot, 'reason', '--reasoner', 'HermiT', '--input', document,
                '--output', tempfile.NamedTemporaryFile(suffix='.ttl', delete=False).name)
    if run.returncode == 0:
        print('     consistent')
    else:
        print('     INCONSISTENT')
        problems.append('inconsistent under HermiT')

    print(f'\n{len(problems)} problem(s)')
    for p in problems:
        print(f'   {p}')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
