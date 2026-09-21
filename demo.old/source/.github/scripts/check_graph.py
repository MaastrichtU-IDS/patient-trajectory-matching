"""Regenerated graph must be isomorphic to the committed example graph."""
import sys
from rdflib import Graph
from rdflib.compare import isomorphic, to_isomorphic, graph_diff

generated = Graph().parse('verification/pro-solid-run/graph.ttl', format='turtle')
committed = Graph().parse('examples/pro-solid/graph.ttl', format='turtle')
if isomorphic(generated, committed):
    print(f'graph: isomorphic to committed example ({len(generated)} triples)')
    sys.exit(0)
_, only_generated, only_committed = graph_diff(to_isomorphic(generated), to_isomorphic(committed))
print('::error::regenerated graph differs from examples/pro-solid/graph.ttl')
for label, g in (('only in generated', only_generated), ('only in committed', only_committed)):
    for s, p, o in sorted(g, key=str)[:20]:
        print(f'  {label}: {s} {p} {o}')
sys.exit(1)
