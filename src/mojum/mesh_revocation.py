"""Offline, signed peer-revocation snapshots for Continuity Mesh.

This answers only whether an enrolled peer appears in a fresh signed
revocation snapshot. A clear result is not authentication or enrollment.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import weakref
from typing import Any

SNAPSHOT_SCHEMA = "mojum.mesh.revocations/1"
MAX_ENTRIES = 4096
MAX_AUTHORITY_KEYS = 128
MAX_ENVELOPE_BYTES = 1_048_576
MAX_IDENTIFIER_LENGTH = 128
MAX_TICK = (1 << 63) - 1
MAX_VALIDITY_TICKS = 1_000_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)*", re.ASCII)
_ENVELOPE_FIELDS = {"schema", "authority_id", "key_id", "generation",
                    "issued_at_tick", "expires_at_tick", "revocations", "signature"}
_REVOCATION_FIELDS = {"enrollment_id", "node_id", "sequence", "revoked_at_tick"}


class MeshRevocationError(ValueError):
    """Malformed, untrusted, stale, or unverifiable revocation input."""


@dataclass(frozen=True, slots=True)
class RevocationEntry:
    enrollment_id: str
    node_id: str
    sequence: int
    revoked_at_tick: int


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class VerifiedRevocationSnapshot:
    """Verified snapshot object; membership is tracked by object identity."""

    authority_id: str
    key_id: str
    generation: int
    issued_at_tick: int
    expires_at_tick: int
    entries: tuple[RevocationEntry, ...]
    digest: str


# Keep the authoritative verified fields outside the public dataclass. A
# frozen dataclass can still be changed with object.__setattr__; storing only
# immutable primitive tuples here prevents such changes from altering a
# revocation decision. Weak keys preserve identity-based provenance without
# retaining unused snapshots forever.
_VERIFIED_SNAPSHOTS: weakref.WeakKeyDictionary[
    VerifiedRevocationSnapshot,
    tuple[str, str, int, int, int, tuple[tuple[str, str, int, int], ...], str],
] = weakref.WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class RevocationDecision:
    operation: str
    node_id: str
    enrollment_id: str
    allowed_by_revocation_check: bool
    reason: str
    generation: int
    snapshot_digest: str


def _identifier(value: Any, field: str) -> str:
    if (type(value) is not str or not 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
            or _IDENTIFIER.fullmatch(value) is None):
        raise MeshRevocationError(f"invalid {field}")
    return value


def _tick(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TICK:
        raise MeshRevocationError(f"invalid {field}")
    return value


def _exact_native_keys(value: dict, expected: set[str], field: str) -> None:
    # Bound cardinality before enumerating caller-controlled keys.
    if type(value) is not dict or len(value) != len(expected):
        raise MeshRevocationError(f"{field} has an invalid shape")
    keys = tuple(dict.keys(value))
    # Run before set/equality: hostile key objects must not execute __hash__/__eq__.
    if any(type(key) is not str for key in keys):
        raise MeshRevocationError(f"{field} keys must be native strings")
    if set(keys) != expected:
        raise MeshRevocationError(f"{field} has an invalid shape")


def _canonical(payload: dict) -> bytes:
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise MeshRevocationError("payload is not canonical JSON data") from exc
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise MeshRevocationError("revocation snapshot exceeds byte budget")
    return encoded


def _validate_payload(payload: Any) -> tuple[dict, tuple[RevocationEntry, ...]]:
    if type(payload) is not dict:
        raise MeshRevocationError("snapshot payload must be a native dict")
    expected = _ENVELOPE_FIELDS - {"signature"}
    _exact_native_keys(payload, expected, "snapshot payload")
    if type(payload["schema"]) is not str or payload["schema"] != SNAPSHOT_SCHEMA:
        raise MeshRevocationError("unsupported snapshot schema")
    authority_id = _identifier(payload["authority_id"], "authority id")
    key_id = _identifier(payload["key_id"], "key id")
    generation = _tick(payload["generation"], "generation")
    if generation < 1:
        raise MeshRevocationError("generation must be positive")
    issued = _tick(payload["issued_at_tick"], "issued_at_tick")
    expires = _tick(payload["expires_at_tick"], "expires_at_tick")
    if expires <= issued or expires - issued > MAX_VALIDITY_TICKS:
        raise MeshRevocationError("invalid snapshot validity window")

    raw_entries = payload["revocations"]
    if type(raw_entries) is not list or len(raw_entries) > MAX_ENTRIES:
        raise MeshRevocationError("invalid revocation collection")
    entries: list[RevocationEntry] = []
    seen_enrollments: set[str] = set()
    seen_nodes: set[str] = set()
    previous = ""
    for raw in raw_entries:
        if type(raw) is not dict:
            raise MeshRevocationError("revocation entry must be a native dict")
        _exact_native_keys(raw, _REVOCATION_FIELDS, "revocation entry")
        enrollment = _identifier(raw["enrollment_id"], "enrollment id")
        node = _identifier(raw["node_id"], "node id")
        sequence = _tick(raw["sequence"], "revocation sequence")
        revoked_at = _tick(raw["revoked_at_tick"], "revoked_at_tick")
        if sequence < 1 or revoked_at > issued:
            raise MeshRevocationError("revocation is not effective at snapshot issue time")
        if enrollment in seen_enrollments or node in seen_nodes:
            raise MeshRevocationError("duplicate revocation identity")
        if previous and enrollment <= previous:
            raise MeshRevocationError("revocations must be sorted by enrollment id")
        seen_enrollments.add(enrollment)
        seen_nodes.add(node)
        previous = enrollment
        entries.append(RevocationEntry(enrollment, node, sequence, revoked_at))

    normalized = {
        "schema": SNAPSHOT_SCHEMA, "authority_id": authority_id, "key_id": key_id,
        "generation": generation, "issued_at_tick": issued,
        "expires_at_tick": expires,
        "revocations": [{"enrollment_id": e.enrollment_id, "node_id": e.node_id,
                         "sequence": e.sequence, "revoked_at_tick": e.revoked_at_tick}
                        for e in entries],
    }
    return normalized, tuple(entries)


def sign_snapshot(*, authority_id: str, key_id: str, generation: int,
                  issued_at_tick: int, expires_at_tick: int,
                  revocations: list[dict], authority_key: bytes) -> dict:
    """Build a deterministic HMAC envelope using an out-of-band authority key."""
    if type(authority_key) is not bytes or not 32 <= len(authority_key) <= 128:
        raise MeshRevocationError("authority key must be 32..128 native bytes")
    payload = {"schema": SNAPSHOT_SCHEMA, "authority_id": authority_id,
               "key_id": key_id, "generation": generation,
               "issued_at_tick": issued_at_tick, "expires_at_tick": expires_at_tick,
               "revocations": revocations}
    normalized, _ = _validate_payload(payload)
    signature = hmac.new(authority_key, _canonical(normalized), hashlib.sha256).hexdigest()
    return {**normalized, "signature": signature}


def verify_snapshot(envelope: Any, *, authority_keys: dict[str, bytes],
                    now_tick: int, minimum_generation: int = 0
                    ) -> VerifiedRevocationSnapshot:
    """Verify authority signature, key map, validity and caller's rollback floor."""
    if type(authority_keys) is not dict or len(authority_keys) > MAX_AUTHORITY_KEYS:
        raise MeshRevocationError("invalid authority key map")
    # Validate every entry before lookup to prevent hostile-key equality.
    for candidate_id, candidate_key in authority_keys.items():
        _identifier(candidate_id, "authority key id")
        if type(candidate_key) is not bytes or not 32 <= len(candidate_key) <= 128:
            raise MeshRevocationError("invalid authority key material")

    if type(envelope) is not dict:
        raise MeshRevocationError("snapshot envelope must be a native dict")
    _exact_native_keys(envelope, _ENVELOPE_FIELDS, "snapshot envelope")
    signature = envelope["signature"]
    if (type(signature) is not str or len(signature) != 64
            or any(c not in "0123456789abcdef" for c in signature)):
        raise MeshRevocationError("invalid snapshot signature encoding")
    payload = {key: value for key, value in envelope.items() if key != "signature"}
    normalized, entries = _validate_payload(payload)
    key = authority_keys.get(normalized["key_id"])
    if key is None:
        raise MeshRevocationError("unknown authority key id")
    expected = hmac.new(key, _canonical(normalized), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise MeshRevocationError("invalid revocation snapshot signature")

    now = _tick(now_tick, "now_tick")
    floor = _tick(minimum_generation, "minimum_generation")
    if normalized["generation"] < floor:
        raise MeshRevocationError("revocation snapshot rollback detected")
    if not normalized["issued_at_tick"] <= now <= normalized["expires_at_tick"]:
        raise MeshRevocationError("revocation snapshot is not currently valid")
    digest = hashlib.sha256(_canonical({**normalized, "signature": signature})).hexdigest()
    verified = VerifiedRevocationSnapshot(
        authority_id=normalized["authority_id"], key_id=normalized["key_id"],
        generation=normalized["generation"], issued_at_tick=normalized["issued_at_tick"],
        expires_at_tick=normalized["expires_at_tick"], entries=entries, digest=digest,
    )
    _VERIFIED_SNAPSHOTS[verified] = (
        verified.authority_id,
        verified.key_id,
        verified.generation,
        verified.issued_at_tick,
        verified.expires_at_tick,
        tuple((entry.enrollment_id, entry.node_id, entry.sequence, entry.revoked_at_tick)
              for entry in entries),
        verified.digest,
    )
    return verified


def check_peer_access(snapshot: VerifiedRevocationSnapshot, *, node_id: str,
                      enrollment_id: str, operation: str,
                      now_tick: int) -> RevocationDecision:
    """Return only the revocation-list decision for one read/write attempt.

    A positive result means only that the pair is absent from this fresh,
    verified snapshot. It does not authenticate the peer or grant storage access.
    """
    if type(snapshot) is not VerifiedRevocationSnapshot:
        raise MeshRevocationError("a verified snapshot is required")
    verified_state = _VERIFIED_SNAPSHOTS.get(snapshot)
    if verified_state is None:
        raise MeshRevocationError("a verified snapshot is required")

    # Frozen dataclasses are a convenience, not a security boundary:
    # object.__setattr__ can alter even a slots/frozen instance. Refuse a
    # modified public view, and use the registry's primitive tuples below as
    # the authoritative state for every decision.
    (authority_id, key_id, generation, issued_at, expires_at,
     verified_entries, snapshot_digest) = verified_state
    try:
        if (type(snapshot.authority_id) is not str
                or type(snapshot.key_id) is not str
                or type(snapshot.generation) is not int
                or type(snapshot.issued_at_tick) is not int
                or type(snapshot.expires_at_tick) is not int
                or type(snapshot.entries) is not tuple
                or type(snapshot.digest) is not str):
            raise MeshRevocationError("verified snapshot object was modified")
        public_entries = []
        for entry in snapshot.entries:
            if (type(entry) is not RevocationEntry
                    or type(entry.enrollment_id) is not str
                    or type(entry.node_id) is not str
                    or type(entry.sequence) is not int
                    or type(entry.revoked_at_tick) is not int):
                raise MeshRevocationError("verified snapshot object was modified")
            public_entries.append((
                entry.enrollment_id, entry.node_id, entry.sequence, entry.revoked_at_tick
            ))
        if ((snapshot.authority_id, snapshot.key_id, snapshot.generation,
             snapshot.issued_at_tick, snapshot.expires_at_tick,
             tuple(public_entries), snapshot.digest) != verified_state):
            raise MeshRevocationError("verified snapshot object was modified")
    except AttributeError as exc:
        raise MeshRevocationError("verified snapshot object was modified") from exc

    node = _identifier(node_id, "node id")
    enrollment = _identifier(enrollment_id, "enrollment id")
    if type(operation) is not str or operation not in ("read", "write"):
        raise MeshRevocationError("operation must be read or write")
    now = _tick(now_tick, "now_tick")
    if not issued_at <= now <= expires_at:
        raise MeshRevocationError("verified snapshot expired or not yet valid")

    match = next((entry for entry in verified_entries
                  if entry[0] == enrollment or entry[1] == node), None)
    allowed, reason = True, "not_listed_as_revoked"
    if match is not None:
        if match[0] != enrollment or match[1] != node:
            allowed, reason = False, "revocation_identity_mismatch"
        elif match[3] <= now:
            allowed, reason = False, "peer_revoked"
    return RevocationDecision(
        operation=operation, node_id=node, enrollment_id=enrollment,
        allowed_by_revocation_check=allowed, reason=reason,
        generation=generation, snapshot_digest=snapshot_digest,
    )
