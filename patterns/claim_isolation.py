"""Check one constructive model of claim descriptions plus pinned SULO.

This is a model checker for this fixed encoding/import closure, not an OWL reasoner.
A satisfying model with Process empty witnesses non-entailment of occurrence.
"""
from collections import defaultdict
from functools import lru_cache
import hashlib
from pathlib import Path
from itertools import combinations
from rdflib import Graph, Literal, RDF, RDFS, OWL, URIRef, XSD
from rdflib.collection import Collection
from . import exact_intervals as ei
from . import claim_rdf as cr

PIN = '433c83980ff2c37f241f8150d5525b84caeed9fa87825ff30341716c93c25a5e'
S = ei.S


def check(graph, *, local=False):
    cr.decode(graph, fields=cr.LOCAL_FIELDS if local else cr.FIELDS)  # Closed description vocabulary and exactly one value per datum.
    path = ei.ROOT / 'ontology/vendor/sulo-0.2.14.ttl'
    ei.require(hashlib.sha256(path.read_bytes()).hexdigest() == PIN, 'UNREVIEWED_SULO_PIN')
    base = Graph().parse(path)
    profile = Graph().parse(ei.ROOT / 'ontology/claim-description-profile.ttl')
    expected = {cr.CP[t] for t in cr.TYPES} | {cr.CP['Field_' + k] for k in cr.FIELDS}
    expected_profile = {(c, RDF.type, OWL.Class) for c in expected} | {
        (c, RDFS.subClassOf, S.InformationObject) for c in expected}
    ei.require(set(profile) == expected_profile, 'UNREVIEWED_CLAIM_CLASS_MODULE')
    ontology = base + profile
    extension_hashes = {}
    if local:
        extension_path = ei.ROOT / 'ontology/local-claim-description-profile.ttl'
        extension = Graph().parse(extension_path)
        extra_classes = {cr.CP['Field_' + k] for k in cr.LOCAL_FIELDS - cr.FIELDS}
        expected_extension = {(c, RDF.type, OWL.Class) for c in extra_classes} | {
            (c, RDFS.subClassOf, S.InformationObject) for c in extra_classes}
        ei.require(set(extension) == expected_extension, 'UNREVIEWED_LOCAL_CLAIM_CLASS_MODULE')
        ontology += extension
        expected |= extra_classes
        extension_hashes['ontology/local-claim-description-profile.ttl'] = hashlib.sha256(extension_path.read_bytes()).hexdigest()
    ei.require(not list(ontology.triples((None, OWL.imports, None))), 'UNREVIEWED_IMPORT')
    domain = frozenset(graph.subjects())
    ei.require(bool(domain), 'EMPTY_MODEL_DOMAIN')
    ei.require({o for _, _, o in graph if isinstance(o, URIRef) and o not in expected} <= domain,
               'CLAIM_MODEL_UNBOUND_OBJECT')
    classes = set(ontology.subjects(RDF.type, OWL.Class))
    object_properties = set(ontology.subjects(RDF.type, OWL.ObjectProperty))
    data_properties = set(ontology.subjects(RDF.type, OWL.DatatypeProperty))
    annotations = set(ontology.subjects(RDF.type, OWL.AnnotationProperty)) | {RDFS.label, RDFS.comment}
    values = defaultdict(set)
    for s, p, o in graph:
        if p == S.hasValue: values[s].add(o)
    edges = {p: frozenset((s, o) for s, _, o in graph.triples((None, p, None)))
             for p in (S.hasDirectPart, S.refersTo)}
    counts = defaultdict(int)

    def items(node):
        return list(Collection(ontology, node))

    def inverse(rel):
        kind, pairs = rel
        return (kind, frozenset((b, a) for a, b in pairs)) if kind == 'edges' else rel

    @lru_cache(None)
    def relation(prop):
        if not isinstance(prop, URIRef):
            target = ontology.value(prop, OWL.inverseOf)
            ei.require(target is not None, 'UNSUPPORTED_MODEL_PROPERTY_EXPRESSION')
            return inverse(relation(target))
        if prop in (S.hasPart, S.isPartOf, S.contains, S.isIn, OWL.topObjectProperty):
            return 'universal', frozenset()
        if prop in (S.hasFeature, S.isFeatureOf):
            return 'identity', frozenset()
        if prop in edges: return 'edges', edges[prop]
        if prop == S.isDirectPartOf: return inverse(relation(S.hasDirectPart))
        if prop == S.isReferredToIn: return inverse(relation(S.refersTo))
        ei.require(prop in object_properties or prop == OWL.bottomObjectProperty, 'UNSUPPORTED_MODEL_PROPERTY')
        return 'edges', frozenset()

    @lru_cache(None)
    def targets(prop, individual):
        kind, pairs = relation(prop)
        if kind == 'universal': return domain
        if kind == 'identity': return frozenset((individual,))
        return frozenset(b for a, b in pairs if a == individual)

    def data_member(value, expression):
        if expression == RDFS.Literal: return isinstance(value, Literal)
        if expression in (XSD.dateTime, XSD.dateTimeStamp): return value.datatype == expression
        if expression == XSD.decimal: return value.datatype in (XSD.decimal, XSD.integer)
        union = ontology.value(expression, OWL.unionOf)
        if union is not None: return any(data_member(value, e) for e in items(union))
        datatype = ontology.value(expression, OWL.onDatatype)
        ei.require(datatype == XSD.decimal, 'UNSUPPORTED_MODEL_DATARANGE')
        restrictions = items(ontology.value(expression, OWL.withRestrictions))
        for r in restrictions:
            ei.require(set(ontology.predicate_objects(r)) == {(XSD.minInclusive, Literal('0.0', datatype=XSD.decimal))},
                       'UNSUPPORTED_MODEL_FACET')
        return data_member(value, datatype) and value.toPython() >= 0

    @lru_cache(None)
    def extent(expression):
        if expression in (OWL.Thing, S.Object, S.Feature, S.InformationObject): return domain
        if expression == OWL.Nothing: return frozenset()
        if expression in expected: return frozenset(graph.subjects(RDF.type, expression))
        if isinstance(expression, URIRef):
            ei.require(expression in classes, 'UNSUPPORTED_MODEL_CLASS')
            return frozenset()
        for predicate in (OWL.unionOf, OWL.intersectionOf):
            ls = ontology.value(expression, predicate)
            if ls is not None:
                sets = [extent(e) for e in items(ls)]
                return frozenset().union(*sets) if predicate == OWL.unionOf else domain.intersection(*sets)
        complement = ontology.value(expression, OWL.complementOf)
        if complement is not None: return domain - extent(complement)
        prop = ontology.value(expression, OWL.onProperty)
        ei.require(prop is not None, 'UNSUPPORTED_MODEL_EXPRESSION')
        some, every = ontology.value(expression, OWL.someValuesFrom), ontology.value(expression, OWL.allValuesFrom)
        ei.require((some is None) != (every is None), 'UNSUPPORTED_MODEL_RESTRICTION')
        filler = some if some is not None else every
        if prop in data_properties:
            def holds(a):
                truth = [data_member(v, filler) for v in values[a]]
                return any(truth) if some is not None else all(truth)
        else:
            wanted = extent(filler)
            def holds(a):
                ts = targets(prop, a)
                return bool(ts & wanted) if some is not None else ts <= wanted
        return frozenset(a for a in domain if holds(a))

    def subrelation(left, right):
        lk, lp = left; rk, rp = right
        if rk == 'universal' or left == right: return True
        if lk == 'edges' and not lp: return True
        if lk == 'edges' and rk == 'identity': return all(a == b for a, b in lp)
        if lk == rk == 'edges': return lp <= rp
        return False

    declarations = {OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
                    OWL.Ontology, OWL.Restriction, RDFS.Datatype}
    structural = {RDF.first, RDF.rest, OWL.onProperty, OWL.someValuesFrom, OWL.allValuesFrom,
                  OWL.unionOf, OWL.intersectionOf, OWL.complementOf, OWL.onDatatype,
                  OWL.withRestrictions, XSD.minInclusive, OWL.members}
    metadata = {OWL.versionIRI, OWL.versionInfo, OWL.priorVersion, OWL.backwardCompatibleWith}
    # The byte pin fixes the complete closure. Every logical axiom below is checked;
    # structural triples are interpreted as parts of the fixed class/property expressions.
    for a, p, b in ontology:
        if p in annotations | structural | metadata: continue
        if p == RDFS.subClassOf:
            ok = extent(a) <= extent(b)
        elif p == RDFS.subPropertyOf:
            ok = subrelation(relation(a), relation(b))
        elif p == OWL.inverseOf:
            ok = relation(a) == inverse(relation(b))
            if not isinstance(a, URIRef):
                ei.require(ok, 'CLAIM_COUNTERMODEL_INVERSE_EXPRESSION')
                continue  # Anonymous inverse expression inside the PRO chain.
        elif p in (RDFS.domain, RDFS.range):
            if a in data_properties:
                ok = all(s in extent(b) for s, vs in values.items() if vs) if p == RDFS.domain else False
            else:
                rel = relation(a) if p == RDFS.domain else inverse(relation(a))
                relevant = domain if rel[0] in ('universal', 'identity') else {x for x, _ in rel[1]}
                ok = relevant <= extent(b)
        elif p == OWL.disjointWith:
            ok = not (extent(a) & extent(b))
        elif p == OWL.disjointUnionOf:
            sets = [extent(x) for x in items(b)]
            ok = extent(a) == frozenset().union(*sets) and all(not x & y for x, y in combinations(sets, 2))
        elif p == OWL.propertyChainAxiom:
            # Pinned PRO chain has an empty first relation in this model.
            chain = items(b)
            ok = bool(chain) and relation(chain[0]) == ('edges', frozenset())
            for item in chain: relation(item)
        elif p == RDF.type:
            if b in declarations: continue
            if b == OWL.AllDisjointClasses:
                sets = [extent(x) for x in items(ontology.value(a, OWL.members))]
                ok = all(not x & y for x, y in combinations(sets, 2))
            elif b == OWL.FunctionalProperty and a == S.hasValue:
                ok = all(len(vs) <= 1 for vs in values.values())
            elif b == OWL.ReflexiveProperty:
                ok = all(x in targets(a, x) for x in domain)
            elif b == OWL.TransitiveProperty:
                rel = relation(a)
                ok = rel[0] in ('universal', 'identity') or not rel[1]
            else:
                raise ei.ContractError('UNSUPPORTED_MODEL_AXIOM_TYPE:' + str(b))
        else:
            raise ei.ContractError('UNSUPPORTED_MODEL_AXIOM:' + str(p))
        ei.require(ok, 'CLAIM_COUNTERMODEL_FAILED:' + str(p))
        counts[str(p)] += 1
    # Every ABox triple is satisfied by construction; verify rather than assume.
    for a, p, b in graph:
        if p == RDF.type: ok = a in extent(b)
        elif p == S.hasValue: ok = b in values[a]
        else: ok = b in targets(p, a)
        ei.require(ok, 'CLAIM_COUNTERMODEL_ABOX_FAILED')
    return {'status': 'VERIFIED_EMPTY_PROCESS_MODEL', 'sulo_sha256': PIN,
            'profile_sha256': hashlib.sha256((ei.ROOT / 'ontology/claim-description-profile.ttl').read_bytes()).hexdigest(),
            'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'extension_sha256': extension_hashes,
            'domain_size': len(domain), 'assertion_triples_checked': len(graph),
            'logical_axioms_checked': sum(counts.values()), 'axiom_counts': dict(sorted(counts.items())),
            'process_extension_size': 0, 'temporal_extension_size': 0,
            'scope': 'claim descriptions plus pinned SULO and the declared claim class modules only; not the accepted assertion view',
            'general_owl_reasoner': False}
