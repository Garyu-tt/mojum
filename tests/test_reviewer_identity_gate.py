"""Offline exact-head reviewer identity preflight tests (issue #1109)."""
import unittest

from mojum.reviewer_identity_gate import (
    MAX_REVIEWS,
    SCHEMA,
    ReviewerSnapshotError,
    evaluate_review_snapshot,
)

HEAD = "a" * 40
OTHER = "b" * 40
STAMP = "2026-11-23T18:00:00Z"


def review(review_id=1, user_id=30, state="APPROVED", commit_sha=HEAD,
           submitted_at=STAMP, login="reviewer"):
    return {
        "review_id": review_id,
        "user_id": user_id,
        "login": login,
        "state": state,
        "commit_sha": commit_sha,
        "submitted_at": submitted_at,
    }


def snapshot(reviews=None, **overrides):
    value = {
        "schema": SCHEMA,
        "head_sha": HEAD,
        "author_id": 10,
        "last_pusher_id": 11,
        "reviews_complete": True,
        "reviews": [review()] if reviews is None else reviews,
    }
    value.update(overrides)
    return value


class ReviewerIdentityGateTests(unittest.TestCase):
    def test_exact_head_distinct_reviewer_is_ready(self):
        result = evaluate_review_snapshot(snapshot())
        self.assertEqual(result.verdict, "ready")
        self.assertEqual(result.code, "INDEPENDENT_EXACT_HEAD_APPROVAL")
        self.assertEqual(result.qualifying_reviewer_ids, (30,))
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.as_dict()["merge_performed"])

    def test_author_approval_does_not_qualify(self):
        result = evaluate_review_snapshot(snapshot([review(user_id=10)]))
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "ONLY_AUTHOR_OR_LAST_PUSHER_APPROVED")
        self.assertEqual(result.qualifying_reviewer_ids, ())

    def test_last_pusher_approval_does_not_qualify(self):
        result = evaluate_review_snapshot(snapshot([review(user_id=11)]))
        self.assertEqual(result.code, "ONLY_AUTHOR_OR_LAST_PUSHER_APPROVED")

    def test_stale_head_approval_does_not_qualify(self):
        result = evaluate_review_snapshot(snapshot([review(commit_sha=OTHER)]))
        self.assertEqual(result.code, "NO_APPROVAL_FOR_CURRENT_HEAD")
        self.assertEqual(result.verdict, "defer")

    def test_latest_comment_supersedes_earlier_approval(self):
        reviews = [
            review(review_id=1, state="APPROVED", submitted_at="2026-11-23T17:00:00Z"),
            review(review_id=2, state="COMMENTED", submitted_at="2026-11-23T18:00:00Z"),
        ]
        result = evaluate_review_snapshot(snapshot(reviews))
        self.assertEqual(result.code, "INDEPENDENT_APPROVAL_MISSING")

    def test_latest_changes_requested_supersedes_approval(self):
        reviews = [
            review(review_id=1, state="APPROVED", submitted_at="2026-11-23T17:00:00Z"),
            review(review_id=2, state="CHANGES_REQUESTED", submitted_at="2026-11-23T18:00:00Z"),
        ]
        self.assertEqual(
            evaluate_review_snapshot(snapshot(reviews)).code,
            "INDEPENDENT_APPROVAL_MISSING",
        )

    def test_later_approval_restores_eligibility_only_for_exact_head(self):
        reviews = [
            review(review_id=1, state="CHANGES_REQUESTED", submitted_at="2026-11-23T17:00:00Z"),
            review(review_id=2, state="APPROVED", submitted_at="2026-11-23T18:00:00Z"),
        ]
        self.assertEqual(evaluate_review_snapshot(snapshot(reviews)).verdict, "ready")

    def test_incomplete_pagination_defers_even_with_approval(self):
        result = evaluate_review_snapshot(snapshot(reviews_complete=False))
        self.assertEqual(result.verdict, "defer")
        self.assertEqual(result.code, "REVIEW_SNAPSHOT_INCOMPLETE")

    def test_review_order_does_not_override_timestamps(self):
        reviews = [
            review(review_id=2, state="COMMENTED", submitted_at="2026-11-23T18:00:00Z"),
            review(review_id=1, state="APPROVED", submitted_at="2026-11-23T17:00:00Z"),
        ]
        self.assertEqual(
            evaluate_review_snapshot(snapshot(reviews)).code,
            "INDEPENDENT_APPROVAL_MISSING",
        )

    def test_same_timestamp_uses_review_id_tie_breaker(self):
        reviews = [
            review(review_id=1, state="APPROVED"),
            review(review_id=2, state="DISMISSED"),
        ]
        self.assertEqual(
            evaluate_review_snapshot(snapshot(reviews)).code,
            "INDEPENDENT_APPROVAL_MISSING",
        )

    def test_multiple_independent_reviewers_are_sorted_and_redacted(self):
        result = evaluate_review_snapshot(snapshot([
            review(review_id=2, user_id=32, login="third"),
            review(review_id=1, user_id=30, login="first"),
        ]))
        self.assertEqual(result.qualifying_reviewer_ids, (30, 32))
        self.assertNotIn("login", str(result.as_dict()).lower())

    def test_rejects_non_native_and_unexpected_shapes(self):
        cases = [
            [],
            snapshot(extra=True),
            snapshot(author_id=True),
            snapshot(last_pusher_id=0),
            snapshot(reviews_complete=1),
            snapshot(reviews=()),
            snapshot(reviews=[review(user_id=True)]),
            snapshot(reviews=[review(state="APPROVED ") ]),
            snapshot(reviews=[review(commit_sha="short")]),
            snapshot(reviews=[review(submitted_at="2026-11-23T18:00:00")]),
            snapshot(reviews=[{**review(), "body": "not accepted"}]),
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ReviewerSnapshotError):
                evaluate_review_snapshot(item)

    def test_duplicate_review_ids_are_rejected(self):
        with self.assertRaisesRegex(ReviewerSnapshotError, "duplicate review_id"):
            evaluate_review_snapshot(snapshot([review(), review(user_id=31)]))

    def test_review_budget_is_bounded(self):
        with self.assertRaisesRegex(ReviewerSnapshotError, "budget"):
            evaluate_review_snapshot(snapshot(reviews=[review(i + 1) for i in range(MAX_REVIEWS + 1)]))

    def test_sha256_object_ids_are_supported(self):
        sha = "c" * 64
        result = evaluate_review_snapshot(snapshot(
            head_sha=sha,
            reviews=[review(commit_sha=sha)],
        ))
        self.assertEqual(result.verdict, "ready")
        self.assertEqual(result.head_sha, sha)


if __name__ == "__main__":
    unittest.main()
