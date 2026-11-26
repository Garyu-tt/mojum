"""Fail-closed, metadata-only evidence policy for independent PR review.

This module recommends readiness only. It never talks to GitHub or grants approval.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

_SCHEMA = "mojum.independent-review-gate/1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_JOB_STATUSES = frozenset({"completed"})
_ALLOWED_CONCLUSIONS = frozenset({"success"})
_MAX_JOBS = 32
_MAX_RUNS = 128
_MAX_NAME = 100
_MAX_LOGIN = 39


@dataclass(frozen=True)
class ReviewGateResult:
    schema: str
    decision: str
    reasons: tuple[str, ...]
    required_jobs: tuple[str, ...]
    successful_jobs: tuple[str, ...]
    receipt_sha256: str


def _native_string(value: Any, field: str, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{field} must be a non-empty native string of at most {maximum} characters")
    if value.strip() != value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{field} contains whitespace/control characters outside the token contract")
    return value


def _sha(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase 40-character commit SHA")
    return value


def _exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(expected):
        raise ValueError(f"{field} must be a native dict with exactly {sorted(expected)}")
    # Bound and type-check keys before hashing them in a set comparison.
    for key in value.keys():
        if type(key) is not str or not key or len(key) > 64:
            raise ValueError(f"{field} contains a non-native or oversized key")
    if set(value.keys()) != expected:
        raise ValueError(f"{field} must contain exactly {sorted(expected)}")
    return value


def evaluate_review_evidence(evidence: dict[str, Any]) -> ReviewGateResult:
    """Evaluate caller-supplied GitHub evidence; malformed input raises ValueError.

    The caller is responsible for fetching authenticated, current GitHub data.
    A REVIEW_READY result is not a GitHub approval or merge authorization.
    """
    e = _exact_keys(evidence, {
        "pr_number", "target_branch", "draft", "author_login", "last_pusher_login",
        "reviewer_login", "review_state", "reviewed_head_sha", "current_head_sha",
        "reviewed_base_sha", "current_base_sha", "required_jobs", "job_runs",
        "unresolved_threads",
    }, "evidence")

    if type(e["pr_number"]) is not int or not 1 <= e["pr_number"] <= 2_147_483_647:
        raise ValueError("pr_number must be a positive native integer")
    if type(e["draft"]) is not bool:
        raise ValueError("draft must be a native bool")
    if type(e["unresolved_threads"]) is not int or not 0 <= e["unresolved_threads"] <= 100_000:
        raise ValueError("unresolved_threads must be a bounded non-negative native integer")

    target = _native_string(e["target_branch"], "target_branch", 100)
    author = _native_string(e["author_login"], "author_login", _MAX_LOGIN)
    pusher = _native_string(e["last_pusher_login"], "last_pusher_login", _MAX_LOGIN)
    reviewer = _native_string(e["reviewer_login"], "reviewer_login", _MAX_LOGIN)
    review_state = _native_string(e["review_state"], "review_state", 32)
    reviewed_head = _sha(e["reviewed_head_sha"], "reviewed_head_sha")
    current_head = _sha(e["current_head_sha"], "current_head_sha")
    reviewed_base = _sha(e["reviewed_base_sha"], "reviewed_base_sha")
    current_base = _sha(e["current_base_sha"], "current_base_sha")

    required = e["required_jobs"]
    if type(required) is not list or not 1 <= len(required) <= _MAX_JOBS:
        raise ValueError(f"required_jobs must be a native list with 1..{_MAX_JOBS} entries")
    required_names = tuple(_native_string(x, "required job name", _MAX_NAME) for x in required)
    if len(set(required_names)) != len(required_names):
        raise ValueError("required_jobs contains duplicate names")

    runs = e["job_runs"]
    if type(runs) is not list or len(runs) > _MAX_RUNS:
        raise ValueError(f"job_runs must be a native list with at most {_MAX_RUNS} entries")
    by_name: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(runs):
        run = _exact_keys(raw, {"name", "head_sha", "status", "conclusion"}, f"job_runs[{index}]")
        name = _native_string(run["name"], "job run name", _MAX_NAME)
        head = _sha(run["head_sha"], "job run head_sha")
        status = _native_string(run["status"], "job run status", 32)
        conclusion = run["conclusion"]
        if conclusion is not None:
            conclusion = _native_string(conclusion, "job run conclusion", 32)
        if name in by_name:
            # Multiple attempts for one required context are ambiguous unless caller
            # has already reduced them to the latest attempt.
            raise ValueError("job_runs contains duplicate names; supply latest attempt only")
        by_name[name] = {"head_sha": head, "status": status, "conclusion": conclusion}

    reasons: list[str] = []
    if target != "main":
        reasons.append("wrong_target_branch")
    if e["draft"]:
        reasons.append("draft_pr")
    if reviewer.casefold() == author.casefold():
        reasons.append("reviewer_is_author")
    if reviewer.casefold() == pusher.casefold():
        reasons.append("reviewer_is_last_pusher")
    if review_state != "APPROVED":
        reasons.append("independent_approval_missing")
    if reviewed_head != current_head:
        reasons.append("reviewed_head_stale")
    if reviewed_base != current_base:
        reasons.append("reviewed_base_stale")
    if e["unresolved_threads"] != 0:
        reasons.append("unresolved_review_threads")

    successful: list[str] = []
    for name in required_names:
        run = by_name.get(name)
        if run is None:
            reasons.append(f"required_job_missing:{name}")
        elif run["head_sha"] != current_head:
            reasons.append(f"required_job_stale:{name}")
        elif run["status"] not in _ALLOWED_JOB_STATUSES or run["conclusion"] not in _ALLOWED_CONCLUSIONS:
            reasons.append(f"required_job_not_successful:{name}")
        else:
            successful.append(name)

    decision = "REVIEW_READY" if not reasons else "HOLD"
    receipt_payload = {
        "schema": _SCHEMA,
        "decision": decision,
        "reasons": reasons,
        "pr_number": e["pr_number"],
        "target_branch": target,
        "author_login": author,
        "last_pusher_login": pusher,
        "reviewer_login": reviewer,
        "review_state": review_state,
        "reviewed_head_sha": reviewed_head,
        "current_head_sha": current_head,
        "reviewed_base_sha": reviewed_base,
        "current_base_sha": current_base,
        "required_jobs": list(required_names),
        "successful_jobs": successful,
        "job_runs": [
            {"name": name, "head_sha": run["head_sha"], "status": run["status"], "conclusion": run["conclusion"]}
            for name, run in sorted(by_name.items())
        ],
        "unresolved_threads": e["unresolved_threads"],
    }
    canonical = json.dumps(receipt_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(canonical).hexdigest()
    return ReviewGateResult(
        schema=_SCHEMA,
        decision=decision,
        reasons=tuple(reasons),
        required_jobs=required_names,
        successful_jobs=tuple(successful),
        receipt_sha256=digest,
    )
