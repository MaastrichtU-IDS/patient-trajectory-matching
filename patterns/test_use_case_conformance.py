"""Synthetic acceptance cases replayed by the joint evidence selector."""
from copy import deepcopy
import json
import unittest

from . import joint_evidence
from . import exact_intervals as ei
from . import interval_cohort


def gen01_fixture(name):
    return json.loads((ei.ROOT / "examples/joint-evidence/gen-01" / name).read_text())


def run_gen01(request_name):
    return joint_evidence.execute(
        gen01_fixture("archive.json"),
        gen01_fixture(request_name),
        gen01_fixture("policy.json"),
        gen01_fixture("query.json"),
    )


def selected_fact_ids(result):
    return {fact["id"] for fact in result["selection"]["selected_semantic_facts"]}


def observation_payload(result):
    return next(
        fact for fact in result["selection"]["selected_semantic_facts"]
        if fact["id"] == "variant_observation"
    )


def coh01_fixture(name):
    return json.loads((ei.ROOT / "examples/interval-cohort" / name).read_text())


def run_coh01(query_name, *, engine="indexed"):
    source = coh01_fixture("source-rows.json")
    manifest = coh01_fixture("manifest.json")
    snapshot = interval_cohort.prepare(interval_cohort.build_graph(source), manifest)
    return interval_cohort.execute(snapshot, coh01_fixture(query_name), engine=engine)


def cohort_semantics(result):
    return {key: value for key, value in result.items() if key != "execution"}


def expected_removed_patient_ids():
    return ["P2", "P3"]


class UseCaseConformanceTests(unittest.TestCase):
    def test_coh01_refinement_reports_reproducible_membership_delta(self):
        """COH-01 is a constructed process cohort; HPO matching remains a proposed profile extension."""
        broad = run_coh01("coh-01-broad-query.json")
        refined = run_coh01("coh-01-refined-query.json")
        self.assertTrue(set(refined["matched_patient_ids"]) <= set(broad["matched_patient_ids"]))
        self.assertEqual(
            sorted(set(broad["matched_patient_ids"]) - set(refined["matched_patient_ids"])),
            expected_removed_patient_ids(),
        )
        self.assertEqual(broad["matched_patient_ids"], ["P1", "P2", "P3"])
        self.assertEqual(refined["matched_patient_ids"], ["P1"])
        self.assertEqual(broad["evidence"]["context_id"], refined["evidence"]["context_id"])
        self.assertEqual(
            [trajectory["patient_id"] for trajectory in broad["trajectories"]],
            [trajectory["patient_id"] for trajectory in refined["trajectories"]],
        )

        for query_name, result in (("coh-01-broad-query.json", broad),
                                   ("coh-01-refined-query.json", refined)):
            with self.subTest(query=query_name, engine="reference"):
                reference = run_coh01(query_name, engine="reference")
                self.assertEqual(cohort_semantics(result), cohort_semantics(reference))
            self.assertEqual(result["context"]["query"], coh01_fixture(query_name))
            self.assertTrue(result["context_id"])
            for trajectory in result["trajectories"]:
                for match in trajectory["matches"] + trajectory["unresolved_bindings"]:
                    for slot in match["slots"].values():
                        for key in ("process", "patient_role", "patient_bearer", "evidence_id"):
                            self.assertIn(key, slot)
                        evidence = result["evidence"]["bindings"][slot["evidence_id"]]
                        self.assertIn("source_record", evidence)
                        self.assertIn("context_id", evidence)
                        self.assertTrue(evidence["source_record"])
                        self.assertEqual(evidence["context_id"], result["evidence"]["context_id"])

    def test_gen01_pre_release_excludes_later_pathogenicity(self):
        result = run_gen01("before-request.json")
        self.assertEqual(result["status"], "READY")
        self.assertIn("variant_observation", selected_fact_ids(result))
        self.assertNotIn("clingen_r2_pathogenic", selected_fact_ids(result))
        self.assertFalse(result["selection"]["later_evidence_used"])

    def test_gen01_post_release_changes_interpretation_not_observation(self):
        before = run_gen01("before-request.json")
        after = run_gen01("after-request.json")
        self.assertEqual(after["status"], "READY")
        self.assertEqual(observation_payload(before), observation_payload(after))
        self.assertNotIn("clingen_r2_pathogenic", selected_fact_ids(before))
        self.assertIn("clingen_r2_pathogenic", selected_fact_ids(after))
        self.assertFalse(after["selection"]["later_evidence_used"])

    def test_gen01_conflicting_classification_claims_fail_closed(self):
        archive = gen01_fixture("archive.json")
        request = gen01_fixture("after-request.json")
        policy = gen01_fixture("policy.json")
        query = gen01_fixture("query.json")
        r2 = next(assertion for assertion in archive["assertions"] if assertion["id"] == "clingen_release_r2")

        same_id_conflict = deepcopy(r2)
        same_id_conflict["id"] = "clingen_release_r2_conflicting"
        same_id_pathogenicity = next(fact for fact in same_id_conflict["bundle"]["semantic_facts"]
                                      if fact["id"] == "clingen_r2_pathogenic")
        same_id_pathogenicity["class_iri"] = (
            "https://example.org/trajectory/genomics/BenignVariantInterpretation"
        )
        archive["assertions"].append(same_id_conflict)
        archive["entries"].append({
            **next(entry for entry in archive["entries"] if entry["id"] == "clingen_r2_release"),
            "id": "clingen_r2_conflicting_release",
            "assertion_id": same_id_conflict["id"],
            "supersedes": None,
        })
        result = joint_evidence.execute(archive, request, policy, query)
        self.assertEqual(result["status"], "BLOCKED_EVIDENCE")
        self.assertTrue(any(
            blocker.get("row") == "semantic_facts:clingen_r2_pathogenic"
            for blocker in result["selection"]["blockers"]
        ))

        archive = gen01_fixture("archive.json")
        incompatible = deepcopy(r2)
        incompatible["id"] = "clingen_release_r2_benign"
        incompatible_pathogenicity = next(fact for fact in incompatible["bundle"]["semantic_facts"]
                                           if fact["id"] == "clingen_r2_pathogenic")
        incompatible_pathogenicity.update(
            id="clingen_r2_benign",
            class_iri="https://example.org/trajectory/genomics/BenignVariantInterpretation",
        )
        archive["assertions"].append(incompatible)
        archive["entries"].append({
            **next(entry for entry in archive["entries"] if entry["id"] == "clingen_r2_release"),
            "id": "clingen_r2_benign_release",
            "assertion_id": incompatible["id"],
            "supersedes": None,
        })
        result = joint_evidence.execute(archive, request, policy, query)
        self.assertEqual(result["status"], "INCONSISTENT_ONTOLOGY")


if __name__ == "__main__":
    unittest.main()
