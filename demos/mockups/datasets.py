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
    'mimic-demo-aggregate': Kind(Policy('approved'), 'aggregate', True, frozenset({'store', 'd_items'}),
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
            return 'ready' if service._store is not None else 'failed'
        if not service.opened and not self._opening:
            return 'idle'
        return service.state()

    def open_in_background(self):
        """Open a MIMIC store now, on its own thread (hosted: so that readiness covers it)."""
        if self.kind == 'mimic-demo-aggregate' and not self._opening:
            self._opening = True
            threading.Thread(target=self.service.status, name=f'open-{self.id}', daemon=True).start()


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
