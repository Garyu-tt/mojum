"""Deterministic lifecycle transition policy for QA supervisor snapshots.

This module validates a proposed state transition. It does not perform work,
authenticate evidence, poll GitHub, or authorize a merge.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping

STATES = frozenset({
    "DISCOVERED", "CLAIMED", "REVIEWING", "TESTING", "FIXING",
    "RETESTING", "READY", "MERGED", "WAITING_CI",
    "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED",
})
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REASON_MAX = 512
_EVIDENCE_MAX = 16

# Edges are intentionally conservative. Recovery from blocked states requires
# explicit evidence and never silently resumes a previously authorized action.
_EDGES = {
    "DISCOVERED": {"CLAIMED", "WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT"},
    "CLAIMED": {"REVIEWING", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"},
    "REVIEWING": {"TESTING", "FIXING", "WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"},
    "TESTING": {"READY", "FIXING", "WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"},
    "FIXING": {"RETESTING", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"},
    "RETESTING": {"READY", "FIXING", "WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"},
    "READY": {"MERGED", "TESTING", "REVIEWING", "FIXING", "WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT"},
    "WAITING_CI": {"TESTING", "FIXING", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"},
    "BLOCKED_PERMISSION": {"CLAIMED", "REVIEWING", "TESTING", "FIXING"},
    "BLOCKED_CONFLICT": {"REVIEWING", "TESTING", "FIXING"},
    "ESCALATED": {"REVIEWING", "TESTING", "FIXING", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT"},
    "MERGED": set(),
}
_TERMINAL = frozenset({"MERGED"})
_REQUIRED = {
    "CLAIMED": {"worker_id"},
    "REVIEWING": {"review_id"},
    "TESTING": {"ci_head_sha"},
    "RETESTING": {"fix_commit_sha"},
    "READY": {"ci_head_sha", "review_id", "base_sha"},
    "MERGED": {"release_receipt"},
    "BLOCKED_PERMISSION": {"permission_code"},
    "BLOCKED_CONFLICT": {"conflict_code"},
    "WAITING_CI": {"ci_head_sha"},
    "ESCALATED": {"escalation_reason"},
}

class LifecycleError(ValueError):
    """Raised when a proposed lifecycle transition is malformed or unsafe."""

@dataclass(frozen=True)
class Transition:
    previous: str
    current: str
    reason: str
    evidence: Mapping[str, object]
    digest: str

def _text(value: object, name: str, limit: int = 128) -> str:
    if type(value) is not str or not value or len(value) > limit or value.strip() != value:
        raise LifecycleError(f"{name} must be a non-empty bounded string")
    return value

def _sha(value: object, name: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise LifecycleError(f"{name} must be a lowercase 40-character SHA")
    return value

def _validate_evidence(target: str, evidence: object, head_sha: str | None) -> dict[str, object]:
    if type(evidence) is not dict or len(evidence) > _EVIDENCE_MAX:
        raise LifecycleError("evidence must be a bounded plain dict")
    if any(type(k) is not str for k in evidence):
        raise LifecycleError("evidence keys must be strings")
    required = _REQUIRED.get(target, set())
    if not required.issubset(evidence):
        raise LifecycleError("required evidence is missing")
    if target in {"TESTING", "WAITING_CI", "READY"}:
        _sha(evidence.get("ci_head_sha"), "ci_head_sha")
        if head_sha is None or evidence["ci_head_sha"] != head_sha:
            raise LifecycleError("CI evidence must match the current head SHA")
    if target == "READY":
        _sha(evidence.get("base_sha"), "base_sha")
        _text(evidence.get("review_id"), "review_id")
    if target == "CLAIMED":
        _text(evidence.get("worker_id"), "worker_id")
    if target == "REVIEWING":
        _text(evidence.get("review_id"), "review_id")
    if target == "RETESTING":
        _sha(evidence.get("fix_commit_sha"), "fix_commit_sha")
        if head_sha is None or evidence["fix_commit_sha"] != head_sha:
            raise LifecycleError("fix commit must match the current head SHA")
    if target == "BLOCKED_PERMISSION":
        _text(evidence.get("permission_code"), "permission_code")
    if target == "BLOCKED_CONFLICT":
        _text(evidence.get("conflict_code"), "conflict_code")
    if target == "ESCALATED":
        _text(evidence.get("escalation_reason"), "escalation_reason", 256)
    if target == "MERGED":
        receipt = evidence.get("release_receipt")
        if type(receipt) is not dict or set(receipt) != {"schema", "head_sha", "base_sha", "review_id", "ci_passed", "human_authorized"}:
            raise LifecycleError("release_receipt has an invalid shape")
        if receipt["schema"] != "mojum.release-receipt/1":
            raise LifecycleError("unsupported release receipt schema")
        _sha(receipt["head_sha"], "release_receipt.head_sha")
        _sha(receipt["base_sha"], "release_receipt.base_sha")
        _text(receipt["review_id"], "release_receipt.review_id")
        if head_sha is None or receipt["head_sha"] != head_sha:
            raise LifecycleError("release receipt head does not match current head")
        if type(receipt["ci_passed"]) is not bool or type(receipt["human_authorized"]) is not bool:
            raise LifecycleError("release receipt flags must be native bools")
        if not receipt["ci_passed"] or not receipt["human_authorized"]:
            raise LifecycleError("release receipt does not satisfy its declared gates")
    # Keep only bounded scalar values; reject opaque nested objects and bool-as-int.
    for key, value in evidence.items():
        _text(key, "evidence key", 64)
        if type(value) is str:
            _text(value, f"evidence.{key}", 256)
        elif type(value) not in (bool, int):
            if key != "release_receipt" or type(value) is not dict:
                raise LifecycleError(f"unsupported evidence value for {key}")
        elif type(value) is int and not (-2**63 <= value < 2**63):
            raise LifecycleError("evidence integer is out of range")
    return evidence

def _freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value

def propose_transition(previous: object, target: object, *, reason: object,
                       head_sha: object = None, evidence: object = None) -> Transition:
    """Validate and return a detached, deterministic transition proposal."""
    if type(previous) is not str or previous not in STATES:
        raise LifecycleError("unknown previous state")
    if type(target) is not str or target not in STATES:
        raise LifecycleError("unknown target state")
    if target not in _EDGES[previous]:
        raise LifecycleError(f"transition {previous}->{target} is not allowed")
    if previous in _TERMINAL:
        raise LifecycleError("terminal states cannot transition")
    reason = _text(reason, "reason", _REASON_MAX)
    normalized_head = None if head_sha is None else _sha(head_sha, "head_sha")
    clean = _validate_evidence(target, evidence if evidence is not None else {}, normalized_head)
    canonical = {
        "previous": previous, "current": target, "reason": reason,
        "head_sha": normalized_head, "evidence": clean,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    digest = hashlib.sha256(b"mojum.qa-lifecycle/1\0" + encoded).hexdigest()
    detached = json.loads(encoded.decode("ascii"))["evidence"]
    return Transition(previous, target, reason, _freeze(detached), digest)
