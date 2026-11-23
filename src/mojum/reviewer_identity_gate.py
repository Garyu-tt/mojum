"""Offline exact-head GitHub reviewer identity preflight (issue #1109).

Evaluates a caller-supplied complete review snapshot. It does not query
GitHub, provision identities, submit approvals, or authorize merges.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

SCHEMA = "mojum.reviewer-identity.v1"
MAX_REVIEWS = 1000
_SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_LOGIN_RE = re.compile(r"[A-Za-z0-9-]{1,39}\Z")
_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"})


class ReviewerSnapshotError(ValueError):
    """The supplied review snapshot is malformed or exceeds its budget."""


@dataclass(frozen=True)
class ReviewerGateResult:
    verdict: str
    code: str
    head_sha: str
    review_count: int
    latest_reviewers: int
    qualifying_reviewer_ids: tuple[int, ...]

    @property
    def exit_code(self) -> int:
        return 0 if self.verdict == "ready" else (2 if self.verdict == "defer" else 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "code": self.code,
            "head_sha": self.head_sha,
            "review_count": self.review_count,
            "latest_reviewers": self.latest_reviewers,
            "qualifying_reviewer_ids": list(self.qualifying_reviewer_ids),
            "exit_code": self.exit_code,
            "merge_performed": False,
        }


def _native_int(value: object, label: str, *, maximum: int = (1 << 63) - 1) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ReviewerSnapshotError(f"{label} must be a positive native integer")
    return value


def _sha(value: object, label: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        raise ReviewerSnapshotError(f"{label} must be a full hexadecimal Git object ID")
    return value.lower()


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str or len(value) > 40 or _TIMESTAMP_RE.fullmatch(value) is None:
        raise ReviewerSnapshotError(f"{label} must be a bounded RFC 3339 timestamp")
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except (ValueError, OverflowError):
        raise ReviewerSnapshotError(f"{label} must be a bounded RFC 3339 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReviewerSnapshotError(f"{label} must include a timezone")
    return parsed


def evaluate_review_snapshot(snapshot: object) -> ReviewerGateResult:
    """Require the latest exact-head approval by a distinct GitHub account.

    Caller-supplied reviews_complete must mean every page was fetched. This
    assertion is a trust boundary, not cryptographic proof of completeness.
    """
    if type(snapshot) is not dict:
        raise ReviewerSnapshotError("snapshot must be a native object")
    expected = {
        "schema", "head_sha", "author_id", "last_pusher_id",
        "reviews_complete", "reviews",
    }
    if any(type(key) is not str for key in snapshot):
        raise ReviewerSnapshotError("snapshot keys must be native strings")
    if set(snapshot) != expected:
        raise ReviewerSnapshotError("snapshot has missing or unexpected fields")
    if type(snapshot["schema"]) is not str or snapshot["schema"] != SCHEMA:
        raise ReviewerSnapshotError("unsupported snapshot schema")

    head_sha = _sha(snapshot["head_sha"], "head_sha")
    author_id = _native_int(snapshot["author_id"], "author_id")
    pusher_id = _native_int(snapshot["last_pusher_id"], "last_pusher_id")
    if type(snapshot["reviews_complete"]) is not bool:
        raise ReviewerSnapshotError("reviews_complete must be a boolean")
    reviews = snapshot["reviews"]
    if type(reviews) is not list:
        raise ReviewerSnapshotError("reviews must be a native list")
    if len(reviews) > MAX_REVIEWS:
        raise ReviewerSnapshotError("review count exceeds the configured budget")

    record_fields = {
        "review_id", "user_id", "login", "state", "commit_sha", "submitted_at"
    }
    seen_review_ids: set[int] = set()
    latest: dict[int, tuple[datetime, int, str, str]] = {}
    for index, review in enumerate(reviews):
        if type(review) is not dict:
            raise ReviewerSnapshotError(f"review[{index}] must be a native object")
        if any(type(key) is not str for key in review):
            raise ReviewerSnapshotError(f"review[{index}] keys must be native strings")
        if set(review) != record_fields:
            raise ReviewerSnapshotError(f"review[{index}] has an invalid shape")
        review_id = _native_int(review["review_id"], f"review[{index}].review_id")
        if review_id in seen_review_ids:
            raise ReviewerSnapshotError("duplicate review_id")
        seen_review_ids.add(review_id)
        user_id = _native_int(review["user_id"], f"review[{index}].user_id")
        login = review["login"]
        if type(login) is not str or _LOGIN_RE.fullmatch(login) is None:
            raise ReviewerSnapshotError(f"review[{index}].login is invalid")
        state = review["state"]
        if type(state) is not str or state not in _STATES:
            raise ReviewerSnapshotError(f"review[{index}].state is invalid")
        commit_sha = _sha(review["commit_sha"], f"review[{index}].commit_sha")
        submitted_at = _timestamp(review["submitted_at"], f"review[{index}].submitted_at")
        current = latest.get(user_id)
        candidate = (submitted_at, review_id, state, commit_sha)
        if current is None or candidate[:2] > current[:2]:
            latest[user_id] = candidate

    if not snapshot["reviews_complete"]:
        return ReviewerGateResult(
            "defer", "REVIEW_SNAPSHOT_INCOMPLETE", head_sha,
            len(reviews), len(latest), (),
        )

    qualifying = tuple(sorted(
        user_id
        for user_id, (_, _, state, commit_sha) in latest.items()
        if user_id not in {author_id, pusher_id}
        and state == "APPROVED"
        and commit_sha == head_sha
    ))
    if qualifying:
        return ReviewerGateResult(
            "ready", "INDEPENDENT_EXACT_HEAD_APPROVAL", head_sha,
            len(reviews), len(latest), qualifying,
        )

    distinct_approvals = [
        user_id for user_id, (_, _, state, _) in latest.items()
        if user_id not in {author_id, pusher_id} and state == "APPROVED"
    ]
    if distinct_approvals:
        code = "NO_APPROVAL_FOR_CURRENT_HEAD"
    elif any(state == "APPROVED" for _, _, state, _ in latest.values()):
        code = "ONLY_AUTHOR_OR_LAST_PUSHER_APPROVED"
    else:
        code = "INDEPENDENT_APPROVAL_MISSING"
    return ReviewerGateResult(
        "defer", code, head_sha, len(reviews), len(latest), (),
    )
