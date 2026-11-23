"""Bounded, read-only point-to-target visibility for the synthetic 2D room.

Not an authoritative vision sensor, a physics engine, an optical model, or an
external-world input. The caller must explicitly opt into PUBLIC_SYNTHETIC data.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from mojum.world_room import Room, RoomState, _finite_scalar, _revision, _segment_hits_rect, _valid

FRAME_SCHEMA = "mojum.world.line-of-sight/1"
FRAME_PROVENANCE = "deterministic-point-room-target-visibility/v1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"
MAX_ROOM_EXTENT_M = 10_000.0
MAX_DISTANCE_M = 1_000.0
MAX_OBSTACLES = 256
MAX_TARGETS = 64
MAX_TICK = 10_000_000
_TARGET_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z", re.ASCII)


def _coordinate(value: object) -> bool:
    return type(value) in (int, float) and _finite_scalar(value)


def _validate_source(room: Room, state: RoomState, expected_tick: int,
                     expected_revision: str) -> None:
    if type(room) is not Room or type(state) is not RoomState:
        raise ValueError("invalid room or state type")
    if (not _coordinate(room.width) or not _coordinate(room.height)
            or not 0 < room.width <= MAX_ROOM_EXTENT_M
            or not 0 < room.height <= MAX_ROOM_EXTENT_M):
        raise ValueError("room dimensions exceed witness limits")
    if type(room.obstacles) is not tuple or len(room.obstacles) > MAX_OBSTACLES:
        raise ValueError("invalid or oversized obstacles")
    # Frozen Python objects can be altered via object.__setattr__. Recheck the
    # canonical constructor constraints rather than trusting an old instance.
    try:
        reconstructed = Room(room.width, room.height, room.obstacles)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid room geometry") from None
    if reconstructed.obstacles != room.obstacles:
        raise ValueError("noncanonical room geometry")
    if (type(state.tick) is not int or not 0 <= state.tick <= MAX_TICK
            or type(expected_tick) is not int or expected_tick != state.tick
            or not _coordinate(state.x) or not _coordinate(state.y)
            or not _valid(room, state.x, state.y)):
        raise ValueError("invalid or stale room state")
    if (type(state.revision) is not str or type(expected_revision) is not str
            or state.revision != expected_revision
            or state.revision != _revision(room, state.tick, state.x, state.y)):
        raise ValueError("stale or corrupted room revision")


def read_target_visibility(
    room: Room,
    state: RoomState,
    targets: list[tuple[str, int | float, int | float]] | tuple[tuple[str, int | float, int | float], ...],
    *,
    expected_tick: int,
    expected_revision: str,
    visibility: str,
    opt_in: bool = False,
) -> dict:
    """Return detached, checksummed PUBLIC_SYNTHETIC target-line observations.

    Targets are ordered ``(id, x, y)`` triples in room coordinates. Closed
    obstacle edges count as occlusion; ``blocking_obstacle_index`` is the first
    *declared* obstacle intersecting the segment, not necessarily the nearest.
    Both endpoints must lie inside the room, but a target can lie on/in an
    obstacle and is then occluded. Source state is never advanced or modified.
    """
    if type(opt_in) is not bool or opt_in is not True or visibility != PUBLIC_SYNTHETIC or type(visibility) is not str:
        raise ValueError("explicit PUBLIC_SYNTHETIC opt-in required")
    _validate_source(room, state, expected_tick, expected_revision)
    if type(targets) not in (list, tuple) or not 1 <= len(targets) <= MAX_TARGETS:
        raise ValueError("invalid target collection")

    # Admit every request before computing any observation to avoid returning
    # a partial result on malformed later targets.
    validated: list[tuple[str, int | float, int | float, float]] = []
    ids: set[str] = set()
    for item in targets:
        if type(item) not in (tuple, list) or len(item) != 3:
            raise ValueError("invalid target entry")
        target_id, x, y = item
        if (type(target_id) is not str or _TARGET_ID.fullmatch(target_id) is None
                or target_id in ids or not _coordinate(x) or not _coordinate(y)
                or not 0 <= x <= room.width or not 0 <= y <= room.height):
            raise ValueError("invalid, duplicate or outside target")
        distance = math.hypot(x - state.x, y - state.y)
        if not math.isfinite(distance) or distance > MAX_DISTANCE_M:
            raise ValueError("target exceeds distance limit")
        ids.add(target_id)
        validated.append((target_id, x, y, distance))

    observations = []
    for target_id, x, y, distance in validated:
        blocker = next(
            (index for index, rect in enumerate(room.obstacles)
             if _segment_hits_rect(state.x, state.y, x, y, rect)), None,
        )
        observations.append({
            "id": target_id,
            "position": [x, y],
            "distance_m": distance,
            "visible": blocker is None,
            "blocking_obstacle_index": blocker,
        })

    frame = {
        "schema": FRAME_SCHEMA,
        "visibility": PUBLIC_SYNTHETIC,
        "tick": state.tick,
        "room_revision": state.revision,
        "observer_position": [state.x, state.y],
        "targets": observations,
        "provenance": FRAME_PROVENANCE,
        "physical_contact": False,
    }
    canonical = json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False)
    frame["frame_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return frame
