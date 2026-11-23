"""The datasets the mockup server serves, each with its store, its access policy and what it may return (D50).

A registry entry:

    {"id": "synthetic-paediatric-rare", "kind": "synthetic-trajectory", "title": "...",
     "access": "public" | "approved" | {"role": "dataset:<id>:read"}, "results": "per_patient" | "aggregate",
     ...kind-specific settings}

Kinds, each served by an existing backend whose own rules stay in force:

| kind | backend | floor policy | results |
|---|---|---|---|
| `synthetic-trajectory` | `store_trajectory.TrajectoryStore` (D37): only the generator's cohort, checked by content | public | per patient |
| `mimic-demo-aggregate` | `store_query.StoreService` (D29): only the open demo's 100 patients, by content; counts only | approved | aggregate |
| `mimic-iv` | none yet | `{"role": "dataset:mimic-iv:read"}` | per patient |

- An entry may raise its kind's floor (a synthetic dataset behind a role, say), never lower it, so no configuration
  can make the MIMIC demo public.
- `results` must be what the backend produces: the field describes the backend, it does not change it.
- `mimic-iv` is refused outright (DATASET_KIND_NOT_HOSTABLE) until the storage and backup access review and a backend
  exist (docs/hosting-plan.md, section 7). The tier is reserved so the policy and the realm role are in place.
- Kind-specific settings: `cohort` (synthetic, optional: another cohort file of the generator's dataset) and `store`
  (required), `d_items` (optional) for the MIMIC demo. Unknown fields are refused.
- `fetch` (MIMIC demo only, hosting): `"physionet:mimic-iv-demo-2.2"`, the one source known. The server downloads,
  checks and builds the store at start into `store`, which must end in `/build/store` (mimic_demo_fetch.py). Such a
  dataset is optional: it answers 503 until built, and never holds readiness back.

Without a registry file the server builds the registry from its flags, as before D50: the synthetic cohort, and the
MIMIC demo when a store is named.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import threading

from access import Policy

ID = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')
FETCH_SOURCE = 'physionet:mimic-iv-demo-2.2'  # the open demo, fetched and built at start (mimic_demo_fetch.py)
SYNTHETIC_ID = 'synthetic-paediatric-rare'
MIMIC_DEMO_ID = 'mimic-iv-demo'


@dataclass(frozen=True)
class Kind:
    floor: Policy
    results: str
    hostable: bool
    settings: frozenset
    required: frozenset = frozenset()
    page: str | None = None


KINDS = {
    'synthetic-trajectory': Kind(Policy('public'), 'per_patient', True, frozenset({'cohort'}), page='trajectory/'),
    'mimic-demo-aggregate': Kind(Policy('approved'), 'aggregate', True, frozenset({'store', 'd_items', 'fetch'}),
                                 frozenset({'store'}), page='store-query/'),
    'mimic-iv': Kind(Policy('role', 'dataset:mimic-iv:read'), 'per_patient', False, frozenset()),
}
COMMON = frozenset({'id', 'kind', 'title', 'access', 'results'})


class RegistryError(ValueError):
    """A registry the server refuses to start with, by name."""


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    title: str
    policy: Policy
    results: str
    settings: dict


def parse(document):
    """Entries from a registry document, or RegistryError naming the first problem."""
    if not isinstance(document, dict) or set(document) != {'datasets'} or not isinstance(document['datasets'], list):
        raise RegistryError('REGISTRY_MUST_BE_AN_OBJECT_WITH_DATASETS')
    entries, seen = [], set()
    for raw in document['datasets']:
        if not isinstance(raw, dict):
            raise RegistryError('DATASET_MUST_BE_AN_OBJECT')
        kind = KINDS.get(raw.get('kind'))
        if kind is None:
            raise RegistryError('UNKNOWN_DATASET_KIND')
        if not kind.hostable:
            raise RegistryError('DATASET_KIND_NOT_HOSTABLE')
        unknown = set(raw) - COMMON - kind.settings
        if unknown or not COMMON <= set(raw) or not kind.required <= set(raw):
            raise RegistryError('DATASET_FIELDS')
        if not isinstance(raw['id'], str) or not ID.fullmatch(raw['id']) or raw['id'] in seen:
            raise RegistryError('DATASET_ID_INVALID_OR_REPEATED')
        if not isinstance(raw['title'], str) or not 0 < len(raw['title']) <= 200:
            raise RegistryError('DATASET_TITLE')
        try:
            policy = Policy.parse(raw['access'])
        except ValueError as error:
            raise RegistryError(str(error)) from None
        if not policy.at_least(kind.floor):
            raise RegistryError('ACCESS_BELOW_THE_KINDS_FLOOR')
        if raw['results'] != kind.results:
            raise RegistryError('RESULTS_NOT_WHAT_THE_BACKEND_PRODUCES')
        settings = {k: raw[k] for k in kind.settings if k in raw}
        if not all(isinstance(v, str) and v for v in settings.values()):
            raise RegistryError('DATASET_SETTING_MUST_BE_A_PATH')
        if 'fetch' in settings:
            # Fetched at start (mimic_demo_fetch.py): the one source known, built where `store` says.
            if settings['fetch'] != FETCH_SOURCE:
                raise RegistryError('UNKNOWN_FETCH_SOURCE')
            if not settings['store'].endswith('/build/store'):
                raise RegistryError('FETCHED_STORE_MUST_END_IN_BUILD_STORE')
        seen.add(raw['id'])
        entries.append(Entry(raw['id'], raw['kind'], raw['title'], policy, raw['results'], settings))
    return entries


def load(path):
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise RegistryError('REGISTRY_UNREADABLE') from None
    return parse(document)


def from_flags(trajectory_store=True, mimic_store=None):
    """The registry before D50, from the server's flags: the synthetic cohort, and the MIMIC demo if a store is named."""
    entries = []
    if trajectory_store:
        entries.append(Entry(SYNTHETIC_ID, 'synthetic-trajectory',
                             'Synthetic paediatric rare-disease cohort (48 invented patients)', Policy('public'),
                             'per_patient', {}))
    if mimic_store:
        entries.append(Entry(MIMIC_DEMO_ID, 'mimic-demo-aggregate', 'MIMIC-IV clinical database demo (open, 100 patients)',
                             Policy('approved'), 'aggregate', {'store': str(mimic_store)}))
    return entries


class Dataset:
    """A registry entry with its backend."""

    def __init__(self, entry, service):
        self.entry = entry
        self.service = service
        self._opening = False
        # Hosted: the matches' child processes (match_worker), set by server.py. The synthetic dataset's start with
        # it; the MIMIC demo's (IsolatedStoreQuery) by `on_open`, once the server's own service has opened the store.
        self.matcher = None
        self.on_open = None
        # A fetched dataset (setting `fetch`): None until started, then 'pending', 'ready' or 'failed' (fetch_error).
        self.fetch_state = None
        self.fetch_error = None

    def fetch_gate(self):
        """None once a fetched store may be opened; else the code to answer meanwhile (the store service's gate)."""
        if self.fetch_state in (None, 'ready'):
            return None
        return self.fetch_error or 'STORE_BUILDING'

    def _opened(self):
        """After a background open: start what needs an open, checked store (the hosted MIMIC matcher)."""
        if self.on_open is not None and self.service.store is not None:
            self.on_open(self)

    @property
    def optional(self):
        """A fetched dataset never holds readiness back: if its source is unreachable, the others still serve."""
        return 'fetch' in self.entry.settings

    def start_fetch(self, fetch_and_build, on_event, attempts=(0, 60, 600)):
        """Fetch and build the store on a background thread (`attempts`: seconds to wait before each try), then open
        it through the store service, whose content gate still applies."""
        import threading
        import time
        self.fetch_state = 'pending'

        def run():
            dest = Path(self.entry.settings['store']).parents[1]
            for wait in attempts:
                time.sleep(wait)
                started = time.monotonic()
                try:
                    result = fetch_and_build(dest)
                except Exception as error:  # noqa: BLE001 - a named failure, or any other, leaves no store
                    from mimic_demo_fetch import FETCH_ERRORS
                    self.fetch_error = str(error) if str(error) in FETCH_ERRORS else 'MIMIC_DEMO_FETCH_FAILED'
                    on_event('mimic_demo_fetch', dataset=self.id, ok=False, error=self.fetch_error,
                             seconds=round(time.monotonic() - started, 1))
                    continue
                how = result[1] if isinstance(result, tuple) else 'built'  # 'reused': a verified store on the volume
                on_event('mimic_demo_fetch', dataset=self.id, ok=True, how=how,
                         seconds=round(time.monotonic() - started, 1))
                self.fetch_state, self.fetch_error = 'ready', None
                self.service.status()  # open now, for the first time (the gate was closed): the content gate decides
                self._opened()
                return
            self.fetch_state = 'failed'
        threading.Thread(target=run, name=f'fetch-{self.id}', daemon=True).start()

    id = property(lambda self: self.entry.id)
    kind = property(lambda self: self.entry.kind)
    policy = property(lambda self: self.entry.policy)

    def endpoints(self):
        base = f'/api/datasets/{self.id}/'
        names = ('status', 'catalogue', 'match') if self.kind == 'mimic-demo-aggregate' else ('status', 'match')
        return {name: base + name for name in names}

    def describe(self):
        """What the dataset list says about it: nothing about its data."""
        return {'id': self.id, 'kind': self.kind, 'title': self.entry.title, 'results': self.entry.results,
                'access': self.policy.access, 'page': KINDS[self.kind].page, 'endpoints': self.endpoints(),
                'ready': self.state() == 'ready'}

    def state(self):
        """'ready', 'pending', 'failed', or 'idle' (a MIMIC store not yet asked for, which opens at first use
        locally), without waiting and without opening anything."""
        service = self.service
        if self.kind == 'synthetic-trajectory':
            # Read from TrajectoryStore's build state directly: store_trajectory.py is bound by digest in the D45
            # latency verdict, so it is left unchanged rather than given a method of its own.
            if not service._ready.is_set():
                return 'pending'
            if service._store is None:
                return 'failed'
            return self.matcher.state() if self.matcher is not None else 'ready'
        if self.fetch_state in ('pending', 'failed'):
            return self.fetch_state
        if not service.opened and not self._opening:
            return 'idle'
        state = service.state()
        if state == 'ready' and self.on_open is not None:  # hosted: ready once its match workers are
            return self.matcher.state() if self.matcher is not None else 'pending'
        return state

    def open_in_background(self):
        """Open a MIMIC store now, on its own thread (hosted: so that readiness covers it)."""
        if self.kind == 'mimic-demo-aggregate' and not self._opening:
            self._opening = True

            def run():
                self.service.status()
                self._opened()
            threading.Thread(target=run, name=f'open-{self.id}', daemon=True).start()


class Registry:
    def __init__(self, datasets):
        self.datasets = list(datasets)
        self.by_id = {d.id: d for d in self.datasets}

    def get(self, dataset_id):
        return self.by_id.get(dataset_id)

    def first(self, kind):
        return next((d for d in self.datasets if d.kind == kind), None)

    def visible(self, principal):
        return [d for d in self.datasets if d.policy.permits(principal)]

    def non_public(self):
        return [d for d in self.datasets if d.policy.access != 'public']
