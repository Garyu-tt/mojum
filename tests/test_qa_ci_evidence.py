import unittest
from copy import deepcopy
from unittest.mock import patch

from mojum.qa_ci_evidence import EvidenceError, evaluate_ci_evidence, SCHEMA

HEAD = "a" * 40
NOW = "2026-11-23T22:55:00Z"


def fixture():
    return {
        "schema": SCHEMA,
        "complete": True,
        "pr_head_sha": HEAD,
        "required_checks": ["unit / py311", "unit / py312"],
        "observed_at": "2026-11-23T22:54:00Z",
        "checks": [
            {"name": "unit / py311", "head_sha": HEAD, "run_id": 101, "status": "completed", "conclusion": "success", "completed_at": "2026-11-23T22:53:00Z"},
            {"name": "unit / py312", "head_sha": HEAD, "run_id": 102, "status": "completed", "conclusion": "success", "completed_at": "2026-11-23T22:53:30Z"},
        ],
    }


class CIEvidenceTests(unittest.TestCase):
    def test_exact_head_complete_matrix_passes(self):
        result = evaluate_ci_evidence(fixture(), now=NOW)
        self.assertEqual(result.decision, "PASS")
        self.assertEqual(result.successful_checks, 2)
        self.assertEqual(len(result.evidence_sha256), 64)

    def test_digest_is_order_independent(self):
        a = fixture()
        b = fixture()
        b["checks"].reverse()
        b["required_checks"].reverse()
        self.assertEqual(
            evaluate_ci_evidence(a, now=NOW).evidence_sha256,
            evaluate_ci_evidence(b, now=NOW).evidence_sha256,
        )

    def test_non_native_key_rejected_before_hashing(self):
        class HostileKey(str):
            def __init__(self, value):
                self.explode = False
            def __hash__(self):
                if self.explode:
                    raise RuntimeError("hostile hash executed")
                return str.__hash__(self)

        snapshot = fixture()
        key = HostileKey("schema")
        snapshot[key] = snapshot.pop("schema")
        key.explode = True
        with self.assertRaisesRegex(EvidenceError, "keys must be native strings"):
            evaluate_ci_evidence(snapshot, now=NOW)

    def test_incomplete_snapshot_rejected(self):
        x = fixture()
        x["complete"] = False
        with self.assertRaises(EvidenceError):
            evaluate_ci_evidence(x, now=NOW)

    def test_missing_check_rejected(self):
        x = fixture()
        x["checks"].pop()
        with self.assertRaisesRegex(EvidenceError, "missing required"):
            evaluate_ci_evidence(x, now=NOW)

    def test_duplicate_check_rejected(self):
        x = fixture()
        x["checks"].append(deepcopy(x["checks"][0]))
        with self.assertRaisesRegex(EvidenceError, "duplicate check"):
            evaluate_ci_evidence(x, now=NOW)

    def test_unexpected_check_rejected(self):
        x = fixture()
        extra = deepcopy(x["checks"][0])
        extra["name"] = "not-required"
        x["checks"].append(extra)
        with self.assertRaisesRegex(EvidenceError, "unexpected"):
            evaluate_ci_evidence(x, now=NOW)

    def test_stale_head_rejected(self):
        x = fixture()
        x["checks"][0]["head_sha"] = "b" * 40
        with self.assertRaisesRegex(EvidenceError, "different PR head"):
            evaluate_ci_evidence(x, now=NOW)

    def test_pending_job_rejected(self):
        x = fixture()
        x["checks"][0]["status"] = "in_progress"
        with self.assertRaisesRegex(EvidenceError, "not completed"):
            evaluate_ci_evidence(x, now=NOW)

    def test_failure_rejected(self):
        x = fixture()
        x["checks"][0]["conclusion"] = "failure"
        with self.assertRaisesRegex(EvidenceError, "did not succeed"):
            evaluate_ci_evidence(x, now=NOW)

    def test_empty_status_matrix_rejected(self):
        x = fixture()
        x["checks"] = []
        with self.assertRaises(EvidenceError):
            evaluate_ci_evidence(x, now=NOW)

    def test_duplicate_required_name_rejected(self):
        x = fixture()
        x["required_checks"].append(x["required_checks"][0])
        with self.assertRaisesRegex(EvidenceError, "duplicate required"):
            evaluate_ci_evidence(x, now=NOW)

    def test_native_types_required(self):
        x = fixture()
        x["complete"] = 1
        with self.assertRaises(EvidenceError):
            evaluate_ci_evidence(x, now=NOW)

    def test_unknown_fields_rejected(self):
        x = fixture()
        x["approval"] = True
        with self.assertRaises(EvidenceError):
            evaluate_ci_evidence(x, now=NOW)

    def test_stale_timestamp_rejected(self):
        x = fixture()
        x["checks"][0]["completed_at"] = "2026-11-01T00:00:00Z"
        with self.assertRaisesRegex(EvidenceError, "stale"):
            evaluate_ci_evidence(x, now=NOW)

    def test_future_snapshot_rejected(self):
        x = fixture()
        x["observed_at"] = "2026-11-24T00:00:00Z"
        with self.assertRaisesRegex(EvidenceError, "future"):
            evaluate_ci_evidence(x, now=NOW)

    def test_malformed_timestamp_rejected(self):
        x = fixture()
        x["checks"][0]["completed_at"] = "yesterday"
        with self.assertRaises(EvidenceError):
            evaluate_ci_evidence(x, now=NOW)

    def test_invalid_sha_rejected(self):
        x = fixture()
        x["pr_head_sha"] = "not-a-sha"
        with self.assertRaises(EvidenceError):
            evaluate_ci_evidence(x, now=NOW)

    def test_oversized_sha_rejected_before_regex_scan(self):
        snapshot = fixture()
        snapshot["pr_head_sha"] = "a" * 1_000_000
        with patch("mojum.qa_ci_evidence._SHA_RE") as sha_pattern:
            with self.assertRaisesRegex(EvidenceError, "40-character"):
                evaluate_ci_evidence(snapshot, now=NOW)
            sha_pattern.fullmatch.assert_not_called()

    def test_no_merge_authority_in_report(self):
        result = evaluate_ci_evidence(fixture(), now=NOW).to_dict()
        self.assertNotIn("approved", result)
        self.assertNotIn("merge", result)


if __name__ == "__main__":
    unittest.main()
