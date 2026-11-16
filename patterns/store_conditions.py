"""The one entry point for the store's match requests with conditions beyond `execution_store`'s own (D38).

The request language grows in modules no build loads (D34's build reuse): rises and falls in
`store_conditions_relative` (profile 1.2, D36), gene steps in `store_conditions_gene` (D38), and further profiles in
their own modules. A caller calls this module only (`store_parity.py`, the trajectory page's server, the tools), which
routes each request by its conditions' profile to the one module that answers it; that module hands what it does not
answer on unchanged, so the answer to any other request is byte-identical to calling the module directly.

Routing, by `conditions['profile']`:
- `execution-store-conditions-gene-1.0`: `store_conditions_gene`;
- every other request (none, 1.0, 1.1, 1.2): `store_conditions_relative`, which hands all but 1.2 to
  `execution_store`.

Each module is imported when a request needs it.
"""
from __future__ import annotations

from . import execution_store as es

GENE_PROFILE = 'execution-store-conditions-gene-1.0'
# D39 (Track B) adds its route here: its profile, and its module in `_module` below.


def _module(conditions):
    profile = conditions.get('profile') if isinstance(conditions, dict) else None
    if profile == GENE_PROFILE:
        from . import store_conditions_gene as module
        return module
    # D39: `if profile == CODED_PROFILE: from . import store_conditions_coded as module; return module`
    from . import store_conditions_relative as module
    return module


def profiles():
    """Every conditions profile a request through this module may carry."""
    from . import store_conditions_relative as rc
    return [*rc.CONDITIONS_PROFILES, GENE_PROFILE]


def match(store, query, windows, detail='full', chunk=es.CHUNK_PATIENTS, workers=1, conditions=None):
    """The store's answer to a match request, by the module its profile names."""
    return _module(conditions).match(store, query, windows, detail, chunk, workers, conditions)


def candidates(store, query, conditions=None):
    """The patients who could match, by the module the request's profile names."""
    return _module(conditions).candidates(store, query, conditions)


def match_context(store, query, windows, conditions=None):
    """What a result depends on, and its digest, by the module the request's profile names."""
    return _module(conditions).match_context(store, query, windows, conditions)
