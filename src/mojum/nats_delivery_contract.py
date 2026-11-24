"""Offline, metadata-only contract for delegated NATS JetStream delivery.

This module does not connect to NATS, publish messages, or establish authority.
It admits bounded PUBLIC_SYNTHETIC metadata and produces deterministic plans.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

SCHEMA = "mojum.nats-delivery/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_SEQUENCE = (1 << 63) - 1
MAX_SEEN = 4096
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {
    "schema", "provenance", "node_id", "stream", "subject", "event_id",
    "delivery_id", "sequence", "tick", "payload_sha256", "causation_id",
}


class ContractError(ValueError):
    """Raised when untrusted delivery metadata violates the wire contract."""


@dataclass(frozen=True)
class DeliveryEnvelope:
    schema: str
    provenance: str
    node_id: str
    stream: str
    subject: str
    event_id: str
    delivery_id: str
    sequence: int
    tick: int
    payload_sha256: str
    causation_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "provenance": self.provenance,
            "node_id": self.node_id, "stream": self.stream,
            "subject": self.subject, "event_id": self.event_id,
            "delivery_id": self.delivery_id, "sequence": self.sequence,
            "tick": self.tick, "payload_sha256": self.payload_sha256,
            "causation_id": self.causation_id,
        }


@dataclass(frozen=True)
class DeliveryPlan:
    state: str
    delivery_key: str
    next_sequence: int | None
    reason: str


def _token(value: Any, field: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise ContractError(f"{field} must be a bounded ASCII token")
    return value


def _counter(value: Any, field: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_SEQUENCE:
        raise ContractError(f"{field} must be an integer in the admitted range")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ContractError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def admit_envelope(raw: Mapping[str, Any]) -> DeliveryEnvelope:
    """Validate and detach an exact-field metadata envelope."""
    if type(raw) is not dict:
        raise ContractError("envelope must be a native dict")
    if len(raw) != len(_FIELDS):
        raise ContractError("envelope fields do not match the versioned schema")
    keys = tuple(raw.keys())
    if any(type(key) is not str or len(key) > 32 for key in keys):
        raise ContractError("envelope keys must be short native strings")
    if set(keys) != _FIELDS:
        raise ContractError("envelope fields do not match the versioned schema")
    if type(raw["schema"]) is not str or raw["schema"] != SCHEMA:
        raise ContractError("unsupported schema")
    if type(raw["provenance"]) is not str or raw["provenance"] != PROVENANCE:
        raise ContractError("only PUBLIC_SYNTHETIC metadata is admitted")
    causation = raw["causation_id"]
    if causation is not None:
        causation = _token(causation, "causation_id")
    return DeliveryEnvelope(
        schema=SCHEMA, provenance=PROVENANCE,
        node_id=_token(raw["node_id"], "node_id"),
        stream=_token(raw["stream"], "stream"),
        subject=_token(raw["subject"], "subject"),
        event_id=_token(raw["event_id"], "event_id"),
        delivery_id=_token(raw["delivery_id"], "delivery_id"),
        sequence=_counter(raw["sequence"], "sequence"),
        tick=_counter(raw["tick"], "tick"),
        payload_sha256=_digest(raw["payload_sha256"], "payload_sha256"),
        causation_id=causation,
    )


def _admit_envelope_object(envelope: Any) -> DeliveryEnvelope:
    """Reject foreign objects before invoking any caller-defined method."""
    if type(envelope) is not DeliveryEnvelope:
        raise ContractError("native DeliveryEnvelope required")
    return admit_envelope(envelope.to_dict())


def delivery_key(envelope: DeliveryEnvelope) -> str:
    """Return a stable deduplication key scoped to node, stream and delivery ID."""
    admitted = _admit_envelope_object(envelope)
    material = "\n".join((admitted.node_id, admitted.stream, admitted.delivery_id))
    return hashlib.sha256(material.encode("ascii")).hexdigest()


def plan_delivery(
    envelope: DeliveryEnvelope,
    *,
    seen_delivery_keys: Sequence[str] = (),
    last_sequence: int | None = None,
) -> DeliveryPlan:
    """Return an advisory deterministic consumer decision; never mutates state."""
    admitted = _admit_envelope_object(envelope)
    if type(seen_delivery_keys) not in (list, tuple) or len(seen_delivery_keys) > MAX_SEEN:
        raise ContractError("seen_delivery_keys must be a bounded native list or tuple")
    for item in seen_delivery_keys:
        _digest(item, "seen_delivery_key")
    if last_sequence is not None:
        last_sequence = _counter(last_sequence, "last_sequence")
    key = delivery_key(admitted)
    if key in seen_delivery_keys:
        return DeliveryPlan("DUPLICATE", key, None, "delivery key was already observed")
    if last_sequence is not None:
        if admitted.sequence <= last_sequence:
            return DeliveryPlan("STALE", key, None, "sequence does not advance consumer cursor")
        if admitted.sequence != last_sequence + 1:
            return DeliveryPlan("GAP", key, last_sequence + 1, "consumer must reconcile missing sequence")
    next_sequence = admitted.sequence + 1 if admitted.sequence < MAX_SEQUENCE else None
    return DeliveryPlan("ACCEPT_CANDIDATE", key, next_sequence, "metadata is admissible; external commit is still required")


def canonical_bytes(envelope: DeliveryEnvelope) -> bytes:
    """Canonical detached JSON bytes for a metadata-only envelope."""
    admitted = _admit_envelope_object(envelope)
    return json.dumps(admitted.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def receipt_digest(envelope: DeliveryEnvelope, state: str) -> str:
    if type(state) is not str or state not in {"ACCEPT_CANDIDATE", "DUPLICATE", "STALE", "GAP"}:
        raise ContractError("unsupported receipt state")
    return hashlib.sha256(canonical_bytes(envelope) + b"\n" + state.encode("ascii")).hexdigest()
