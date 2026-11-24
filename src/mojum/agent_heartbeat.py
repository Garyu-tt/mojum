"""Bounded offline HMAC admission for external-agent heartbeat envelopes.

This module authenticates a caller-supplied event. It does not prove that a
process is alive, persist replay state, or contact any external service.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Mapping

SCHEMA = "mojum.agent-heartbeat/1"
EVENTS = frozenset({"start", "heartbeat", "finish"})
_FIELDS = frozenset({
    "schema", "agent_id", "run_id", "sequence", "event",
    "issued_at_ns", "expires_at_ns", "signature",
})
_PAYLOAD_FIELDS = _FIELDS - {"signature"}
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEX_RE = re.compile(r"[0-9a-f]{64}")
MAX_ENVELOPE_BYTES = 16 * 1024
MIN_KEY_BYTES = 32
MAX_KEY_BYTES = 128
DEFAULT_MAX_LIFETIME_NS = 60_000_000_000
DEFAULT_CLOCK_SKEW_NS = 5_000_000_000
MAX_LIFETIME_NS = 3_600_000_000_000
MAX_CLOCK_SKEW_NS = 60_000_000_000


class HeartbeatError(ValueError):
    """Heartbeat envelope is malformed, unauthenticated, or outside its window."""


@dataclass(frozen=True)
class VerifiedHeartbeat:
    """Sanitized authenticated metadata; contains no key or signature."""

    agent_id: str
    run_id: str
    sequence: int
    event: str
    issued_at_ns: int
    expires_at_ns: int
    payload_sha256: str


def _canonical(payload: Mapping[str, object]) -> bytes:
    try:
        text = json.dumps(
            dict(payload), ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
        encoded = text.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise HeartbeatError("payload is not canonical JSON data") from exc
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise HeartbeatError("heartbeat envelope exceeds size limit")
    return encoded


def _pairs_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HeartbeatError("duplicate JSON key")
        result[key] = value
    return result


def _parse(raw: str | bytes | Mapping[str, object]) -> dict[str, object]:
    if type(raw) is bytes:
        try:
            raw = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise HeartbeatError("invalid UTF-8 envelope") from exc
    if type(raw) is str:
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise HeartbeatError("invalid UTF-8 envelope") from exc
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise HeartbeatError("heartbeat envelope exceeds size limit")
        try:
            value = json.loads(
                raw,
                object_pairs_hook=_pairs_no_duplicates,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    HeartbeatError("non-finite JSON number")
                ),
            )
        except HeartbeatError:
            raise
        except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
            raise HeartbeatError("invalid JSON envelope") from exc
        if type(value) is not dict:
            raise HeartbeatError("heartbeat envelope must be a JSON object")
        if _canonical(value) != encoded:
            raise HeartbeatError("envelope JSON is not in canonical form")
        return value
    if type(raw) is dict:
        # Round-trip through canonical JSON to detach caller-owned containers
        # and reject unsupported values before verification.
        encoded = _canonical(raw)
        try:
            value = json.loads(encoded, object_pairs_hook=_pairs_no_duplicates)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise HeartbeatError("invalid envelope mapping") from exc
        if type(value) is not dict:
            raise HeartbeatError("heartbeat envelope must be an object")
        return value
    raise HeartbeatError("envelope must be canonical JSON text or a plain dict")


def _validate_payload(value: dict[str, object]) -> dict[str, object]:
    if set(value) != _FIELDS:
        raise HeartbeatError("heartbeat envelope has missing or unknown fields")
    if type(value["schema"]) is not str or value["schema"] != SCHEMA:
        raise HeartbeatError("unsupported heartbeat schema")
    for field in ("agent_id", "run_id"):
        item = value[field]
        if type(item) is not str or _ID_RE.fullmatch(item) is None:
            raise HeartbeatError(f"{field} is invalid")
    if (type(value["sequence"]) is not int or value["sequence"] < 0
            or value["sequence"] > 2**63 - 1):
        raise HeartbeatError("sequence must be a bounded nonnegative integer")
    if type(value["event"]) is not str or value["event"] not in EVENTS:
        raise HeartbeatError("unsupported heartbeat event")
    for field in ("issued_at_ns", "expires_at_ns"):
        item = value[field]
        if type(item) is not int or item < 0 or item > 2**63 - 1:
            raise HeartbeatError(f"{field} must be a bounded nonnegative integer")
    signature = value["signature"]
    if type(signature) is not str or _HEX_RE.fullmatch(signature) is None:
        raise HeartbeatError("signature must be 64 lowercase hexadecimal characters")
    return {key: value[key] for key in _PAYLOAD_FIELDS}


def sign_heartbeat(
    *, agent_id: str, run_id: str, sequence: int, event: str,
    issued_at_ns: int, expires_at_ns: int, key: bytes,
) -> dict[str, object]:
    """Create a detached envelope using the canonical signing format."""
    if type(key) is not bytes or not MIN_KEY_BYTES <= len(key) <= MAX_KEY_BYTES:
        raise HeartbeatError("key must be 32..128 bytes")
    payload: dict[str, object] = {
        "schema": SCHEMA, "agent_id": agent_id, "run_id": run_id,
        "sequence": sequence, "event": event,
        "issued_at_ns": issued_at_ns, "expires_at_ns": expires_at_ns,
    }
    provisional = dict(payload)
    provisional["signature"] = "0" * 64
    payload = _validate_payload(provisional)
    digest = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    return {**payload, "signature": digest}


def verify_heartbeat(
    raw: str | bytes | Mapping[str, object], *, key: bytes, now_ns: int,
    max_lifetime_ns: int = DEFAULT_MAX_LIFETIME_NS,
    clock_skew_ns: int = DEFAULT_CLOCK_SKEW_NS,
) -> VerifiedHeartbeat:
    """Authenticate one event against caller-supplied logical wall-clock time.

    Replay/high-water admission must be performed atomically by the caller
    using (agent_id, run_id, sequence); this function is intentionally stateless.
    """
    if type(key) is not bytes or not MIN_KEY_BYTES <= len(key) <= MAX_KEY_BYTES:
        raise HeartbeatError("key must be 32..128 bytes")
    if type(now_ns) is not int or not 0 <= now_ns <= 2**63 - 1:
        raise HeartbeatError("now_ns must be a bounded nonnegative integer")
    if type(max_lifetime_ns) is not int or not 1 <= max_lifetime_ns <= MAX_LIFETIME_NS:
        raise HeartbeatError("max_lifetime_ns is outside the allowed range")
    if type(clock_skew_ns) is not int or not 0 <= clock_skew_ns <= MAX_CLOCK_SKEW_NS:
        raise HeartbeatError("clock_skew_ns is outside the allowed range")

    envelope = _parse(raw)
    payload = _validate_payload(envelope)
    issued = payload["issued_at_ns"]
    expires = payload["expires_at_ns"]
    if expires <= issued or expires - issued > max_lifetime_ns:
        raise HeartbeatError("heartbeat lifetime is invalid")
    if issued > now_ns + clock_skew_ns:
        raise HeartbeatError("heartbeat is not yet valid")
    if expires <= now_ns:
        raise HeartbeatError("heartbeat has expired")

    expected = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(envelope["signature"], expected):
        raise HeartbeatError("heartbeat authentication failed")

    return VerifiedHeartbeat(
        agent_id=payload["agent_id"], run_id=payload["run_id"],
        sequence=payload["sequence"], event=payload["event"],
        issued_at_ns=issued, expires_at_ns=expires,
        payload_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )


__all__ = [
    "SCHEMA", "HeartbeatError", "VerifiedHeartbeat",
    "sign_heartbeat", "verify_heartbeat",
]
