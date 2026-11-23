"""Optional, read-only logical-tick barrier for synthetic room and body frames.

This module does not attest the real origin of physics measurements, and its
SHA-256 values are deterministic checksums, not signatures or writer leases.
It never performs actuation or mutates the Aquarium/Noosphere journal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

from .physics_observation import validate_virtual_physics_observation
from .world_room import Room, RoomState, _revision, _valid

SCHEMA = "mojum.world-physics.barrier/1"
MAX_TICK = 2**63 - 1
MIN_DT_NS = 1_000
MAX_DT_NS = 1_000_000_000
MAX_CANONICAL_BYTES = 2 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FIELDS = frozenset({
    "schema", "tick", "time_ns", "dt_ns", "room_digest",
    "room_revision", "body_revision", "physics_backend",
    "previous_commit", "commit",
})


def _sha_string(value: object) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _canonical_bytes(value: object) -> bytes:
    try:
        raw = json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise ValueError("invalid canonical frame") from exc
    if len(raw) > MAX_CANONICAL_BYTES:
        raise ValueError("frame exceeds canonical byte budget")
    return raw


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def body_frame_revision(payload: object) -> str:
    """Content checksum of a strictly validated detached virtual body packet."""
    packet = validate_virtual_physics_observation(payload)
    return _digest(packet)


def _check_clock(tick: object, dt_ns: object) -> int:
    if type(tick) is not int or not 0 <= tick <= MAX_TICK:
        raise ValueError("invalid logical tick")
    if type(dt_ns) is not int or not MIN_DT_NS <= dt_ns <= MAX_DT_NS:
        raise ValueError("invalid logical step duration")
    if tick > MAX_TICK // dt_ns:
        raise ValueError("logical timestamp overflows int64")
    return tick * dt_ns


def verify_barrier_receipt(receipt: object) -> bool:
    """Verify a receipt's exact shape, clock and self-checksum (not its origin)."""
    if type(receipt) is not dict or set(receipt) != _FIELDS:
        raise ValueError("invalid barrier receipt fields")
    if type(receipt["schema"]) is not str or receipt["schema"] != SCHEMA:
        raise ValueError("unsupported barrier receipt schema")
    timestamp = _check_clock(receipt["tick"], receipt["dt_ns"])
    if type(receipt["time_ns"]) is not int or timestamp != receipt["time_ns"]:
        raise ValueError("inconsistent barrier timestamp")
    if not all(_sha_string(receipt[field]) for field in (
        "room_digest", "room_revision", "body_revision", "commit",
    )):
        raise ValueError("invalid barrier revision")
    prev = receipt["previous_commit"]
    if prev is not None and not _sha_string(prev):
        raise ValueError("invalid previous commit")
    if type(receipt["physics_backend"]) is not str or receipt["physics_backend"] not in (
        "fixture", "mujoco",
    ):
        raise ValueError("unsupported physics backend")
    actual = _digest({key: receipt[key] for key in _FIELDS - {"commit"}})
    if not hmac.compare_digest(receipt["commit"], actual):
        raise ValueError("barrier receipt checksum mismatch")
    return True


def align_world_physics_frame(
    room: Room,
    state: RoomState,
    physics: object,
    *,
    dt_ns: int,
    expected_room_revision: str,
    expected_body_revision: str,
    previous: dict | None = None,
) -> dict:
    """Pair an already-measured room and passive body reading at one exact tick.

    The caller supplies independently obtained expected revisions; recomputing
    them from untrusted inputs and treating them as trustworthy would be a TOCTOU
    mistake. Previous is optional for standalone snapshots. For a causal chain,
    pass an externally trusted prior receipt and independently anchor its commit.
    """
    if type(room) is not Room or type(state) is not RoomState:
        raise ValueError("room and room state required")
    if not _sha_string(expected_room_revision) or not _sha_string(expected_body_revision):
        raise ValueError("expected revisions must be exact sha256 strings")
    timestamp = _check_clock(state.tick, dt_ns)

    # Re-check current geometry and state, not merely the caller's CAS token.
    # RoomState is frozen, but forged or out-of-room states can be built.
    try:
        checked_room = Room(room.width, room.height, room.obstacles)
        valid = _valid(checked_room, state.x, state.y)
        recomputed = _revision(checked_room, state.tick, state.x, state.y) if valid else None
    except (ValueError, TypeError, OverflowError, AttributeError) as exc:
        raise ValueError("invalid room geometry or position") from exc
    if not valid or not _sha_string(state.revision):
        raise ValueError("invalid room state")
    if not (hmac.compare_digest(recomputed, state.revision)
            and hmac.compare_digest(state.revision, expected_room_revision)):
        raise ValueError("stale or forged room revision")

    checked_physics = validate_virtual_physics_observation(physics)
    if checked_physics["tick"] != state.tick or checked_physics["time_ns"] != timestamp:
        raise ValueError("world/physics logical clock mismatch")
    body_digest = _digest(checked_physics)
    if not hmac.compare_digest(body_digest, expected_body_revision):
        raise ValueError("stale or substituted physics revision")

    room_digest = _digest([checked_room.width, checked_room.height, checked_room.obstacles])
    prev_commit = None
    if previous is not None:
        verify_barrier_receipt(previous)
        if previous["tick"] + 1 != state.tick:
            raise ValueError("duplicate, skipped or reordered barrier frame")
        if previous["dt_ns"] != dt_ns or previous["time_ns"] >= timestamp:
            raise ValueError("barrier clock changed or regressed")
        if (previous["room_digest"] != room_digest
                or previous["physics_backend"] != checked_physics["backend"]):
            raise ValueError("room geometry or physics backend switched without a new chain")
        prev_commit = previous["commit"]

    receipt = {
        "schema": SCHEMA,
        "tick": state.tick,
        "time_ns": timestamp,
        "dt_ns": dt_ns,
        "room_digest": room_digest,
        "room_revision": state.revision,
        "body_revision": body_digest,
        "physics_backend": checked_physics["backend"],
        "previous_commit": prev_commit,
    }
    receipt["commit"] = _digest(receipt)
    return receipt
