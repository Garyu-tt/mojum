import unittest

from mojum.qa_lifecycle import LifecycleError, STATES, propose_transition

HEAD = "a" * 40
BASE = "b" * 40

class LifecycleTests(unittest.TestCase):
    def test_state_inventory_and_valid_claim(self):
        self.assertIn("MERGED", STATES)
        result = propose_transition("DISCOVERED", "CLAIMED", reason="worker accepted", evidence={"worker_id": "worker-1"})
        self.assertEqual(result.current, "CLAIMED")
        self.assertEqual(len(result.digest), 64)

    def test_review_requires_id(self):
        with self.assertRaises(LifecycleError):
            propose_transition("CLAIMED", "REVIEWING", reason="review", evidence={})

    def test_ci_must_match_exact_head(self):
        with self.assertRaises(LifecycleError):
            propose_transition("REVIEWING", "TESTING", reason="run CI", head_sha=HEAD, evidence={"ci_head_sha": BASE})

    def test_testing_exact_head(self):
        result = propose_transition("REVIEWING", "TESTING", reason="run CI", head_sha=HEAD, evidence={"ci_head_sha": HEAD})
        self.assertEqual(result.current, "TESTING")

    def test_ready_requires_review_ci_and_base(self):
        with self.assertRaises(LifecycleError):
            propose_transition("TESTING", "READY", reason="looks good", head_sha=HEAD,
                               evidence={"ci_head_sha": HEAD, "review_id": "r1"})
        result = propose_transition("TESTING", "READY", reason="gates recorded", head_sha=HEAD,
                                    evidence={"ci_head_sha": HEAD, "review_id": "r1", "base_sha": BASE})
        self.assertEqual(result.current, "READY")

    def test_merged_requires_explicit_release_receipt(self):
        receipt = {"schema": "mojum.release-receipt/1", "head_sha": HEAD, "base_sha": BASE,
                   "review_id": "review-1", "ci_passed": True, "human_authorized": True}
        result = propose_transition("READY", "MERGED", reason="external merge observed",
                                    head_sha=HEAD, evidence={"release_receipt": receipt})
        self.assertEqual(result.current, "MERGED")

    def test_merged_rejects_model_only_or_missing_authority(self):
        receipt = {"schema": "mojum.release-receipt/1", "head_sha": HEAD, "base_sha": BASE,
                   "review_id": "review-1", "ci_passed": True, "human_authorized": False}
        with self.assertRaises(LifecycleError):
            propose_transition("READY", "MERGED", reason="model says ship", head_sha=HEAD,
                               evidence={"release_receipt": receipt})

    def test_no_direct_discovered_to_merged(self):
        with self.assertRaises(LifecycleError):
            propose_transition("DISCOVERED", "MERGED", reason="skip gates", head_sha=HEAD,
                               evidence={"release_receipt": {}})

    def test_terminal_state(self):
        with self.assertRaises(LifecycleError):
            propose_transition("MERGED", "REVIEWING", reason="reopen", evidence={"review_id": "r2"})

    def test_blocked_permission_requires_code(self):
        with self.assertRaises(LifecycleError):
            propose_transition("CLAIMED", "BLOCKED_PERMISSION", reason="403", evidence={})

    def test_bool_is_not_integer_head_or_count(self):
        with self.assertRaises(LifecycleError):
            propose_transition("REVIEWING", "TESTING", reason="run", head_sha=True,
                               evidence={"ci_head_sha": HEAD})

    def test_invalid_edge(self):
        with self.assertRaises(LifecycleError):
            propose_transition("FIXING", "MERGED", reason="skip retest", head_sha=HEAD,
                               evidence={"release_receipt": {}})

    def test_digest_is_deterministic(self):
        a = propose_transition("DISCOVERED", "CLAIMED", reason="claim", evidence={"worker_id": "w1"})
        b = propose_transition("DISCOVERED", "CLAIMED", reason="claim", evidence={"worker_id": "w1"})
        self.assertEqual(a.digest, b.digest)

    def test_input_is_detached(self):
        evidence = {"worker_id": "w1"}
        result = propose_transition("DISCOVERED", "CLAIMED", reason="claim", evidence=evidence)
        evidence["worker_id"] = "tampered"
        self.assertEqual(result.evidence["worker_id"], "w1")

    def test_rejects_unknown_state_and_unbounded_reason(self):
        with self.assertRaises(LifecycleError):
            propose_transition("UNKNOWN", "CLAIMED", reason="x", evidence={"worker_id": "w"})
        with self.assertRaises(LifecycleError):
            propose_transition("DISCOVERED", "CLAIMED", reason="x" * 513, evidence={"worker_id": "w"})

if __name__ == "__main__":
    unittest.main()
