"""Claim descriptions and explicit acceptance over patient-local calendar bounds."""
from rdflib import Graph
from . import claim_projection as cp
from . import claim_rdf as cr
from . import exact_intervals as ei
from . import patient_local as local

PROFILE = 'patient-local-claim-projection-1.0'
RECORD_PROFILE = 'patient-local-record-claim-projection-1.0'
STORE_PROFILE = 'patient-local-claim-store-1.0'
RECORD_STORE_PROFILE = 'patient-local-record-claim-store-1.0'
SCHEMA = ei.ROOT / 'schemas/local-claim-store.schema.json'
RECORD_SCHEMA = ei.ROOT / 'schemas/local-record-claim-store.schema.json'
FILES = ('patterns/local_claim_projection.py', 'schemas/local-claim-store.schema.json',
         'schemas/local-record-claim-store.schema.json', 'ontology/local-claim-description-profile.ttl',
         'patterns/bounded_rdf.py', 'ontology/bounded-rdf-profile.ttl') + local.FILES


def validate_store(store):
    ei.require(isinstance(store, dict) and store.get('profile') in (STORE_PROFILE, RECORD_STORE_PROFILE),
               'UNSUPPORTED_LOCAL_CLAIM_STORE_PROFILE')
    schema = RECORD_SCHEMA if store['profile'] == RECORD_STORE_PROFILE else SCHEMA
    def check_variable(variable):
        lower = local.parse_local(variable['local_lower'])
        upper = local.parse_local(variable['local_upper'])
        ei.require(lower <= upper, 'REVERSED_LOCAL_CLAIM_BOUNDS')
    return cp._validate_store(store, schema,
        lambda clock: local.parse_local(clock['origin'], origin=True), check_variable)


def select(store, policy, semantic_policy):
    validate_store(store)
    return cp._select_validated(store, policy, semantic_policy)


def _prepare(source):
    # The declared local-label bounds survive export and an independently checked
    # raw-label/numeric-coordinate RDF ingestion route before semantic projection.
    return local.prepare_graph(local.export_source(source))


def execute(store, policy, semantic_policy, query, *, timeout_seconds=20):
    if isinstance(store, Graph): store = cr.decode(store, fields=cr.LOCAL_FIELDS)
    ei.require(isinstance(store, dict) and store.get('profile') in (STORE_PROFILE, RECORD_STORE_PROFILE),
               'UNSUPPORTED_LOCAL_CLAIM_STORE_PROFILE')
    recorded = store['profile'] == RECORD_STORE_PROFILE
    interpretation = {'time_domain': local.TIME_DOMAIN,
        'gap_semantics': 'local_calendar_coordinate_difference', 'physical_elapsed_time_verified': False,
        'time_semantics': 'recorded_source_intervals' if recorded else 'declared_bounded_local_intervals'}
    route = cp.ProjectionRoute(
        RECORD_PROFILE if recorded else PROFILE,
        local.RECORD_PROFILE if recorded else local.PROFILE_ID,
        validate_store, _prepare, local.execute_semantic, local.SEMANTIC_QUERY_PROFILE,
        description_fields=cr.LOCAL_FIELDS, local_isolation=True, interpretation=interpretation, files=FILES)
    result = cp._execute(store, policy, semantic_policy, query, route=route, timeout_seconds=timeout_seconds)
    # Retain the normalized material paired to the semantic module as well as
    # the raw selected source. Never replace an original bound with its number.
    result['normalized_source'] = local.normalize(result['source']) if result['source'] is not None else None
    return result


def main():
    cp._main(execute, 'local-claim-projection', __doc__)


if __name__ == '__main__':
    main()
