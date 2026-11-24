"""Deterministic escalation routing for the Mojum QA supervisor.

This module consumes caller-supplied PUBLIC metadata only. It does not call
models, inspect GitHub, authorize merges, or treat confidence as evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.qa-escalation-input/1"
RECEIPT_SCHEMA = "mojum.qa-escalation-receipt/1"
MAX_FINDINGS = 32
MAX_VERDICTS = 8
LOW_CONFIDENCE_MILLI = 600
_ALLOWED_FLAGS = frozenset({
    "security_sensitive",
    "authorization_ambiguous",
    "stale_revision",
    "unsafe_input",
    "schema_migration",
})
_ALLOWED_VERDICTS = frozenset({"PASS", "FAIL", "UNKNOWN"})
_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class EscalationInputError(ValueError):
    """Raised when an escalation snapshot is malformed or exceeds limits."""


@dataclass(frozen=True)
class EscalationDecision:
    schema: str
    pr_number: int
    head_sha: str
    route: str
    reasons: tuple[str, ...]
    input_digest: str
    receipt_digest: str


def _exact_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    if set(value) != expected:
        raise EscalationInputError(f"{where} fields must be exactly {sorted(expected)!r}")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EscalationInputError("input is not canonical JSON data") from exc


def decide_escalation(snapshot: dict[str, Any]) -> EscalationDecision:
    """Return a deterministic route: LOCAL_REVIEW, ESCALATE, or HUMAN_BLOCK.

    HUMAN_BLOCK has precedence over model escalation because stale revisions
    and ambiguous authorization must be resolved outside model judgement.
    """
    if type(snapshot) is not dict:
        raise EscalationInputError("snapshot must be an exact dict")
    _exact_keys(
        snapshot,
        {"schema", "pr_number", "head_sha", "confidence_milli", "risk_flags", "qa_verdicts"},
        "snapshot",
    )
    if type(snapshot["schema"]) is not str or snapshot["schema"] != SCHEMA:
        raise EscalationInputError("unsupported schema")
    pr_number = snapshot["pr_number"]
    if type(pr_number) is not int or not 1 <= pr_number <= 2**31 - 1:
        raise EscalationInputError("pr_number must be a positive bounded native int")
    head_sha = snapshot["head_sha"]
    if type(head_sha) is not str or not _SHA_RE.fullmatch(head_sha):
        raise EscalationInputError("head_sha must be a lowercase full Git SHA")
    confidence = snapshot["confidence_milli"]
    if type(confidence) is not int or not 0 <= confidence <= 1000:
        raise EscalationInputError("confidence_milli must be a native int from 0 to 1000")

    flags = snapshot["risk_flags"]
    if type(flags) is not list or len(flags) > MAX_FINDINGS:
        raise EscalationInputError("risk_flags must be a bounded list")
    if any(type(flag) is not str or flag not in _ALLOWED_FLAGS for flag in flags):
        raise EscalationInputError("risk_flags contains an unknown or malformed flag")
    if len(set(flags)) != len(flags):
        raise EscalationInputError("risk_flags must not contain duplicates")

    verdicts = snapshot["qa_verdicts"]
    if type(verdicts) is not list or len(verdicts) > MAX_VERDICTS:
        raise EscalationInputError("qa_verdicts must be a bounded list")
    if any(type(verdict) is not str or verdict not in _ALLOWED_VERDICTS for verdict in verdicts):
        raise EscalationInputError("qa_verdicts contains an unknown or malformed verdict")

    reasons: list[str] = []
    if "authorization_ambiguous" in flags:
        reasons.append("AUTHORIZATION_AMBIGUOUS")
    if "stale_revision" in flags:
        reasons.append("STALE_REVISION")
    if "security_sensitive" in flags:
        reasons.append("SECURITY_SENSITIVE")
    if "unsafe_input" in flags:
        reasons.append("UNSAFE_INPUT")
    if "schema_migration" in flags:
        reasons.append("SCHEMA_MIGRATION")
    if "PASS" in verdicts and "FAIL" in verdicts:
        reasons.append("CONFLICTING_QA_VERDICTS")
    if confidence < LOW_CONFIDENCE_MILLI:
        reasons.append("LOW_TRIAGE_CONFIDENCE")

    if "AUTHORIZATION_AMBIGUOUS" in reasons or "STALE_REVISION" in reasons:
        route = "HUMAN_BLOCK"
    elif reasons:
        route = "ESCALATE"
    else:
        route = "LOCAL_REVIEW"

    input_digest = hashlib.sha256(_canonical_bytes(snapshot)).hexdigest()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "route": route,
        "reasons": reasons,
        "input_digest": input_digest,
    }
    receipt_digest = hashlib.sha256(_canonical_bytes(receipt)).hexdigest()
    return EscalationDecision(
        schema=RECEIPT_SCHEMA,
        pr_number=pr_number,
        head_sha=head_sha,
        route=route,
        reasons=tuple(reasons),
        input_digest=input_digest,
        receipt_digest=receipt_digest,
    )
