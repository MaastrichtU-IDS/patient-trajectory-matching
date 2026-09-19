"""Review checks for the specification's finite examples, not an application parser."""
from fractions import Fraction
from itertools import product
import json
from pathlib import Path
import re

FOLDER = Path(__file__).resolve().parent


def check_links():
    count = 0
    for file in FOLDER.glob('*.md'):
        text = file.read_text()
        targets = re.findall(r'\]\(([^)]+)\)', text) + re.findall(r'^\[[^\]]+\]:\s+(\S+)', text, re.M)
        for target in targets:
            if '://' in target:
                continue
            pathname, _, anchor = target.partition('#')
            dest = (file.parent / pathname).resolve() if pathname else file
            assert dest.exists(), (file.name, target)
            if anchor:
                headings = re.findall(r'^#+\s+(.+)$', dest.read_text(), re.M)
                slugs = [re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings]
                assert anchor in slugs, (file.name, anchor)
            count += 1
    return count


def example_tree():
    text = (FOLDER / 'example.ttq').read_text()
    lex = re.compile(r'\s*(<[^<>\s]+>|"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_-]*|-?[0-9]+(?:\.[0-9]+)?|[()])')
    tokens, position = [], 0
    while position < len(text.rstrip()):
        match = lex.match(text, position)
        assert match, ('lexical error', text[position:position + 30])
        tokens.append(match[1]); position = match.end()
    cursor = 0

    def term():
        nonlocal cursor
        name = tokens[cursor]; cursor += 1
        if cursor < len(tokens) and tokens[cursor] == '(':
            cursor += 1; args = []
            while tokens[cursor] != ')':
                args.append(term())
            cursor += 1
            return name, args
        return name

    tree = term()
    assert cursor == len(tokens)
    return tree


def check_example():
    tree = example_tree()
    arities = {'TemporalDocument':None, 'Snapshot':None, 'OntologyRef':1, 'AdmissionPolicy':1,
               'Clock':3, 'Patient':1, 'Variable':5, 'Scope':3, 'Interval':1, 'Boundary':4,
               'Event':6, 'StateAssertion':8, 'Observation':8, 'TrajectoryQuery':5,
               'Slots':None, 'Slot':3, 'Conditions':None, 'Condition':2, 'Gap':4,
               'Relaxation':None, 'Relaxable':None, 'Option':3, 'Widen':3, 'CoverageQuery':7}
    counts = {}

    def walk(node):
        if not isinstance(node, tuple):
            return
        name, args = node
        assert name in arities, name
        if arities[name] is not None:
            assert len(args) == arities[name], (name, args)
        counts[name] = counts.get(name, 0) + 1
        for item in args:
            walk(item)
    walk(tree)
    assert tree[0] == 'TemporalDocument'
    snapshot = tree[1][0][1]
    assert snapshot[1] == 'GFOBoundaryMicrosecond'
    declarations = snapshot[4:]
    ids = [args[0] for _, args in declarations]
    assert len(ids) == len(set(ids))
    tables = {kind:{args[0]:args for name,args in declarations if name == kind} for kind in
              ('Clock','Variable','Interval','Boundary','Event','StateAssertion','Observation')}
    clocks, variables = tables['Clock'], tables['Variable']
    intervals, boundaries = tables['Interval'], tables['Boundary']
    for v in variables.values():
        assert v[1][0] == 'Scope'
        patient, episode, clock = v[1][1]
        assert clocks[clock][2] == ('Patient', [patient])
        assert int(v[2]) <= int(v[3])
    for b in boundaries.values():
        assert b[1] in ('Left','Right') and b[2] in intervals and b[3] in variables
    ends = {}
    for name in intervals:
        bs = [b for b in boundaries.values() if b[2] == name]
        assert sorted(b[1] for b in bs) == ['Left','Right']
        left = next(b for b in bs if b[1] == 'Left')
        right = next(b for b in bs if b[1] == 'Right')
        assert variables[left[3]][1] == variables[right[3]][1]
        assert int(variables[left[3]][2]) < int(variables[right[3]][3])
        ends[name] = (left,right)
    for e in tables['Event'].values():
        assert e[4] in intervals
        assert variables[ends[e[4]][0][3]][1][1][:2] == e[2:4]
    for kind in ('StateAssertion','Observation'):
        for row in tables[kind].values():
            bs = ends[row[6]] if kind == 'StateAssertion' else [boundaries[row[6]]]
            for b in bs:
                v = variables[b[3]]
                assert v[2] == v[3]
                assert v[1][1] == row[1:4]
    # The contact describes two oriented referents with one chart position.
    a, b = boundaries['lowRight'], boundaries['nextLowLeft']
    assert a[0] != b[0] and a[1] == 'Right' and b[1] == 'Left' and a[3] == b[3]
    # Exactly the two companion OWL memberships required by the query are present.
    owl = (FOLDER/'example.ofn').read_text()
    memberships = set(re.findall(r'ClassAssertion\((<[^>]+>)\s+(<[^>]+>)\)', owl))
    query = next(a for n,a in tree[1][1:] if n == 'TrajectoryQuery')
    slots = query[3][1]
    for _, slot in slots:
        event = tables['Event'][slot[0]]
        assert (slot[2], event[1]) in memberships
    relaxation = next(a for n,a in tree[1][1:] if n == 'Relaxation')
    assert relaxation[1] == query[0]
    condition = query[4][1][0][1]
    assert relaxation[4][1] == [condition[0]]
    change = relaxation[5][1][2][1]
    old = condition[1][1]
    assert change[0] == condition[0] and int(change[1]) <= int(old[2])
    assert int(change[2]) >= int(old[3])
    return counts


def check_semantic_examples():
    minute = 60000000
    lo, hi = 48*minute-minute, 50*minute-minute
    assert lo <= 48*minute < hi <= 50*minute
    assert min(6,10)-max(4,0) == 2
    assert Fraction(0) < Fraction(1,3) < Fraction(2,3) < Fraction(1)
    # Finite coordinate descriptions coexist with an interior dense chart region.
    right, left = (Fraction(15), 'Right'), (Fraction(15), 'Left')
    assert right != left and right[0] == left[0]
    assert all(any(a == b for a in (1,2)) for b in (1,2))
    assert not any(all(a == b for b in (1,2)) for a in (1,2))
    options, worlds = [(0,1),(1,2)], [0,1,2]
    assert all(any(lo <= w <= hi for lo,hi in options) for w in worlds)
    assert not any(all(lo <= w <= hi for w in worlds) for lo,hi in options)
    count = 0
    # Four constant-support cells; uniform choices in unknown cells supply
    # distinguishing completions. This does not enumerate an infinite time model.
    for states in product((True,False,None), repeat=4):
        completions = [v for v in product((True,False),repeat=4)
                       if all(s is None or s == x for s,x in zip(states,v))]
        holds, violated = all(s is True for s in states), any(s is False for s in states)
        assert holds == all(all(v) for v in completions)
        assert violated == all(not all(v) for v in completions)
        assert (not holds and not violated) == (any(all(v) for v in completions) and any(not all(v) for v in completions))
        count += 1
    return count


if __name__ == '__main__':
    print(json.dumps({'local_links_and_anchors':check_links(),
                      'example_constructors':check_example(),
                      'state_completion_cases':check_semantic_examples(),
                      'boundary_and_quantifier_examples':'passed'}, indent=2))
