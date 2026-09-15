"""Closed, typed information trees: claim content is never asserted as occurrence."""
import hashlib
import json
from rdflib import Graph, Literal, Namespace, RDF, URIRef, XSD
from . import exact_intervals as ei

CP = Namespace('https://example.org/trajectory/claim/')
DATA = 'https://example.org/trajectory/claim-data/'
S = ei.S
MAX_BYTES = 262144
MAX_NODES = 8192
MAX_TRIPLES = 32768
MAX_DEPTH = 24
# Fixed binding classes encode positions, not new predicates.
FIELDS = frozenset('profile dataset_id snapshot_id clocks claims clock_id origin scope policy '
    'id patient_id episode_id bundle variables events constraints semantic_facts source_id '
    'source_record_id source_sha256 lower_us upper_us source_key source_location source_hash '
    'record_id event_kind status start_var end_var left_var right_var kind local_id '
    'subject object class_iri property_iri event_id'.split())
LOCAL_FIELDS = FIELDS | frozenset(('origin_source_key', 'local_lower', 'local_upper'))
TYPES = ('Document', 'ObjectDescription', 'ArrayDescription', 'StringDatum', 'IntegerDatum',
         'ItemBinding', 'IndexDatum')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def encode(document, *, fields=FIELDS):
    ei.require(len(canonical(document).encode()) <= MAX_BYTES, 'CLAIM_DOCUMENT_LIMIT')
    graph = Graph(); graph.bind('cp', CP); graph.bind('sulo', S)
    root = URIRef(DATA + digest(document))
    count = 0

    def visit(value, node, depth):
        nonlocal count
        count += 1
        ei.require(count <= MAX_NODES and depth <= MAX_DEPTH, 'CLAIM_TREE_LIMIT')
        if type(value) is dict:
            graph.add((node, RDF.type, CP.ObjectDescription))
            for key, item in sorted(value.items()):
                ei.require(key in fields, 'UNSUPPORTED_CLAIM_FIELD:' + key)
                binding = URIRef(str(node) + '/field/' + key)
                target = URIRef(str(binding) + '/value')
                graph.add((node, S.hasDirectPart, binding))
                graph.add((binding, RDF.type, CP['Field_' + key]))
                graph.add((binding, S.refersTo, target))
                visit(item, target, depth + 1)
        elif type(value) is list:
            graph.add((node, RDF.type, CP.ArrayDescription))
            for i, item in enumerate(value):
                binding = URIRef(str(node) + '/item/' + str(i))
                index = URIRef(str(binding) + '/index')
                target = URIRef(str(binding) + '/value')
                graph.add((node, S.hasDirectPart, binding))
                graph.add((binding, RDF.type, CP.ItemBinding))
                graph.add((binding, S.hasDirectPart, index))
                graph.add((index, RDF.type, CP.IndexDatum))
                graph.add((index, S.hasValue, Literal(i, datatype=XSD.integer)))
                graph.add((binding, S.refersTo, target))
                visit(item, target, depth + 1)
        else:
            ei.require(type(value) in (str, int), 'UNSUPPORTED_CLAIM_VALUE')
            cls, datatype = (CP.StringDatum, XSD.string) if type(value) is str else (CP.IntegerDatum, XSD.integer)
            graph.add((node, RDF.type, cls))
            graph.add((node, S.hasValue, Literal(value, datatype=datatype, normalize=False)))
    visit(document, root, 0)
    graph.add((root, RDF.type, CP.Document))
    ei.require(len(graph) <= MAX_TRIPLES, 'CLAIM_GRAPH_LIMIT')
    return graph


def decode(graph, *, fields=FIELDS):
    ei.require(isinstance(graph, Graph) and len(graph) <= MAX_TRIPLES, 'CLAIM_GRAPH_LIMIT')
    ei.require(all(isinstance(s, URIRef) and isinstance(p, URIRef) and
                   isinstance(o, (URIRef, Literal)) for s, p, o in graph), 'CLAIM_GRAPH_NAMED_NODES')
    roots = list(graph.subjects(RDF.type, CP.Document))
    ei.require(len(roots) == 1, 'CLAIM_DOCUMENT_CARDINALITY')
    seen = set()

    def one(node, prop):
        values = list(graph.objects(node, prop))
        ei.require(len(values) == 1, 'CLAIM_FIELD_CARDINALITY')
        return values[0]

    def literal(node, datatype):
        val = one(node, S.hasValue)
        ei.require(isinstance(val, Literal) and val.datatype == datatype and val.language is None,
                   'CLAIM_LITERAL_DATATYPE')
        if datatype == XSD.integer:
            ei.require(type(val.toPython()) is int and str(val) == str(val.toPython()), 'CLAIM_INTEGER_LEXICAL')
            return int(val)
        return str(val)

    def visit(node, depth):
        ei.require(depth <= MAX_DEPTH and node not in seen and len(seen) < MAX_NODES, 'CLAIM_TREE_LIMIT_OR_ALIAS')
        seen.add(node)
        types = set(graph.objects(node, RDF.type)) - {CP.Document}
        ei.require(len(types) == 1, 'CLAIM_NODE_TYPE')
        typ = next(iter(types))
        if typ == CP.ObjectDescription:
            value = {}
            for binding in graph.objects(node, S.hasDirectPart):
                field_type = one(binding, RDF.type)
                key = str(field_type).removeprefix(str(CP) + 'Field_')
                ei.require(key in fields and key not in value, 'CLAIM_FIELD_TYPE_OR_DUPLICATE')
                value[key] = visit(one(binding, S.refersTo), depth + 1)
            return value
        if typ == CP.ArrayDescription:
            items = {}
            for binding in graph.objects(node, S.hasDirectPart):
                ei.require(one(binding, RDF.type) == CP.ItemBinding, 'CLAIM_ITEM_TYPE')
                idx = one(binding, S.hasDirectPart)
                ei.require(one(idx, RDF.type) == CP.IndexDatum, 'CLAIM_INDEX_TYPE')
                i = literal(idx, XSD.integer)
                ei.require(i >= 0 and i not in items, 'CLAIM_INDEX_DUPLICATE')
                items[i] = visit(one(binding, S.refersTo), depth + 1)
            ei.require(sorted(items) == list(range(len(items))), 'CLAIM_INDEX_GAP')
            return [items[i] for i in range(len(items))]
        ei.require(typ in (CP.StringDatum, CP.IntegerDatum), 'CLAIM_NODE_TYPE')
        return literal(node, XSD.string if typ == CP.StringDatum else XSD.integer)

    result = visit(roots[0], 0)
    # Fix both the content-addressed identities and the complete allowed graph.
    ei.require(set(encode(result, fields=fields)) == set(graph), 'CLAIM_GRAPH_EXTRA_OR_CHANGED_TRIPLES')
    return result
