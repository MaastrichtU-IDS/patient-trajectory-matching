"""The SULO stack and every committed graph that uses it must stay OWL 2 DL and consistent.

This is the ontology-axiom level of the three-level constraint model, which the adapter's
own checks only partially cover: they compare named types against the disjoint groups
derived from the ontology, so anything stated as a class expression rather than a named
class -- restrictions, cardinality, property characteristics -- is outside their reach.
A process with an object as its part is the concrete case the reasoner catches and the
in-process check does not.

Three groups of targets are checked.

*The stack itself.* The pinned SULO core merged with every committed application profile.
The profiles are small and are authored independently of each other, so nothing else
establishes that their union stays in the profile and has a model.

*SULO-side graphs.* Every committed instance graph that speaks SULO, merged with the whole
stack. That includes the eight `examples/temporal-interface/*/sulo.ttl` fixtures, which
carry the conformance claim in docs/temporal-kg/sulo-interface.md.

*Formal-core graphs.* The other half of each of those eight pairs, merged with the
standalone core in docs/temporal-kg/ontology/temporal-core.ofn. They use the core's own
vocabulary and are not SULO graphs; merging them with the SULO stack reports every one of
their classes as undeclared and proves nothing. Checking only the SULO half would leave the
conformance pairing gated on one side.

ROBOT is given one merged document per target. Passing the files as separate `--input`
documents is not equivalent and must not be used: the instance graphs do not declare the
SULO properties they use, so OWLAPI parses those triples as annotation assertions. That both
reports spurious punning violations and makes the consistency check vacuous, because
annotations carry no semantics for the reasoner.

Every target carries negative controls that must all be detected, or the run fails. For a
target that has instance data the controls are **built out of the document's own content** --
each contradicts a type the document asserts, reached through a property assertion the
document makes. A control minted from fresh individuals would fire against SULO alone and so
would survive the instance graph being dropped from the merge or parsed as annotations,
which is the regression the control exists to catch. The stack target has no instance data;
its control is a probe individual, and what it establishes is correspondingly different --
see `stack_controls`.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

from rdflib import Graph, Literal, RDF, URIRef

ROBOT = os.environ.get('ROBOT_JAR', '/tmp/robot.jar')
ROOT = pathlib.Path(__file__).resolve().parents[2]

SULO = 'ontology/vendor/sulo-0.2.14.ttl'
CORE = 'docs/temporal-kg/ontology/temporal-core.ofn'
S = 'https://w3id.org/sulo/'
F = 'https://example.org/temporal-kg/v2#'

# Globbed rather than listed so a new profile or fixture is gated without being remembered.
# The counts below are asserted, so a file that disappears from a glob fails the run instead
# of quietly reducing coverage.
PROFILES = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'ontology').glob('*.ttl')
                  if 'shapes' not in p.name)
PAIRS = sorted((ROOT / 'examples/temporal-interface').glob('*/sulo.ttl'))
SULO_GRAPHS = ['examples/pro-solid/graph.ttl', 'demo/evidence/pro-solid/graph.ttl',
               'examples/exact-interval/graph.ttl'] + \
              [str(p.relative_to(ROOT)) for p in PAIRS]
CORE_GRAPHS = [str(p.relative_to(ROOT)).replace('sulo.ttl', 'formal.ttl') for p in PAIRS]
EXPECTED = {'profiles': 9, 'sulo graphs': 11, 'core graphs': 8}


def robot(*arguments):
    return subprocess.run(['java', '-jar', ROBOT, *arguments], capture_output=True, text=True)


def as_turtle(path):
    """ROBOT reads functional syntax; rdflib does not, and the merge is done in rdflib."""
    if not path.endswith('.ofn'):
        return str(ROOT / path)
    output = tempfile.NamedTemporaryFile('w', suffix='.ttl', delete=False).name
    run = robot('convert', '--input', str(ROOT / path), '--format', 'ttl', '--output', output)
    if run.returncode != 0:
        raise RuntimeError(f'could not convert {path}: {(run.stdout + run.stderr).strip()[:400]}')
    return output


def merge(paths):
    graph = Graph()
    for path in paths:
        graph.parse(as_turtle(path), format='turtle')
    return graph


def document(graph):
    handle = tempfile.NamedTemporaryFile('w', suffix='.ttl', delete=False)
    handle.write(graph.serialize(format='turtle'))
    handle.close()
    return handle.name


def dl_violation(label, path):
    """The report this gate would make about `path`, or None if it is in the profile.

    The real document and its negative control are both judged by this one function, so a
    change that stops it reporting violations fails the run on the control instead of
    passing silently on a corpus that happens to be clean.
    """
    report = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False).name
    run = robot('validate-profile', '--profile', 'DL', '--input', path, '--output', report)
    text = pathlib.Path(report).read_text()
    if run.returncode == 0 and 'Ontology and imports closure in profile' in text:
        return None
    return f'{label}: not in the OWL 2 DL profile\n{text.strip()[:2000]}'


def consistent(path):
    output = tempfile.NamedTemporaryFile('w', suffix='.owl', delete=False).name
    run = robot('merge', '--input', path, 'reason', '--reasoner', 'HermiT', '--output', output)
    return run.returncode == 0, (run.stdout + run.stderr)


def subjects_of(graph, predicate):
    return sorted(set(s for s, _, _ in graph.triples((None, URIRef(predicate), None))
                      if isinstance(s, URIRef)))


def pairs_of(graph, predicate):
    """Named subject/object pairs, sorted: a control picked from an unordered store would
    differ between runs, and a control that differs between runs is not a control."""
    return sorted((s, o) for s, _, o in graph.triples((None, URIRef(predicate), None))
                  if isinstance(s, URIRef) and isinstance(o, URIRef))


def sulo_controls(graph):
    """Contradictions reachable only through triples the merged document actually asserts.

    Each names the SULO axiom shape it exercises. Together they establish that the named
    disjointness, the n-ary disjoint union, the functional data property and the universal
    restriction are all live in the document the reasoner is given -- the last of these being
    the class expression the in-process check cannot reach.

    The graphs do not all use the same part predicate: the PRO/SOLID graphs state
    `sulo:hasPart` and the bounded graphs state `sulo:hasDirectPart`, so the part controls
    take whichever the document asserts rather than assuming one.
    """
    valued = subjects_of(graph, S + 'hasValue')
    part_predicate, pairs = None, []
    for candidate in ('hasDirectPart', 'hasPart'):
        pairs = pairs_of(graph, S + candidate)
        if pairs:
            part_predicate = candidate
            break
    controls = []
    if valued:
        # sulo:hasValue has domain sulo:InformationObject, which is disjoint from sulo:Quality.
        controls.append(('a datum reached through sulo:hasValue is also a sulo:Quality',
                         [(valued[0], RDF.type, URIRef(S + 'Quality'))]))
        # sulo:hasValue is functional, so two distinct literals cannot both hold.
        controls.append(('a datum carries a second, different sulo:hasValue literal',
                         [(valued[0], URIRef(S + 'hasValue'), Literal('negative-control'))]))
    if part_predicate:
        whole, part = pairs[0]
        # sulo:Process is restricted to forall hasPart.Process and sulo:Object is disjoint
        # from it, so an object part of a process is a contradiction stated only as a class
        # expression. hasDirectPart is a subproperty of hasPart.
        controls.append((f'a whole reached through sulo:{part_predicate} is a process with an '
                         f'object part', [(whole, RDF.type, URIRef(S + 'Process')),
                                          (part, RDF.type, URIRef(S + 'Object'))]))
        controls.append((f'a whole reached through sulo:{part_predicate} is both a time '
                         f'instant and a time interval',
                         [(whole, RDF.type, URIRef(S + 'TimeInstant')),
                          (whole, RDF.type, URIRef(S + 'TimeInterval'))]))
    return controls


def stack_controls(graph):
    """The stack carries no instances, so its control is a probe individual, not graph content.

    What it has to establish is different too: not that an instance graph reached the
    reasoner, but that each application profile's subclass axioms reach SULO's disjointness
    through the profile rather than around it. Typing a probe as a profile's own start and
    end descriptor is unsatisfiable only if `bt:StartDescriptor` is read as a subclass of
    `sulo:StartTime`, `bt:EndDescriptor` as a subclass of `sulo:EndTime`, and those two as
    disjoint -- one axiom from the profile, one from the profile, one from the core.
    """
    probe = URIRef('urn:negative-control:probe')
    bt = 'https://example.org/trajectory/bounded/'
    return [('a probe typed as both a bounded start and a bounded end descriptor',
             [(probe, RDF.type, URIRef(bt + 'StartDescriptor')),
              (probe, RDF.type, URIRef(bt + 'EndDescriptor'))])]


def core_controls(graph):
    """The standalone core's own disjointness, reached through its own property assertions."""
    intervals = subjects_of(graph, F + 'hasBeginning')
    if not intervals:
        return []
    # hasBeginning has domain TimeInterval, and TimePoint is disjoint from TimeInterval.
    return [('an interval reached through hasBeginning is also a TimePoint',
             [(intervals[0], RDF.type, URIRef(F + 'TimePoint'))])]


def with_triples(graph, triples):
    copy = Graph()
    for triple in graph:
        copy.add(triple)
    for triple in triples:
        copy.add(triple)
    return copy


def anchor(graph):
    """Any named subject of the document, chosen in a stable order."""
    named = sorted(set(s for s in graph.subjects() if isinstance(s, URIRef)))
    return named[0] if named else None


def check(label, paths, controls):
    problems = []
    graph = merge(paths)
    path = document(graph)
    controlled = 0
    violation = dl_violation(label, path)
    if violation:
        problems.append(violation)
    # Every committed document is in the profile, so nothing here would notice if the profile
    # check stopped working. Asserting a type in a class the document never declares takes it
    # out of OWL 2 DL, and the same function has to report it.
    else:
        subject = anchor(graph)
        if subject is None:
            problems.append(f'{label}: no named subject to anchor the profile control on')
        else:
            controlled += 1
            control = dl_violation(label, document(with_triples(
                graph, [(subject, RDF.type, URIRef('urn:negative-control:undeclared-class'))])))
            if control is None:
                problems.append(f'{label}: a type in an undeclared class was accepted as OWL 2 '
                                f'DL; the profile check is not reporting violations')
    ok, log = consistent(path)
    if not ok:
        problems.append(f'{label}: inconsistent under HermiT\n{log.strip()[:2000]}')
    built = controls(graph)
    if not built:
        problems.append(f'{label}: no negative control could be built from this document, so '
                        f'the consistency check above is unverified')
    for description, triples in built:
        controlled += 1
        detected, _ = consistent(document(with_triples(graph, triples)))
        if detected:
            problems.append(f'{label}: negative control NOT detected ({description}); the '
                            f'consistency check is vacuous and proves nothing')
    if not problems:
        print(f'owl: {label} is OWL 2 DL and consistent; {controlled} negative '
              f'control(s) detected')
    return problems


def main():
    if not pathlib.Path(ROBOT).exists():
        print(f'::error::ROBOT jar not found at {ROBOT}; set ROBOT_JAR')
        return 1
    problems = []
    for name, found, expected in (('profiles', len(PROFILES), EXPECTED['profiles']),
                                  ('sulo graphs', len(SULO_GRAPHS), EXPECTED['sulo graphs']),
                                  ('core graphs', len(CORE_GRAPHS), EXPECTED['core graphs'])):
        if found != expected:
            problems.append(f'expected {expected} {name}, found {found}; update EXPECTED if '
                            f'this is intended, so coverage cannot shrink unnoticed')
    missing = [p for p in [SULO, CORE] + PROFILES + SULO_GRAPHS + CORE_GRAPHS
               if not (ROOT / p).exists()]
    if missing:
        problems.append('missing input file(s): ' + ', '.join(missing))
    if problems:
        for problem in problems:
            print(f'::error::{problem}')
        return 1

    stack = [SULO] + PROFILES
    problems += check('the SULO stack and every application profile', stack, stack_controls)
    for instance in SULO_GRAPHS:
        problems += check(instance, stack + [instance], sulo_controls)
    for instance in CORE_GRAPHS:
        problems += check(instance, [CORE, instance], core_controls)

    for problem in problems:
        print(f'::error::{problem}')
    print(f'owl: checked {1 + len(SULO_GRAPHS) + len(CORE_GRAPHS)} merged documents, '
          f'{len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
