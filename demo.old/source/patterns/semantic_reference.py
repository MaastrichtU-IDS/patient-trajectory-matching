"""Independent least-model evaluator for the admitted, non-generating Horn rules.

No OWL parser, reasoner, OFN serializer or temporal compiler is called here.
Inputs are validated by semantic_support. This is a finite-fragment checker,
not a general OWL reasoner.
"""


def evaluate(model):
    members = {(a['individual'], a['class']) for a in model['class_assertions']}
    edges = {(a['subject'], a['property'], a['object']) for a in model['property_assertions']}
    evidence = {pair: {'kind': 'asserted', 'individual': pair[0], 'class': pair[1]}
                for pair in members}

    def witnesses(individual, expression):
        if 'class' in expression:
            pair = individual, expression['class']
            return [list(pair)] if pair in members else None
        if 'all' in expression:
            found = [witnesses(individual, term) for term in expression['all']]
            return None if any(w is None for w in found) else [p for w in found for p in w]
        restriction = expression['some']
        for subject, prop, obj in sorted(edges):
            if subject == individual and prop == restriction['property']:
                found = witnesses(obj, restriction['filler'])
                if found is not None:
                    return [{'property_assertion': [subject, prop, obj]}, *found]
        return None

    derivations = []
    changed = True
    while changed:
        changed = False
        for rule in model['rules']:
            for individual in model['individuals']:
                pair = individual, rule['then']
                if pair in members:
                    continue
                premises = witnesses(individual, rule['if'])
                if premises is not None:
                    members.add(pair)
                    proof = {'kind': 'rule', 'rule_id': rule['id'], 'individual': individual,
                             'class': rule['then'], 'premises': premises}
                    evidence[pair] = proof
                    derivations.append(proof)
                    changed = True
    clashes = [{'individual': ind, 'disjoint_id': d['id'], 'classes': d['classes']}
               for d in model['disjoint'] for ind in model['individuals']
               if all((ind, cls) in members for cls in d['classes'])]
    return {'consistent': not clashes, 'clashes': clashes,
            'memberships': {cls: sorted(ind for ind, kind in members if kind == cls)
                            for cls in model['classes']},
            'derivations': derivations,
            'assertions': [evidence[pair] for pair in sorted(evidence)
                           if evidence[pair]['kind'] == 'asserted']}
