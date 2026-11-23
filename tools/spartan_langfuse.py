"""Opt-in, allowlist-only Langfuse observability for synthetic Spartan workflows.

Issue #697. This module does not import Langfuse, read any PRIVATE journal,
initialize a client, configure credentials, or open a network connection.
Only a caller-supplied Langfuse SDK client can publish an approved envelope.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Iterable

SCHEMA = "mojum.spartan.observability/1"
STAGES = frozenset({"provider_admission", "patch_test", "pr_opened", "ci_complete", "qa_review"})
STATUSES = frozenset({"success", "failure", "blocked", "deferred"})
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z", re.ASCII)
_LEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}\Z", re.ASCII)
MAX_EVENTS = 10_000
MAX_LATENCY_MS = 86_400_000
MAX_TOKENS = 100_000_000
MAX_COST_MICRO_USD = 100_000_000_000


@dataclass(frozen=True, slots=True)
class SafeEvent:
    """Locally supplied metrics only; no prompt/source/organism payload slots."""

    stage: str
    lease_id: str
    attempt: int
    fencing_epoch: int
    sequence: int
    status: str
    pr_head_sha: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    ci_passed: bool | None = None
    qa_passed: bool | None = None


def _int(value: object, name: str, maximum: int, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the allowed integer range")


def validate_event(event: SafeEvent) -> None:
    """Reject unknown stages, stale/malformed identities and invented QA facts."""
    if type(event) is not SafeEvent:
        raise ValueError("observability requires a typed SafeEvent")
    if type(event.stage) is not str or event.stage not in STAGES:
        raise ValueError("unknown event stage")
    if type(event.status) is not str or event.status not in STATUSES:
        raise ValueError("unknown event status")
    if type(event.lease_id) is not str or _LEASE.fullmatch(event.lease_id) is None:
        raise ValueError("invalid opaque lease identifier")
    _int(event.attempt, "attempt", 10_000, minimum=1)
    _int(event.fencing_epoch, "fencing_epoch", 2**63 - 1, minimum=1)
    _int(event.sequence, "sequence", 2**31 - 1)
    _int(event.latency_ms, "latency_ms", MAX_LATENCY_MS)
    _int(event.input_tokens, "input_tokens", MAX_TOKENS)
    _int(event.output_tokens, "output_tokens", MAX_TOKENS)
    _int(event.cost_micro_usd, "cost_micro_usd", MAX_COST_MICRO_USD)
    if event.pr_head_sha is not None and (
        type(event.pr_head_sha) is not str or _SHA.fullmatch(event.pr_head_sha) is None
    ):
        raise ValueError("pr_head_sha must be a full lowercase Git object ID")
    if event.stage in {"pr_opened", "ci_complete", "qa_review"} and event.pr_head_sha is None:
        raise ValueError("PR/CI/QA events require an exact PR HEAD")
    if event.stage == "provider_admission" and event.pr_head_sha is not None:
        raise ValueError("provider admission cannot assert a future PR HEAD")
    for name, value in (("ci_passed", event.ci_passed), ("qa_passed", event.qa_passed)):
        if value is not None and type(value) is not bool:
            raise ValueError(f"{name} must be a measured boolean or unknown")
    if event.stage == "ci_complete":
        if event.ci_passed is None or event.qa_passed is not None:
            raise ValueError("CI event requires only measured CI outcome")
    elif event.stage == "qa_review":
        if event.ci_passed is None or event.qa_passed is None:
            raise ValueError("QA event needs both CI and independent QA results")
    elif event.ci_passed is not None or event.qa_passed is not None:
        raise ValueError("non-CI/QA events cannot assert acceptance results")


def _key(secret: bytes) -> bytes:
    if type(secret) is not bytes or not 32 <= len(secret) <= 128:
        raise ValueError("observability HMAC key must be 32..128 raw bytes")
    return secret


def _mac(secret: bytes, label: str, parts: list[str]) -> str:
    # JSON array encoding removes separator ambiguity between fields.
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hmac.new(secret, label.encode("ascii") + b":" + raw, hashlib.sha256).hexdigest()


def safe_envelope(event: SafeEvent, *, secret: bytes) -> dict[str, object]:
    """Build a detached strict numeric/enumerated envelope, never serialize the event."""
    validate_event(event)
    key = _key(secret)
    identity = [event.lease_id, str(event.attempt), str(event.fencing_epoch)]
    trace_id = _mac(key, "trace-v1", identity)[:32]
    public: dict[str, object] = {
        "schema": SCHEMA,
        "trace_id": trace_id,
        "stage": event.stage,
        "status": event.status,
        "attempt": event.attempt,
        "fencing_epoch": event.fencing_epoch,
        "sequence": event.sequence,
        "latency_ms": event.latency_ms,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "cost_micro_usd": event.cost_micro_usd,
    }
    if event.pr_head_sha is not None:
        public["pr_head_sha"] = event.pr_head_sha
    if event.ci_passed is not None:
        public["ci_passed"] = event.ci_passed
    if event.qa_passed is not None:
        public["qa_passed"] = event.qa_passed
    # Bind the event id to every accepted field as well as the private lease.
    canonical = json.dumps(public, sort_keys=True, separators=(",", ":"), allow_nan=False)
    public["event_id"] = _mac(key, "event-v1", [event.lease_id, canonical])[:32]
    return public


def publish(
    event: SafeEvent,
    *,
    enabled: bool = False,
    client: object | None = None,
    secret: bytes = b"",
) -> bool:
    """Export through an explicitly supplied Langfuse v5 SDK client.

    This is a best-effort *side effect*, never an approval, merge or scheduling
    decision. SDK errors are intentionally not logged or propagated. In the
    default no-op mode there is no validation, import, connection or client use.
    """
    if enabled is not True:
        return False
    try:
        metadata = safe_envelope(event, secret=secret)
        if client is None:
            return False
        # Langfuse 5.2.0: context-manager API for an OpenTelemetry-backed span.
        # Explicitly do not provide input, output, prompt, source or exception text.
        with client.start_as_current_observation(
            name="mojum.spartan." + event.stage,
            as_type="span",
            trace_context={"trace_id": metadata["trace_id"]},
            metadata=metadata,
        ):
            pass
    except Exception:
        # An observability outage cannot veto a worker or independent reviewer.
        return False
    return True


def summarize(events: Iterable[SafeEvent]) -> dict[str, int | str]:
    """Local in-memory quality counters; no I/O and no PRIVATE data export.

    Replayed identical events are counted only once. A conflicting reuse of the
    same leased event sequence fails closed instead of forging a good QA rate.
    """
    totals: dict[str, int | str] = {
        "schema": "mojum.spartan.observability-summary/1",
        "events": 0, "input_tokens": 0, "output_tokens": 0,
        "cost_micro_usd": 0, "latency_ms": 0,
        "ci_pass": 0, "ci_fail": 0, "qa_pass": 0, "qa_fail": 0,
        "ci_false_success": 0,
    }
    seen: dict[tuple[str, int, int, int], SafeEvent] = {}
    processed = 0
    for event in events:
        processed += 1
        if processed > MAX_EVENTS:
            raise ValueError("observability summary event budget exceeded")
        validate_event(event)
        identity = (event.lease_id, event.attempt, event.fencing_epoch, event.sequence)
        previous = seen.get(identity)
        if previous is not None:
            if previous != event:
                raise ValueError("conflicting event replay under same lease sequence")
            continue
        seen[identity] = event
        totals["events"] += 1
        for metric in ("input_tokens", "output_tokens", "cost_micro_usd", "latency_ms"):
            totals[metric] += getattr(event, metric)
        if event.stage == "ci_complete":
            totals["ci_pass" if event.ci_passed else "ci_fail"] += 1
        if event.stage == "qa_review":
            totals["qa_pass" if event.qa_passed else "qa_fail"] += 1
            if event.ci_passed and not event.qa_passed:
                totals["ci_false_success"] += 1
    return totals
