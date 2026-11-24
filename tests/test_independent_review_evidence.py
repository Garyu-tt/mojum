import copy
import unittest

from mojum.independent_review_evidence import (
    ReviewEvidenceError,
    validate_review_evidence,
    SCHEMA,
)

HEAD = "a" * 40
BASE = "b" * 40


def snapshot():
    return {
        "schema": SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "consent": True,
        "pull_request": {
            "number": 712,
            "author": "garyu-tt",
            "last_pusher": "garyu-tt",
            "head_sha": HEAD,
            "base_sha": BASE,
            "state": "open",
            "draft": False,
        },
        "reviews": [
            {"id": 10, "reviewer": "theta-afk-rev", "state": "APPROVED",
             "commit_sha": HEAD, "submitted_at": 100},
        ],
    }


class IndependentReviewEvidenceTests(unittest.TestCase):
    def test_accepts_distinct_exact_head_approval(self):
        receipt = validate_review_evidence(snapshot())
        self.assertEqual(receipt.verdict, "INDEPENDENT_APPROVAL_PRESENT")
        self.assertEqual(receipt.reviewer, "theta-afk-rev")
        self.assertEqual(len(receipt.digest), 64)

    def test_digest_is_deterministic(self):
        self.assertEqual(validate_review_evidence(snapshot()), validate_review_evidence(snapshot()))

    def test_rejects_owner_self_approval(self):
        data = snapshot()
        data["reviews"][0]["reviewer"] = "garyu-tt"
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_last_pusher_even_if_not_author(self):
        data = snapshot()
        data["pull_request"]["last_pusher"] = "theta-afk-rev"
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_stale_head_approval(self):
        data = snapshot()
        data["reviews"][0]["commit_sha"] = "c" * 40
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_later_dismissal_invalidates_approval(self):
        data = snapshot()
        data["reviews"].append({"id": 11, "reviewer": "theta-afk-rev", "state": "DISMISSED",
                                "commit_sha": HEAD, "submitted_at": 101})
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_later_changes_requested_invalidates_approval(self):
        data = snapshot()
        data["reviews"].append({"id": 11, "reviewer": "theta-afk-rev", "state": "CHANGES_REQUESTED",
                                "commit_sha": HEAD, "submitted_at": 101})
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_comment_is_not_approval(self):
        data = snapshot()
        data["reviews"][0]["state"] = "COMMENTED"
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_draft_and_closed_pr(self):
        for key, value in (("draft", True), ("state", "closed")):
            data = snapshot()
            data["pull_request"][key] = value
            with self.assertRaises(ReviewEvidenceError):
                validate_review_evidence(data)

    def test_rejects_boolean_integer(self):
        data = snapshot()
        data["pull_request"]["number"] = True
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_duplicate_review_ids(self):
        data = snapshot()
        data["reviews"].append(copy.deepcopy(data["reviews"][0]))
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_unknown_fields(self):
        data = snapshot()
        data["reviews"][0]["body"] = "looks good"
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_missing_consent_and_private_provenance(self):
        for field, value in (("consent", False), ("provenance", "PRIVATE")):
            data = snapshot()
            data[field] = value
            with self.assertRaises(ReviewEvidenceError):
                validate_review_evidence(data)

    def test_rejects_unbounded_review_list(self):
        data = snapshot()
        data["reviews"] = data["reviews"] * 257
        with self.assertRaises(ReviewEvidenceError):
            validate_review_evidence(data)

    def test_rejects_malformed_sha_and_login(self):
        for mutate in (
            lambda d: d["pull_request"].__setitem__("head_sha", "not-a-sha"),
            lambda d: d["reviews"][0].__setitem__("reviewer", "not a login"),
        ):
            data = snapshot()
            mutate(data)
            with self.assertRaises(ReviewEvidenceError):
                validate_review_evidence(data)

    def test_hostile_string_subclass_keys_are_rejected_before_hashing(self):
        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("hostile key equality must not execute")

        mutations = (
            lambda data: data.__setitem__(HostileKey("schema"), data.pop("schema")),
            lambda data: data["pull_request"].__setitem__(
                HostileKey("number"), data["pull_request"].pop("number")
            ),
            lambda data: data["reviews"][0].__setitem__(
                HostileKey("id"), data["reviews"][0].pop("id")
            ),
        )
        for mutate in mutations:
            data = snapshot()
            mutate(data)
            with self.assertRaisesRegex(ReviewEvidenceError, "bounded native strings"):
                validate_review_evidence(data)

    def test_oversized_mapping_is_rejected_by_cardinality_first(self):
        data = snapshot()
        data.update({f"extra-{index}": index for index in range(10_000)})
        with self.assertRaisesRegex(ReviewEvidenceError, "exactly 5"):
            validate_review_evidence(data)

        data = snapshot()
        data["pull_request"].update({f"extra-{index}": index for index in range(10_000)})
        with self.assertRaisesRegex(ReviewEvidenceError, "exactly 7"):
            validate_review_evidence(data)

    def test_hostile_discriminator_values_are_rejected_without_comparison(self):
        class HostileValue:
            def __eq__(self, other):
                raise AssertionError("untrusted value equality must not execute")

        for location, field in (("root", "schema"), ("root", "provenance"), ("pr", "state"),
                                ("review", "state")):
            data = snapshot()
            target = data if location == "root" else (
                data["pull_request"] if location == "pr" else data["reviews"][0]
            )
            target[field] = HostileValue()
            with self.assertRaises(ReviewEvidenceError):
                validate_review_evidence(data)


if __name__ == "__main__":
    unittest.main()
