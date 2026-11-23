"""Offline writer fencing-token admission for Continuity Mesh.

Evaluates caller-supplied metadata only. It does not mint, authenticate,
persist, or distribute tokens and is not a lease service or consensus system.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.mesh.writer-fence/1"
RECEIPT_SCHEMA = "mojum.mesh.writer-fence-decision/1"
MAX_COUNTER = (1 << 63) - 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)
_FIELDS = frozenset({"schema", "organism_id", "scope", "owner_id", "epoch", "revision"})

@dataclass(frozen=True)
class FenceDecision:
    """Advisory decision; accepted does not imply authenticated authority."""
    accepted: bool
    reason: str
    candidate_digest: str | None
    high_water_digest: str | None
    receipt: str

def _is_counter(value: Any) -> bool:
    return type(value) is int and 1 <= value <= MAX_COUNTER

def _is_id(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None

def _validated_document(value: Any) -> dict[str, Any] | None:
    """Return a fresh canonical dict, or None for malformed input."""
    if type(value) is not dict or len(value) != len(_FIELDS):
        return None
    if any(type(key) is not str for key in dict.keys(value)):
        return None
    try:
        if frozenset(dict.keys(value)) != _FIELDS:
            return None
        data = {key: dict.__getitem__(value, key) for key in _FIELDS}
    except (KeyError, RuntimeError, TypeError, ValueError):
        return None
    if (type(data["schema"]) is not str or data["schema"] != SCHEMA
            or not _is_id(data["organism_id"])
            or not _is_id(data["scope"])
            or not _is_id(data["owner_id"])
            or not _is_counter(data["epoch"])
            or not _is_counter(data["revision"])):
        return None
    return data

def _digest(data: dict[str, Any] | None) -> str | None:
    if data is None:
        return None
    wire = [SCHEMA, data["organism_id"], data["scope"], data["owner_id"],
            data["epoch"], data["revision"]]
    payload = json.dumps(wire, ensure_ascii=True, separators=(",", ":"),
                         allow_nan=False).encode("ascii")
    return hashlib.sha256(payload).hexdigest()

def _decision(accepted: bool, reason: str, candidate_digest: str | None,
              high_water_digest: str | None) -> FenceDecision:
    core = [RECEIPT_SCHEMA, accepted, reason, candidate_digest, high_water_digest]
    receipt = hashlib.sha256(json.dumps(
        core, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")).hexdigest()
    return FenceDecision(accepted, reason, candidate_digest, high_water_digest, receipt)

def evaluate_writer_fence(candidate: Any, high_water: Any = None) -> FenceDecision:
    """Admit a candidate only when it advances the supplied accepted high-water mark.

    The caller must load high_water from a trusted, durable, consensus-backed
    authority. Passing None asserts there is no previously accepted token;
    this function cannot verify that assertion.

    Epochs increase across owner changes. Within one epoch, only the same owner
    may advance revision. Same revision is a duplicate and is rejected.
    """
    proposed = _validated_document(candidate)
    previous = None if high_water is None else _validated_document(high_water)
    if proposed is None or (high_water is not None and previous is None):
        return _decision(False, "REJECT_MALFORMED",
                         _digest(proposed), _digest(previous))
    candidate_digest = _digest(proposed)
    previous_digest = _digest(previous)
    if previous is None:
        if proposed["epoch"] != 1 or proposed["revision"] != 1:
            return _decision(False, "REJECT_MALFORMED", candidate_digest, None)
        return _decision(True, "ACCEPTED_INITIAL", candidate_digest, None)
    if (proposed["organism_id"] != previous["organism_id"]
            or proposed["scope"] != previous["scope"]):
        return _decision(False, "REJECT_SCOPE_MISMATCH",
                         candidate_digest, previous_digest)
    if proposed["epoch"] < previous["epoch"]:
        return _decision(False, "REJECT_STALE_EPOCH",
                         candidate_digest, previous_digest)
    if proposed["epoch"] > previous["epoch"]:
        return _decision(True, "ACCEPTED_NEW_EPOCH",
                         candidate_digest, previous_digest)
    if proposed["owner_id"] != previous["owner_id"]:
        return _decision(False, "REJECT_OWNER_CONFLICT",
                         candidate_digest, previous_digest)
    if proposed["revision"] < previous["revision"]:
        return _decision(False, "REJECT_REVISION_ROLLBACK",
                         candidate_digest, previous_digest)
    if proposed["revision"] == previous["revision"]:
        return _decision(False, "REJECT_DUPLICATE_REVISION",
                         candidate_digest, previous_digest)
    return _decision(True, "ACCEPTED_ADVANCE", candidate_digest, previous_digest)

__all__ = ["SCHEMA", "RECEIPT_SCHEMA", "MAX_COUNTER", "FenceDecision",
           "evaluate_writer_fence"]
