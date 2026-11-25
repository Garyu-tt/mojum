import unittest

from mojum.independent_review_gate import evaluate_review_evidence

HEAD = "a" * 40
BASE = "b" * 40
REQUIRED = ["Aquarium 3.11", "Motor Gate 3.11", "Mesh 3.11"]


def evidence():
    return {
        "pr_number": 42,
        "target_branch": "main",
        "draft": False,
        "author_login": "worker",
        "last_pusher_login": "worker",
        "reviewer_login": "reviewer",
        "review_state": "APPROVED",
        "reviewed_head_sha": HEAD,
        "current_head_sha": HEAD,
        "reviewed_base_sha": BASE,
        "current_base_sha": BASE,
        "required_jobs": list(REQUIRED),
        "job_runs": [
            {"name": name, "head_sha": HEAD, "status": "completed", "conclusion": "success"}
            for name in REQUIRED
        ],
        "unresolved_threads": 0,
    }


class IndependentReviewGateTests(unittest.TestCase):
    def test_complete_independent_exact_head_evidence_is_ready(self):
        result = evaluate_review_evidence(evidence())
        self.assertEqual(result.decision, "REVIEW_READY")
        self.assertEqual(result.successful_jobs, tuple(REQUIRED))
        self.assertEqual(len(result.receipt_sha256), 64)

    def test_author_cannot_approve_own_pr(self):
        data = evidence()
        data["reviewer_login"] = "WORKER"
        result = evaluate_review_evidence(data)
        self.assertEqual(result.decision, "HOLD")
        self.assertIn("reviewer_is_author", result.reasons)
        self.assertIn("reviewer_is_last_pusher", result.reasons)

    def test_last_pusher_is_separately_checked(self):
        data = evidence()
        data["last_pusher_login"] = "reviewer"
        result = evaluate_review_evidence(data)
        self.assertIn("reviewer_is_last_pusher", result.reasons)

    def test_stale_head_and_base_block(self):
        data = evidence()
        data["current_head_sha"] = "c" * 40
        data["current_base_sha"] = "d" * 40
        result = evaluate_review_evidence(data)
        self.assertIn("reviewed_head_stale", result.reasons)
        self.assertIn("reviewed_base_stale", result.reasons)
        self.assertIn("required_job_stale:Aquarium 3.11", result.reasons)

    def test_pending_failed_cancelled_and_skipped_block(self):
        for conclusion in ("failure", "cancelled", "skipped", None):
            with self.subTest(conclusion=conclusion):
                data = evidence()
                data["job_runs"][0]["conclusion"] = conclusion
                self.assertEqual(evaluate_review_evidence(data).decision, "HOLD")
        data = evidence()
        data["job_runs"][0]["status"] = "in_progress"
        self.assertEqual(evaluate_review_evidence(data).decision, "HOLD")

    def test_missing_required_job_blocks(self):
        data = evidence()
        data["job_runs"].pop()
        result = evaluate_review_evidence(data)
        self.assertIn("required_job_missing:Mesh 3.11", result.reasons)

    def test_duplicate_job_attempts_are_rejected_as_ambiguous(self):
        data = evidence()
        data["job_runs"].append(dict(data["job_runs"][0]))
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)

    def test_draft_wrong_target_and_threads_block(self):
        data = evidence()
        data["draft"] = True
        data["target_branch"] = "release"
        data["unresolved_threads"] = 1
        result = evaluate_review_evidence(data)
        self.assertEqual(result.decision, "HOLD")
        self.assertIn("draft_pr", result.reasons)
        self.assertIn("wrong_target_branch", result.reasons)
        self.assertIn("unresolved_review_threads", result.reasons)

    def test_bool_is_not_integer(self):
        data = evidence()
        data["pr_number"] = True
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)
        data = evidence()
        data["unresolved_threads"] = False
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)

    def test_exact_native_containers_and_fields_required(self):
        data = evidence()
        data["job_runs"] = tuple(data["job_runs"])
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)
        data = evidence()
        data["unexpected"] = "ignored?"
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)

    def test_malformed_sha_and_duplicate_required_names_rejected(self):
        data = evidence()
        data["current_head_sha"] = "A" * 40
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)
        data = evidence()
        data["required_jobs"] = ["same", "same"]
        with self.assertRaises(ValueError):
            evaluate_review_evidence(data)

    def test_receipt_is_deterministic_and_sensitive_to_decision(self):
        first = evaluate_review_evidence(evidence())
        second = evaluate_review_evidence(evidence())
        self.assertEqual(first.receipt_sha256, second.receipt_sha256)
        data = evidence()
        data["review_state"] = "COMMENTED"
        third = evaluate_review_evidence(data)
        self.assertNotEqual(first.receipt_sha256, third.receipt_sha256)


if __name__ == "__main__":
    unittest.main()
