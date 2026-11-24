import copy
import unittest

from mojum.archive_retention import (
    RetentionError,
    plan_retention,
)

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64
H4 = "4" * 64


def fixture():
    return {
        "schema": "mojum.archive-retention-input/1",
        "consent": "PUBLIC_SYNTHETIC",
        "current_tick": 100,
        "minimum_age_ticks": 20,
        "minimum_retained_segments": 2,
        "minimum_failure_domains": 2,
        "restore_anchor": {"sequence": 20, "head_sha256": H2},
        "restore_head": H4,
        "segments": [
            {"segment_id": "seg-001", "start_sequence": 1, "end_sequence": 10,
             "sealed_tick": 10, "content_sha256": "a" * 64,
             "previous_head_sha256": H0, "head_sha256": H1,
             "failure_domains": ["disk-a", "disk-b"], "verified": True},
            {"segment_id": "seg-002", "start_sequence": 11, "end_sequence": 20,
             "sealed_tick": 20, "content_sha256": "b" * 64,
             "previous_head_sha256": H1, "head_sha256": H2,
             "failure_domains": ["disk-a", "disk-b"], "verified": True},
            {"segment_id": "seg-003", "start_sequence": 21, "end_sequence": 30,
             "sealed_tick": 30, "content_sha256": "c" * 64,
             "previous_head_sha256": H2, "head_sha256": H3,
             "failure_domains": ["disk-a", "disk-b"], "verified": True},
            {"segment_id": "seg-004", "start_sequence": 31, "end_sequence": 40,
             "sealed_tick": 40, "content_sha256": "d" * 64,
             "previous_head_sha256": H3, "head_sha256": H4,
             "failure_domains": ["disk-a", "disk-b"], "verified": True},
        ],
    }


class ArchiveRetentionTests(unittest.TestCase):
    def test_old_prefix_is_only_operator_review_candidate(self):
        plan = plan_retention(fixture())
        self.assertEqual(plan.eligible_segment_ids, ("seg-001", "seg-002"))
        self.assertEqual([d.action for d in plan.decisions],
                         ["ELIGIBLE_FOR_OPERATOR_REVIEW",
                          "ELIGIBLE_FOR_OPERATOR_REVIEW", "KEEP", "KEEP"])
        self.assertIn("required_after_restore_anchor", plan.decisions[2].reasons)

    def test_minimum_recent_segment_floor_is_preserved(self):
        raw = fixture()
        raw["minimum_retained_segments"] = 3
        plan = plan_retention(raw)
        self.assertEqual(plan.eligible_segment_ids, ("seg-001",))
        self.assertIn("minimum_recent_segment_floor", plan.decisions[1].reasons)

    def test_unverified_segment_is_never_eligible(self):
        raw = fixture()
        raw["segments"][0]["verified"] = False
        plan = plan_retention(raw)
        self.assertNotIn("seg-001", plan.eligible_segment_ids)
        self.assertIn("unverified_segment", plan.decisions[0].reasons)

    def test_insufficient_failure_domains_are_kept(self):
        raw = fixture()
        raw["segments"][0]["failure_domains"] = ["disk-a"]
        plan = plan_retention(raw)
        self.assertNotIn("seg-001", plan.eligible_segment_ids)
        self.assertIn("insufficient_failure_domains", plan.decisions[0].reasons)

    def test_policy_cannot_allow_single_failure_domain(self):
        raw = fixture()
        raw["minimum_failure_domains"] = 1
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_minimum_age_is_enforced(self):
        raw = fixture()
        raw["segments"][0]["sealed_tick"] = 95
        plan = plan_retention(raw)
        self.assertNotIn("seg-001", plan.eligible_segment_ids)
        self.assertIn("minimum_age_not_reached", plan.decisions[0].reasons)

    def test_deterministic_digest_and_domain_order(self):
        raw = fixture()
        first = plan_retention(raw)
        changed_order = copy.deepcopy(raw)
        changed_order["segments"][0]["failure_domains"].reverse()
        second = plan_retention(changed_order)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first, second)

    def test_input_is_not_mutated(self):
        raw = fixture()
        before = copy.deepcopy(raw)
        plan_retention(raw)
        self.assertEqual(raw, before)

    def test_rejects_boolean_as_integer(self):
        raw = fixture()
        raw["current_tick"] = True
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_rejects_gap_and_chain_discontinuity(self):
        raw = fixture()
        raw["segments"][1]["start_sequence"] = 12
        with self.assertRaises(RetentionError):
            plan_retention(raw)
        raw = fixture()
        raw["segments"][2]["previous_head_sha256"] = H1
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_rejects_wrong_restore_head_or_anchor(self):
        raw = fixture()
        raw["restore_head"] = H3
        with self.assertRaises(RetentionError):
            plan_retention(raw)
        raw = fixture()
        raw["restore_anchor"]["head_sha256"] = H1
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_rejects_duplicate_ids_and_domains(self):
        raw = fixture()
        raw["segments"][1]["segment_id"] = "seg-001"
        with self.assertRaises(RetentionError):
            plan_retention(raw)
        raw = fixture()
        raw["segments"][0]["failure_domains"] = ["disk-a", "disk-a"]
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_requires_explicit_synthetic_consent_and_exact_shapes(self):
        raw = fixture()
        raw["consent"] = "PRIVATE"
        with self.assertRaises(RetentionError):
            plan_retention(raw)
        raw = fixture()
        raw["extra"] = True
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_empty_inventory_requires_genesis_anchor(self):
        raw = fixture()
        raw["segments"] = []
        raw["restore_anchor"] = {"sequence": 0, "head_sha256": H0}
        raw["restore_head"] = H0
        plan = plan_retention(raw)
        self.assertEqual(plan.decisions, ())
        self.assertEqual(plan.eligible_segment_ids, ())

    def test_unverified_restore_anchor_is_rejected(self):
        raw = fixture()
        raw["segments"][1]["verified"] = False
        with self.assertRaises(RetentionError):
            plan_retention(raw)

    def test_genesis_anchor_keeps_all_segments_required_for_restore(self):
        raw = fixture()
        raw["restore_anchor"] = {"sequence": 0, "head_sha256": H0}
        plan = plan_retention(raw)
        self.assertEqual(plan.eligible_segment_ids, ())
        self.assertTrue(all(d.action == "KEEP" for d in plan.decisions))

    def test_empty_inventory_rejects_non_genesis_head(self):
        raw = fixture()
        raw["segments"] = []
        raw["restore_anchor"] = {"sequence": 0, "head_sha256": H0}
        raw["restore_head"] = H4
        with self.assertRaises(RetentionError):
            plan_retention(raw)


if __name__ == "__main__":
    unittest.main()
