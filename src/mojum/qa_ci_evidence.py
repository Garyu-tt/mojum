"""Fail-closed evaluator for complete, exact-PR-head CI evidence snapshots.

This module evaluates caller-supplied evidence only. It does not fetch GitHub data,
authenticate its source, approve reviews, or authorize merges.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.qa-ci-evidence/1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/()+-]{0,127}$")
_MAX_CHECKS = 128
_MAX_NAME_CHARS = 128
_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_MAX_FUTURE_SKEW_SECONDS = 60


class EvidenceError(ValueError):
    """The supplied CI evidence is incomplete, malformed, stale, or unsafe."""


@dataclass(frozen=True)
class CIEvidenceDecision:
    schema: str
    head_sha: str
    required_checks: tuple[str, ...]
    successful_checks: int
    observed_at: str
    decision: str
    reasons: tuple[str, ...]
    evidence_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "head_sha": self.head_sha,
            "required_checks": list(self.required_checks),
            "successful_checks": self.successful_checks,
            "observed_at": self.observed_at,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "evidence_sha256": self.evidence_sha256,
        }


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise EvidenceError(f"{label} must be a native object with exactly the documented keys")
    # Check cardinality before iterating or allocating a key set. Then reject
    # non-native keys before hashing/comparing caller-controlled key objects.
    if len(value) != len(expected):
        raise EvidenceError(f"{label} must be a native object with exactly the documented keys")
    for key in value:
        if type(key) is not str:
            raise EvidenceError(f"{label} keys must be native strings")
    if set(value) != expected:
        raise EvidenceError(f"{label} must be a native object with exactly the documented keys")
    return value


def _utc(value: object, label: str) -> datetime:
    if type(value) is not str or len(value) != 20 or not value.endswith("Z"):
        raise EvidenceError(f"{label} must be UTC RFC3339 seconds ending in Z")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise EvidenceError(f"{label} is not a valid UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise EvidenceError(f"{label} is not canonical")
    return parsed


def _sha(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 40 or not _SHA_RE.fullmatch(value):
        raise EvidenceError(f"{label} must be a lowercase 40-character commit SHA")
    return value


def evaluate_ci_evidence(snapshot: object, *, now: str) -> CIEvidenceDecision:
    """Evaluate a complete snapshot at an explicit UTC clock value.

    A PASS means only that all declared required checks are represented as
    completed/successful on the exact supplied head and are fresh. It is not
    an authenticated GitHub attestation or a merge/approval decision.
    """
    data = _exact_keys(
        snapshot,
        {"schema", "complete", "pr_head_sha", "required_checks", "checks", "observed_at"},
        "snapshot",
    )
    if type(data["schema"]) is not str or data["schema"] != SCHEMA:
        raise EvidenceError("unsupported evidence schema")
    if type(data["complete"]) is not bool or data["complete"] is not True:
        raise EvidenceError("snapshot must explicitly declare complete=true")
    head = _sha(data["pr_head_sha"], "pr_head_sha")
    observed = _utc(data["observed_at"], "observed_at")
    evaluation_time = _utc(now, "now")
    if observed > evaluation_time:
        raise EvidenceError("snapshot observation time is in the future")
    if (evaluation_time - observed).total_seconds() > _MAX_AGE_SECONDS:
        raise EvidenceError("snapshot observation is stale")

    required_raw = data["required_checks"]
    checks_raw = data["checks"]
    if type(required_raw) is not list or not 1 <= len(required_raw) <= _MAX_CHECKS:
        raise EvidenceError("required_checks must contain 1..128 names")
    if type(checks_raw) is not list or not 1 <= len(checks_raw) <= _MAX_CHECKS:
        raise EvidenceError("checks must contain 1..128 records")

    required: list[str] = []
    for item in required_raw:
        if type(item) is not str or len(item) > _MAX_NAME_CHARS or not _NAME_RE.fullmatch(item):
            raise EvidenceError("required check name is invalid")
        if item in required:
            raise EvidenceError("duplicate required check name")
        required.append(item)

    by_name: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(checks_raw):
        check = _exact_keys(
            raw,
            {"name", "head_sha", "run_id", "status", "conclusion", "completed_at"},
            f"checks[{index}]",
        )
        name = check["name"]
        if type(name) is not str or len(name) > _MAX_NAME_CHARS or not _NAME_RE.fullmatch(name):
            raise EvidenceError(f"checks[{index}].name is invalid")
        if name in by_name:
            raise EvidenceError(f"duplicate check record: {name}")
        if name not in required:
            raise EvidenceError(f"unexpected check record: {name}")
        if _sha(check["head_sha"], f"{name}.head_sha") != head:
            raise EvidenceError(f"{name} is pinned to a different PR head")
        if type(check["run_id"]) is not int or not 1 <= check["run_id"] <= 2**63 - 1:
            raise EvidenceError(f"{name}.run_id must be a positive native integer")
        if type(check["status"]) is not str or check["status"] != "completed":
            raise EvidenceError(f"{name} is not completed")
        if type(check["conclusion"]) is not str or check["conclusion"] != "success":
            raise EvidenceError(f"{name} did not succeed")
        completed = _utc(check["completed_at"], f"{name}.completed_at")
        age = (evaluation_time - completed).total_seconds()
        if age < -_MAX_FUTURE_SKEW_SECONDS:
            raise EvidenceError(f"{name} completion timestamp is in the future")
        if age > _MAX_AGE_SECONDS:
            raise EvidenceError(f"{name} evidence is stale")
        if completed > observed:
            raise EvidenceError(f"{name} completed after the snapshot observation time")
        by_name[name] = check

    missing = sorted(set(required) - set(by_name))
    if missing:
        raise EvidenceError("missing required checks: " + ", ".join(missing))
    if len(by_name) != len(required):
        raise EvidenceError("check inventory does not exactly match required_checks")

    canonical = {
        "schema": SCHEMA,
        "head_sha": head,
        "observed_at": data["observed_at"],
        "required_checks": sorted(required),
        "checks": [
            {
                "name": name,
                "head_sha": by_name[name]["head_sha"],
                "run_id": by_name[name]["run_id"],
                "status": by_name[name]["status"],
                "conclusion": by_name[name]["conclusion"],
                "completed_at": by_name[name]["completed_at"],
            }
            for name in sorted(by_name)
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return CIEvidenceDecision(
        schema=SCHEMA,
        head_sha=head,
        required_checks=tuple(sorted(required)),
        successful_checks=len(by_name),
        observed_at=data["observed_at"],
        decision="PASS",
        reasons=("all_required_checks_completed_successfully_on_exact_head",),
        evidence_sha256=digest,
    )
