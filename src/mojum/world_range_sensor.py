"""Opt-in read-only directional range sensor for the deterministic point-room.

This is synthetic geometry, not physical contact or an actuator observation.
The four rays and their ordering are fixed so a recorded frame is replayable.
"""

from __future__ import annotations

import hashlib
import json

from mojum.world_room import Room, RoomState, _finite_scalar, _revision, _valid

FRAME_SCHEMA = "mojum.world-range-frame/1"
FRAME_PROVENANCE = "deterministic-point-room-range-fixture/v1"
MAX_RANGE_M = 1000.0
DIRECTIONS = ("east", "north", "west", "south")


def _validate(room: Room, state: RoomState, expected_revision: str, max_range_m: float) -> None:
    # Validate the exact room/state used to generate the revision, not a caller-
    # supplied tick or a newly advanced state. No clock or action is involved.
    if type(room) is not Room or type(state) is not RoomState:
        raise ValueError("room and state must be point-room fixture objects")
    if type(state.tick) is not int or state.tick < 0 or not _valid(room, state.x, state.y):
        raise ValueError("invalid room state")
    if (type(expected_revision) is not str
            or type(state.revision) is not str
            or state.revision != expected_revision
            or state.revision != _revision(room, state.tick, state.x, state.y)):
        raise ValueError("stale or corrupted room revision")
    if not _finite_scalar(max_range_m) or not 0 < max_range_m <= MAX_RANGE_M:
        raise ValueError("invalid range limit")


def _nearest(room: Room, x: float, y: float, direction: str):
    """Distance to the nearest closed wall/obstacle along one cardinal ray."""
    if direction == "east":
        nearest = room.width - x
    elif direction == "north":
        nearest = room.height - y
    elif direction == "west":
        nearest = x
    else:
        nearest = y
    hit = "boundary"
    obstacle_index = None

    for index, (ox, oy, ow, oh) in enumerate(room.obstacles):
        distance = None
        if direction == "east" and oy <= y <= oy + oh and ox >= x:
            distance = ox - x
        elif direction == "west" and oy <= y <= oy + oh and ox + ow <= x:
            distance = x - (ox + ow)
        elif direction == "north" and ox <= x <= ox + ow and oy >= y:
            distance = oy - y
        elif direction == "south" and ox <= x <= ox + ow and oy + oh <= y:
            distance = y - (oy + oh)

        # A ray grazing an obstacle's edge counts as a hit. On equal-distance
        # obstacles, the lowest obstacle index wins; never depend on hash order.
        if distance is not None and (distance < nearest or (distance == nearest and hit == "boundary")):
            nearest = distance
            hit = "obstacle"
            obstacle_index = index
    return nearest, hit, obstacle_index


def read_range_frame(
    room: Room,
    state: RoomState,
    *,
    expected_revision: str,
    max_range_m: float = 10.0,
) -> dict:
    """Measure four cardinal rays without moving the organism or touching IO.

    The state revision commits to room geometry, tick and position. Invalid
    or stale state is rejected, rather than silently producing sensory evidence.
    Clipped rays report hit='range_limit' and obstacle_index=None;
    a genuine wall/obstacle exactly at the limit is still reported as a hit.
    """
    _validate(room, state, expected_revision, max_range_m)
    rays = []
    for direction in DIRECTIONS:
        distance, hit, obstacle_index = _nearest(room, state.x, state.y, direction)
        if distance > max_range_m:
            distance, hit, obstacle_index = max_range_m, "range_limit", None
        rays.append({
            "direction": direction,
            "distance_m": distance,
            "hit": hit,
            "obstacle_index": obstacle_index,
        })
    frame = {
        "schema": FRAME_SCHEMA,
        "tick": state.tick,
        "room_revision": state.revision,
        "position": [state.x, state.y],
        "max_range_m": max_range_m,
        "rays": rays,
        "provenance": FRAME_PROVENANCE,
        "physical_contact": False,
    }
    # Unkeyed checksum for deterministic frame identity, NOT authentication.
    canonical = json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False)
    frame["frame_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return frame
