"""Fail-closed validation of independent GitHub review evidence snapshots.

This module validates caller-supplied evidence only. It does not authenticate
GitHub data, contact GitHub, submit reviews, or authorize merges.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.independent-review-evidence/1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_MAX_REVIEWS = 256
_MAX_INPUT_BYTES = 256_000
_MAX_LOGIN = 39


class ReviewEvidenceError(ValueError):
    """Raised when review evidence is malformed or insufficient."""


@dataclass(frozen=True)
class ReviewEvidence:
    schema: str
    pull_request: int
    head_sha: str
    base_sha: str
    reviewer: str
    review_id: int
    submitted_at: int
    digest: str
    verdict: str = "INDEPENDENT_APPROVAL_PRESENT"


def _exact_keys(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ReviewEvidenceError(f"{where}: expected exact object keys")
    return value


def _login(value: Any, where: str) -> str:
    if type(value) is not str or len(value) > _MAX_LOGIN or not _LOGIN_RE.fullmatch(value):
        raise ReviewEvidenceError(f"{where}: invalid GitHub login")
    return value.casefold()


def _sha(value: Any, where: str) -> str:
    if type(value) is not str or not _SHA_RE.fullmatch(value):
        raise ReviewEvidenceError(f"{where}: expected lowercase 40-character SHA")
    return value


def _positive_int(value: Any, where: str, maximum: int = (2**63 - 1)) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise ReviewEvidenceError(f"{where}: expected bounded positive integer")
    return value


def validate_review_evidence(raw: Any) -> ReviewEvidence:
    """Validate one complete PUBLIC_SYNTHETIC snapshot and return a digest receipt.

    Expected schema:
      schema, provenance, consent, pull_request, reviews
    Each review is an event; the latest event per reviewer controls that
    reviewer's current state. Approval must name the exact current head SHA.
    """
    if type(raw) is not dict:
        raise ReviewEvidenceError("snapshot must be an exact dict")
    try:
        encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewEvidenceError("snapshot is not bounded canonical JSON data") from exc
    if len(encoded) > _MAX_INPUT_BYTES:
        raise ReviewEvidenceError("snapshot exceeds byte budget")

    root = _exact_keys(raw, {"schema", "provenance", "consent", "pull_request", "reviews"}, "snapshot")
    if root["schema"] != SCHEMA or root["provenance"] != "PUBLIC_SYNTHETIC" or root["consent"] is not True:
        raise ReviewEvidenceError("schema, provenance, or explicit consent is invalid")

    pr = _exact_keys(root["pull_request"], {"number", "author", "last_pusher", "head_sha", "base_sha", "state", "draft"}, "pull_request")
    number = _positive_int(pr["number"], "pull_request.number", 1_000_000_000)
    author = _login(pr["author"], "pull_request.author")
    last_pusher = _login(pr["last_pusher"], "pull_request.last_pusher")
    head_sha = _sha(pr["head_sha"], "pull_request.head_sha")
    base_sha = _sha(pr["base_sha"], "pull_request.base_sha")
    if pr["state"] != "open" or pr["draft"] is not False:
        raise ReviewEvidenceError("pull request must be open and non-draft")

    reviews = root["reviews"]
    if type(reviews) is not list or not 1 <= len(reviews) <= _MAX_REVIEWS:
        raise ReviewEvidenceError("reviews must contain 1..256 events")

    ids: set[int] = set()
    latest: dict[str, tuple[int, int, str, str]] = {}
    allowed_states = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}
    for index, item in enumerate(reviews):
        event = _exact_keys(item, {"id", "reviewer", "state", "commit_sha", "submitted_at"}, f"reviews[{index}]")
        review_id = _positive_int(event["id"], f"reviews[{index}].id")
        if review_id in ids:
            raise ReviewEvidenceError("duplicate review id")
        ids.add(review_id)
        reviewer = _login(event["reviewer"], f"reviews[{index}].reviewer")
        state = event["state"]
        if type(state) is not str or state not in allowed_states:
            raise ReviewEvidenceError(f"reviews[{index}].state: unsupported state")
        commit_sha = _sha(event["commit_sha"], f"reviews[{index}].commit_sha")
        submitted_at = _positive_int(event["submitted_at"], f"reviews[{index}].submitted_at")
        previous = latest.get(reviewer)
        ordering = (submitted_at, review_id)
        if previous is None or ordering > (previous[0], previous[1]):
            latest[reviewer] = (submitted_at, review_id, state, commit_sha)

    eligible = [
        (reviewer, event)
        for reviewer, event in latest.items()
        if reviewer not in {author, last_pusher}
        and event[2] == "APPROVED"
        and event[3] == head_sha
    ]
    if not eligible:
        raise ReviewEvidenceError("no current exact-head approval from an independent reviewer")
    reviewer, (submitted_at, review_id, _state, _commit) = sorted(eligible, key=lambda pair: (pair[1][0], pair[0]))[0]

    receipt_payload = {
        "schema": SCHEMA,
        "pull_request": number,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "reviewer": reviewer,
        "review_id": review_id,
        "submitted_at": submitted_at,
        "verdict": "INDEPENDENT_APPROVAL_PRESENT",
    }
    digest = hashlib.sha256(json.dumps(receipt_payload, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return ReviewEvidence(
        schema=SCHEMA, pull_request=number, head_sha=head_sha, base_sha=base_sha,
        reviewer=reviewer, review_id=review_id, submitted_at=submitted_at, digest=digest,
    )
