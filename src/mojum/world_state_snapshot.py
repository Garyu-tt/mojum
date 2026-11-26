"""Detached, bounded CPU-only snapshots of the synthetic point-room state.

A SHA-256 fingerprint detects accidental corruption; it is NOT an authenticating
signature, journal checkpoint, or authorization to control a simulated body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re

from .world_room import Room, RoomState, _revision, _valid

SCHEMA = "mojum.world.point-room-state/1"
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_OBSTACLES = 4096
MAX_COORD_M = 1_000_000
MAX_TICK = 1_000_000_000
MAX_NUMBER_TOKEN_CHARS = 32
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class WorldSnapshotError(ValueError):
    """Invalid or unverifiable detached point-room snapshot."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True).encode("ascii")


def _coordinate(value: object, *, positive: bool = False) -> None:
    if type(value) not in (int, float):
        raise WorldSnapshotError("geometry requires exact int or float fields")
    if not -MAX_COORD_M <= value <= MAX_COORD_M or not math.isfinite(value):
        raise WorldSnapshotError("geometry exceeds finite coordinate budget")
    if positive and value <= 0:
        raise WorldSnapshotError("room dimensions and obstacle sizes must be positive")


def _checked(room: object, state: object) -> tuple[Room, RoomState]:
    """Rebuild input records before hashing, including constructor-bypassed objects."""
    if type(room) is not Room or type(state) is not RoomState:
        raise WorldSnapshotError("exact Room and RoomState required")
    try:
        width, height, obstacles = room.width, room.height, room.obstacles
        tick, x, y, revision = state.tick, state.x, state.y, state.revision
    except AttributeError as exc:
        raise WorldSnapshotError("missing room or state field") from exc

    _coordinate(width, positive=True)
    _coordinate(height, positive=True)
    if type(obstacles) is not tuple or len(obstacles) > MAX_OBSTACLES:
        raise WorldSnapshotError("invalid obstacle collection")
    detached = []
    for rectangle in obstacles:
        if type(rectangle) is not tuple or len(rectangle) != 4:
            raise WorldSnapshotError("obstacles require four exact coordinates")
        for index, value in enumerate(rectangle):
            _coordinate(value, positive=index >= 2)
        detached.append(tuple(rectangle))
    if type(tick) is not int or not 0 <= tick <= MAX_TICK:
        raise WorldSnapshotError("invalid logical tick")
    _coordinate(x)
    _coordinate(y)
    if type(revision) is not str or _SHA256.fullmatch(revision) is None:
        raise WorldSnapshotError("invalid room revision")
    try:
        verified_room = Room(width, height, tuple(detached))
        if not _valid(verified_room, x, y):
            raise WorldSnapshotError("position outside free room space")
        correct_revision = _revision(verified_room, tick, x, y)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorldSnapshotError("invalid point-room geometry") from exc
    if not hmac.compare_digest(revision, correct_revision):
        raise WorldSnapshotError("stale or tampered point-room revision")
    return verified_room, RoomState(tick, x, y, revision)


def encode_snapshot(room: Room, state: RoomState) -> bytes:
    """Encode a deterministic, non-authoritative room/state snapshot."""
    room, state = _checked(room, state)
    body = {
        "schema": SCHEMA,
        "room": {"width": room.width, "height": room.height,
                 "obstacles": [list(rect) for rect in room.obstacles]},
        "state": {"tick": state.tick, "x": state.x, "y": state.y,
                  "revision": state.revision},
    }
    fingerprint = hashlib.sha256(_canonical(body)).hexdigest()
    record = {**body, "fingerprint": fingerprint}
    wire = _canonical(record)
    if len(wire) > MAX_SNAPSHOT_BYTES:
        raise WorldSnapshotError("snapshot exceeds byte budget")
    return wire


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorldSnapshotError("duplicate JSON key")
        result[key] = value
    return result


def _int_token(token: str) -> int:
    if len(token) > MAX_NUMBER_TOKEN_CHARS:
        raise WorldSnapshotError("oversized JSON number token")
    return int(token)


def _float_token(token: str) -> float:
    if len(token) > MAX_NUMBER_TOKEN_CHARS:
        raise WorldSnapshotError("oversized JSON number token")
    value = float(token)
    if not math.isfinite(value):
        raise WorldSnapshotError("non-finite JSON number")
    return value


def _nonfinite(_token: str) -> None:
    raise WorldSnapshotError("non-finite JSON constant")


def decode_snapshot(wire: bytes) -> tuple[Room, RoomState]:
    """Restore only a detached fixture after strict wire and revision checks.

    No filesystem, network, canonical journal, live world or motor state is touched.
    """
    if type(wire) is not bytes or len(wire) > MAX_SNAPSHOT_BYTES:
        raise WorldSnapshotError("snapshot requires bounded immutable bytes")
    try:
        record = json.loads(wire.decode("utf-8", "strict"),
                            object_pairs_hook=_no_duplicates,
                            parse_int=_int_token, parse_float=_float_token,
                            parse_constant=_nonfinite)
    except WorldSnapshotError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError,
            ValueError, OverflowError) as exc:
        raise WorldSnapshotError("invalid snapshot JSON") from exc
    if type(record) is not dict or set(record) != {
        "schema", "room", "state", "fingerprint"
    }:
        raise WorldSnapshotError("invalid snapshot envelope")
    if type(record["schema"]) is not str or record["schema"] != SCHEMA:
        raise WorldSnapshotError("unknown snapshot schema")
    fingerprint = record["fingerprint"]
    if type(fingerprint) is not str or _SHA256.fullmatch(fingerprint) is None:
        raise WorldSnapshotError("invalid snapshot fingerprint")
    room_raw, state_raw = record["room"], record["state"]
    if (type(room_raw) is not dict
            or set(room_raw) != {"width", "height", "obstacles"}
            or type(state_raw) is not dict
            or set(state_raw) != {"tick", "x", "y", "revision"}):
        raise WorldSnapshotError("invalid room/state fields")
    obstacles = room_raw["obstacles"]
    if type(obstacles) is not list or len(obstacles) > MAX_OBSTACLES:
        raise WorldSnapshotError("invalid obstacle payload")
    if any(type(rectangle) is not list or len(rectangle) != 4
           for rectangle in obstacles):
        raise WorldSnapshotError("invalid obstacle rectangle")
    try:
        # Neither a dict with arbitrary keys nor a forged state can skip the
        # independent geometry and canonical room revision reconstruction.
        room = Room(room_raw["width"], room_raw["height"],
                    tuple(tuple(rectangle) for rectangle in obstacles))
        state = RoomState(state_raw["tick"], state_raw["x"],
                          state_raw["y"], state_raw["revision"])
        room, state = _checked(room, state)
    except (ValueError, TypeError, OverflowError) as exc:
        raise WorldSnapshotError("invalid room/state snapshot") from exc
    expected = encode_snapshot(room, state)
    # This detects both a changed checksum and *noncanonical* spellings,
    # duplicate-free but otherwise misleading numeric forms, or whitespace.
    if not hmac.compare_digest(expected, wire):
        raise WorldSnapshotError("fingerprint mismatch or noncanonical snapshot")
    return room, state
