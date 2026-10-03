"""Pinned SULO stack plus committed instance graphs must stay OWL 2 DL and consistent.

This is the ontology-axiom level of the three-level constraint model, which the adapter's
own checks only partially cover: they compare named types against the disjoint groups
derived from the ontology, so anything stated as a class expression rather than a named
class -- restrictions, cardinality, property characteristics -- is outside their reach.
A process with an object as its part is the concrete case the reasoner catches and the
in-process check does not.

ROBOT is given one merged document. Passing the files as separate `--input` documents is
not equivalent and must not be used: the instance graphs do not declare the SULO properties
they use, so OWLAPI parses those triples as annotation assertions. That both reports
spurious punning violations and makes the consistency check vacuous, because annotations
carry no semantics for the reasoner. A negative control below fails the run if the merged
document ever stops detecting a known contradiction.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

from rdflib import Graph, RDF, URIRef

ROBOT = os.environ.get('ROBOT_JAR', '/tmp/robot.jar')
SULO = 'ontology/vendor/sulo-0.2.14.ttl'
PROFILE = 'ontology/pro-solid-profile.ttl'
GRAPHS = ('examples/pro-solid/graph.ttl', 'demo/evidence/pro-solid/graph.ttl')
S = 'https://w3id.org/sulo/'


def merged(instance, *, contradiction=False):
    """Serialize SULO, the application profile and one instance graph as a single document."""
    g = Graph()
    for path in (SULO, PROFILE, instance):
        g.parse(path, format='turtle')
    if contradiction:
        # A fresh individual in two disjoint SULO classes. Minting it keeps the control
        # independent of whatever the fixture happens to contain.
        subject = URIRef('urn:negative-control:instant')
        g.add((subject, RDF.type, URIRef(S + 'StartTime')))
        g.add((subject, RDF.type, URIRef(S + 'EndTime')))
    handle = tempfile.NamedTemporaryFile('w', suffix='.ttl', delete=False)
    handle.write(g.serialize(format='turtle'))
    handle.close()
    return handle.name


def robot(*arguments):
    return subprocess.run(['java', '-jar', ROBOT, *arguments], capture_output=True, text=True)


def profile_in_dl(document):
    report = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False).name
    run = robot('validate-profile', '--profile', 'DL', '--input', document, '--output', report)
    text = pathlib.Path(report).read_text()
    return run.returncode == 0 and 'Ontology and imports closure in profile' in text, text


def consistent(document):
    output = tempfile.NamedTemporaryFile('w', suffix='.owl', delete=False).name
    run = robot('merge', '--input', document, 'reason', '--reasoner', 'HermiT', '--output', output)
    return run.returncode == 0, (run.stdout + run.stderr)


def main():
    if not pathlib.Path(ROBOT).exists():
        print(f'::error::ROBOT jar not found at {ROBOT}; set ROBOT_JAR')
        return 1
    problems = []
    for instance in GRAPHS:
        document = merged(instance)
        in_profile, report = profile_in_dl(document)
        if not in_profile:
            problems.append(f'{instance}: not in the OWL 2 DL profile\n{report.strip()[:2000]}')
        ok, log = consistent(document)
        if not ok:
            problems.append(f'{instance}: inconsistent under HermiT\n{log.strip()[:2000]}')
        # Negative control: the same merged document with a known contradiction must fail.
        # Without this a parse regression would turn the check above into a silent pass.
        detected, _ = consistent(merged(instance, contradiction=True))
        if detected:
            problems.append(f'{instance}: negative control was NOT detected; the consistency '
                            f'check is vacuous and proves nothing')
        if not problems:
            print(f'owl: {instance} is OWL 2 DL and consistent; negative control detected')
    for problem in problems:
        print(f'::error::{problem}')
    print(f'owl: checked {len(GRAPHS)} merged graphs, {len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
