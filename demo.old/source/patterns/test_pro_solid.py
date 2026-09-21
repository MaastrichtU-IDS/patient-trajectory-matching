"""Acceptance checks with fixed expectations and adversarial mutations."""
import json
import platform
import unittest
from decimal import Decimal

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, OWL, RDF, XSD

from patterns.pro_solid import (ROOT, S, EX, DATA, PROFILE, ContractError, build_graph,
                               digest, instant, materialize, project)
from reference_oracle import evaluate


class PatternAcceptance(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((ROOT / 'examples/pro-solid/source-rows.json').read_text())
        self.manifest = json.loads((ROOT / 'examples/pro-solid/manifest.json').read_text())
        self.pattern = json.loads((ROOT / 'examples/exemplar.pattern.json').read_text())
        self.taxonomy = json.loads((ROOT / 'ontology/toy-taxonomy.json').read_text())
        self.graph = build_graph(self.rows)

    def projected(self, graph=None):
        return project(self.graph if graph is None else graph, self.manifest)

    def match(self, graph=None):
        case, _ = self.projected(graph)
        return evaluate(case, self.pattern, self.taxonomy)

    def reject(self, code):
        with self.assertRaisesRegex(ContractError, code):
            self.projected()

    def test_exact_normalized_trajectory_and_existing_event_schema(self):
        case, evidence = self.projected()
        events = {e['event_id']: e for e in case['events']}
        self.assertEqual(Decimal(events['B1']['value']), Decimal('1'))
        self.assertEqual(events['B1']['original_value'], '10.0')
        self.assertEqual(events['B1']['original_unit'], 'mg/L')
        self.assertEqual(events['B1']['time']['start_min_us'], -86400000000)
        self.assertEqual(events['E1']['time']['start_min_us'], -604800000000)
        self.assertEqual(events['F1']['time']['start_min_us'], 0)
        self.assertEqual(self.match()['accepted_as'], 'EXACT')
        self.assertEqual(self.match()['total_cost'], '0')
        self.assertEqual(evidence['bindings']['B1']['unit_factor'], '0.1')
        schema = json.loads((ROOT / 'schemas/contracts.schema.json').read_text())
        validator = Draft202012Validator({**schema, '$ref': '#/$defs/Event'})
        for event in case['events']:
            validator.validate(event)

    def test_pro_chain_is_derived_without_losing_role_binding(self):
        self.assertNotIn((DATA['event-B1'], S.hasParticipant, DATA['person-P1']), self.graph)
        closure = materialize(self.graph)
        self.assertIn((DATA['event-B1'], S.hasParticipant, DATA['person-P1']), closure)
        self.assertEqual(self.projected()[1]['bindings']['B1']['patient_role'], str(DATA['B1-patient-role']))

    def test_inverse_feature_encoding(self):
        self.graph.remove((DATA['B1-patient-role'], S.isFeatureOf, DATA['person-P1']))
        self.graph.add((DATA['person-P1'], S.hasFeature, DATA['B1-patient-role']))
        self.assertEqual(self.match()['accepted_as'], 'EXACT')

    def test_care_provider_does_not_become_patient(self):
        for triple in [(DATA['event-B1'], S.hasParticipant, DATA.providerRole),
                       (DATA.providerRole, RDF.type, EX.CareProviderRole),
                       (DATA.providerRole, S.isFeatureOf, DATA.provider),
                       (DATA.provider, RDF.type, EX.Person)]:
            self.graph.add(triple)
        self.assertEqual(self.projected()[1]['bindings']['B1']['patient_bearer'], str(DATA['person-P1']))

    def test_generic_participation_is_insufficient(self):
        self.graph.remove((DATA['event-B1'], S.hasParticipant, DATA['B1-patient-role']))
        self.graph.add((DATA['event-B1'], S.hasParticipant, DATA['person-P1']))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_wrong_role_type(self):
        self.graph.set((DATA['B1-patient-role'], RDF.type, EX.CareProviderRole))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_multiple_patient_roles_even_for_same_bearer(self):
        for triple in [(DATA['event-B1'], S.hasParticipant, DATA.otherRole),
                       (DATA.otherRole, RDF.type, EX.PatientRole),
                       (DATA.otherRole, S.isFeatureOf, DATA['person-P1'])]:
            self.graph.add(triple)
        self.reject('SHACL_PROFILE_FAILURE')

    def test_multiple_role_bearers(self):
        self.graph.add((DATA['B1-patient-role'], S.isFeatureOf, DATA.otherPerson))
        self.graph.add((DATA.otherPerson, RDF.type, EX.Person))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_role_reuse_across_processes(self):
        self.graph.set((DATA['event-F1'], S.hasParticipant, DATA['B1-patient-role']))
        self.graph.add((DATA['event-F1'], S.hasParticipant, DATA['F1-result-role']))
        self.reject('ROLE_REUSED_ACROSS_PROCESSES')

    def test_patient_shortcut_rejected(self):
        self.graph.add((DATA['event-B1'], EX.hasPatient, DATA['person-P1']))
        self.reject('UNSUPPORTED_OBJECT_PROPERTY')

    def test_specialized_literal_property_rejected(self):
        self.graph.add((DATA['person-P1'], EX.age, Literal(50)))
        self.reject('SOLID_LITERAL_PROPERTY')

    def test_literal_directly_on_person_rejected(self):
        self.graph.add((DATA['person-P1'], S.hasValue, Literal('P1')))
        self.reject('SOLID_INFORMATION_OBJECT')

    def test_multiple_literal_values_rejected(self):
        self.graph.add((DATA['B1-result'], S.hasValue, Literal('11.0', datatype=XSD.decimal)))
        self.reject('SOLID_VALUE_CARDINALITY')

    def test_missing_value_rejected(self):
        self.graph.remove((DATA['B1-result'], S.hasValue, None))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_missing_unit_rejected(self):
        self.graph.remove((DATA['B1-result'], S.hasPart, None))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_multiple_units_rejected(self):
        self.graph.add((DATA['B1-result'], S.hasPart, EX.MilligramPerDecilitre))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_wrong_unit_dimension_rejected(self):
        self.graph.set((DATA['B1-result'], S.hasPart, EX.Second))
        self.reject('UNSUPPORTED_UNIT_OR_DIMENSION')

    def test_negative_concentration_rejected(self):
        self.graph.set((DATA['B1-result'], S.hasValue, Literal('-1', datatype=XSD.decimal)))
        self.reject('SHACL_PROFILE_FAILURE')

    def test_unit_conversion_preserves_long_decimal(self):
        self.rows[0]['value'] = '10.123456789012345678901234567890123456789'
        case, _ = self.projected(build_graph(self.rows))
        value = next(e['value'] for e in case['events'] if e['event_id'] == 'B1')
        self.assertEqual(value, '1.0123456789012345678901234567890123456789')

    def test_timezone_equivalence(self):
        self.assertEqual(instant('2024-01-07T01:00:00+01:00'), instant('2024-01-07T00:00:00Z'))

    def test_unzoned_datetime_not_silently_utc(self):
        self.graph.set((DATA['B1-time'], S.hasValue,
                        Literal('2024-01-07T00:00:00', datatype=XSD.dateTime)))
        self.reject('UNSUPPORTED_OR_UNZONED_TIME')

    def test_unknown_timezone_marker_rejected(self):
        with self.assertRaisesRegex(ContractError, 'UNKNOWN_TIMEZONE_OFFSET'):
            instant('2024-01-07T00:00:00-00:00')

    def test_rdf_loading_preserves_unknown_timezone_for_rejection(self):
        self.graph.set((DATA['B1-time'], S.hasValue,
                        Literal('2024-01-07T00:00:00-00:00', datatype=XSD.dateTime, normalize=False)))
        self.graph = Graph().parse(data=self.graph.serialize(format='turtle'), format='turtle')
        self.reject('UNKNOWN_TIMEZONE_OFFSET')

    def test_submicrosecond_time_rejected(self):
        with self.assertRaisesRegex(ContractError, 'UNSUPPORTED_OR_UNZONED_TIME'):
            instant('2024-01-07T00:00:00.1234567Z')

    def test_wrong_measurement_subject_rejected(self):
        self.graph.set((DATA['B1-quality'], S.isFeatureOf, DATA.otherPerson))
        self.reject('MEASUREMENT_SUBJECT_MISMATCH')

    def test_same_patient_bearer_required_across_trajectory(self):
        self.rows[2]['patient_id'] = 'P2'
        result = self.match(build_graph(self.rows))
        self.assertEqual(result['accepted_as'], 'NONE')

    def test_identical_observation_values_remain_distinct_records(self):
        extra = {**self.rows[2], 'event_id': 'F2', 'record_id': 'record-F2'}
        case, evidence = self.projected(build_graph(self.rows + [extra]))
        self.assertEqual(len(case['events']), 4)
        self.assertNotEqual(evidence['bindings']['F1']['result_datum'], evidence['bindings']['F2']['result_datum'])
        self.assertEqual(self.match(build_graph(self.rows + [extra]))['binding']['followup'], 'F1')

    def test_subclass_entailment_is_exact(self):
        self.rows[1]['source_code'] = 'ex:DrugAChild'
        result = self.match(build_graph(self.rows))
        self.assertEqual((result['accepted_as'], result['total_cost']), ('EXACT', '0'))

    def test_reviewed_semantic_relaxation_has_cost(self):
        self.rows[1]['source_code'] = 'ex:DrugB'
        result = self.match(build_graph(self.rows))
        self.assertEqual(result['exact_status'], 'FAIL')
        self.assertEqual((result['accepted_as'], result['total_cost']), ('RELAXED', '1'))
        self.assertEqual(result['relaxed_constraint_ids'], ['exposure'])

    def test_semantic_relaxation_can_be_disabled(self):
        self.rows[1]['source_code'] = 'ex:DrugB'
        self.pattern['budget']['max_total_cost'] = '0'
        self.assertEqual(self.match(build_graph(self.rows))['accepted_as'], 'NONE')

    def test_not_given_is_not_ingested_as_occurrence(self):
        self.rows[1]['status'] = 'not_given'
        with self.assertRaisesRegex(ContractError, 'UNSUPPORTED_RECORD_STATUS'):
            build_graph(self.rows)

    def test_status_tampering_rejected(self):
        self.graph.set((DATA['E1-status'], S.hasValue, Literal('not_given', datatype=XSD.string)))
        self.reject('PROCESS_STATUS_MISMATCH')

    def test_duplicate_event_ids_rejected(self):
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_EVENT_IDENTIFIER'):
            build_graph(self.rows + [self.rows[0]])

    def test_duplicate_record_ids_rejected(self):
        self.rows[2]['record_id'] = self.rows[0]['record_id']
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_RECORD_IDENTIFIER'):
            self.projected(build_graph(self.rows))

    def test_graph_roundtrip_preserves_evidence_and_values(self):
        reloaded = Graph().parse(data=self.graph.serialize(format='turtle'), format='turtle')
        self.assertEqual(self.projected(), self.projected(reloaded))

    def test_source_row_hash_and_snapshot_retained(self):
        case, evidence = self.projected()
        b = next(e for e in case['events'] if e['event_id'] == 'B1')
        expected = digest(json.dumps(self.rows[0], sort_keys=True, separators=(',', ':')))
        self.assertEqual(b['provenance']['source_record_sha256'], expected)
        self.assertEqual(evidence['snapshot_id'], self.manifest['snapshot_id'])

    def test_changed_value_changes_evidence_identifier(self):
        previous = self.projected()[1]['bindings']['B1']['assertion_id']
        self.graph.set((DATA['B1-result'], S.hasValue, Literal('11', datatype=XSD.decimal)))
        self.assertNotEqual(previous, self.projected()[1]['bindings']['B1']['assertion_id'])

    def test_incomplete_source_scope_remains_unresolved(self):
        self.manifest['source_search_complete'] = False
        self.assertEqual(self.match()['accepted_as'], 'UNRESOLVED')

    def test_extension_declares_no_specialized_properties(self):
        profile = Graph().parse(PROFILE)
        self.assertFalse(list(profile.subjects(RDF.type, OWL.ObjectProperty)))
        self.assertFalse(list(profile.subjects(RDF.type, OWL.DatatypeProperty)))

    def test_named_sulo_disjointness_is_checked(self):
        self.graph.add((DATA['person-P1'], RDF.type, S.InformationObject))
        self.reject('DISJOINT_UPPER_CLASSES')

    def test_metadata_cannot_be_mistyped_numeric_literal(self):
        self.graph.set((DATA['B1-event_id'], S.hasValue, Literal(1)))
        self.reject('METADATA_STRING_REQUIRED')

    def test_sparql_example_keeps_subject_and_result_context(self):
        query = (ROOT / 'examples/pro-solid/measurement-bindings.rq').read_text()
        rows = list(self.graph.query(query))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.person for row in rows}, {DATA['person-P1']})
        self.assertEqual({row.result for row in rows}, {DATA['B1-result'], DATA['F1-result']})


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PatternAcceptance)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'profile': 'pro-solid-2.4', 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors),
              'passed': result.wasSuccessful(), 'production_readiness_claim': False,
              'python': platform.python_version(),
              'tests': unittest.defaultTestLoader.getTestCaseNames(PatternAcceptance)}
    (ROOT / 'verification/v24-pro-solid-report.json').write_text(json.dumps(report, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
