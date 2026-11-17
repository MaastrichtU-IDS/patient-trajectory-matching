"""The one entry point for the store's match requests with conditions beyond `execution_store`'s own (D38).

The request language grows in modules no build loads (D34's build reuse): rises and falls in
`store_conditions_relative` (profile 1.2, D36), gene steps in `store_conditions_gene` (D38), and further profiles in
their own modules. A caller calls this module only (`store_parity.py`, the trajectory page's server, the tools), which
routes each request by its conditions' profile to the one module that answers it; that module hands what it does not
answer on unchanged, so the answer to any other request is byte-identical to calling the module directly.

Routing, by `conditions['profile']`:
- `execution-store-conditions-gene-1.0`: `store_conditions_gene`;
- `execution-store-conditions-gene-coded-1.0` (gene steps under coded only, D42): `store_conditions_gene`, which runs
  D39's hooks from `store_conditions_coded` with the kinds the request leaves out;
- `execution-store-conditions-1.3` (coded only: letter mentions left out of every step, D39): `store_conditions_coded`;
- every other request (none, 1.0, 1.1, 1.2): `store_conditions_relative`, which hands all but 1.2 to
  `execution_store`.

Each module is imported when a request needs it.

After routing, one hook (D43): a request whose conditions give a step a count gets the routed module's answer through
`store_conditions_counts.post_pass`, which decides, where `count_narrowed_v1` can, the results D32's two-pass rule
leaves `unresolved` with STEP_CONDITION_UNCERTAIN, and binds the rule in the context (`match_context` likewise). Every
other request is the routed module's answer, untouched. Callers that skip this module keep D32's rule.
"""
from __future__ import annotations

from . import execution_store as es

GENE_PROFILE = 'execution-store-conditions-gene-1.0'
CODED_PROFILE = 'execution-store-conditions-1.3'  # D39
GENE_CODED_PROFILE = 'execution-store-conditions-gene-coded-1.0'  # D42


def _module(conditions):
    profile = conditions.get('profile') if isinstance(conditions, dict) else None
    if profile in (GENE_PROFILE, GENE_CODED_PROFILE):
        from . import store_conditions_gene as module
        return module
    if profile == CODED_PROFILE:
        from . import store_conditions_coded as module
        return module
    from . import store_conditions_relative as module
    return module


def profiles():
    """Every conditions profile a request through this module may carry."""
    from . import store_conditions_relative as rc
    return [*rc.CONDITIONS_PROFILES, GENE_PROFILE, CODED_PROFILE, GENE_CODED_PROFILE]


def _counts(conditions):
    """D43's post-pass module when the request gives a step a count, else None."""
    from . import store_conditions_counts as counts
    return counts if counts.has_counts(conditions) else None


def match(store, query, windows, detail='full', chunk=es.CHUNK_PATIENTS, workers=1, conditions=None):
    """The store's answer to a match request, by the module its profile names (with D43's post-pass for counts)."""
    out = _module(conditions).match(store, query, windows, detail, chunk, workers, conditions)
    counts = _counts(conditions)  # D43
    return out if counts is None else counts.post_pass(store, query, windows, chunk, conditions, out)


def candidates(store, query, conditions=None):
    """The patients who could match, by the module the request's profile names."""
    return _module(conditions).candidates(store, query, conditions)


def match_context(store, query, windows, conditions=None):
    """What a result depends on, and its digest, by the module the request's profile names (D43: and the count rule)."""
    context = _module(conditions).match_context(store, query, windows, conditions)
    counts = _counts(conditions)  # D43
    return context if counts is None else counts.bind_context(context[0])
