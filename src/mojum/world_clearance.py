"""Read-only nearest-surface sensor for the synthetic point-room fixture.

This is not physics, a motor command, or a physical contact sensor.
"""

import hashlib
import json
import math

from .world_room import Room, RoomState, _revision, _valid

SCHEMA = "mojum.world.point-room-clearance/1"
PROVENANCE = "deterministic-point-room-fixture/v1"
MAX_EXTENT_M = 1_000_000
MAX_RANGE_M = 1_000_000
MAX_TICK = 2**63 - 1


class ClearanceError(ValueError):
    """Unsupported or inconsistent synthetic room/sensor input."""


def _finite_number(value):
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def clearance_frame(room: Room, state: RoomState, *, max_range_m=100.0) -> dict:
    """Report the nearest closed obstacle or outer wall for one point position.

    Saturated readings reveal no nearest feature; ``distance_m`` is capped
    to the requested range. Ties use wall order W/E/S/N, then obstacle order.
    Geometry and state are detached, validated and revision-pinned. The
    digest detects accidental changes but is not authentication.
    """
    if type(room) is not Room or type(state) is not RoomState:
        raise ClearanceError("expected canonical Room and RoomState")
    if not (_finite_number(room.width) and _finite_number(room.height)
            and 0 < room.width <= MAX_EXTENT_M
            and 0 < room.height <= MAX_EXTENT_M):
        raise ClearanceError("room exceeds clearance sensor bounds")
    try:
        # A frozen dataclass can still be mutated with object.__setattr__.
        # Revalidate every obstacle rather than trusting old post-init checks.
        validated_room = Room(room.width, room.height, room.obstacles)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ClearanceError("invalid room geometry") from exc
    if (type(state.tick) is not int or not 0 <= state.tick <= MAX_TICK
            or not _finite_number(state.x) or not _finite_number(state.y)
            or type(state.revision) is not str
            or len(state.revision) != 64
            or not _valid(validated_room, state.x, state.y)):
        raise ClearanceError("invalid room state")
    if state.revision != _revision(validated_room, state.tick, state.x, state.y):
        raise ClearanceError("stale or corrupted room revision")
    if (not _finite_number(max_range_m)
            or not 0 < max_range_m <= MAX_RANGE_M):
        raise ClearanceError("invalid sensor range")

    x, y = state.x, state.y
    # Touching room walls is a valid point-room state and has zero clearance.
    wall_distances = (
        (x, "west"), (validated_room.width - x, "east"),
        (y, "south"), (validated_room.height - y, "north"),
    )
    nearest_distance, wall_name = min(wall_distances, key=lambda item: item[0])
    nearest = {"kind": "wall", "name": wall_name}

    for index, (left, bottom, width, height) in enumerate(validated_room.obstacles):
        # Point-to-closed-AABB Euclidean distance: diagonal corner distance
        # must not be mistaken for a cardinal-direction sensor reading.
        dx = max(left - x, 0, x - (left + width))
        dy = max(bottom - y, 0, y - (bottom + height))
        distance = math.hypot(dx, dy)
        if distance < nearest_distance:
            nearest_distance = distance
            nearest = {"kind": "obstacle", "index": index}

    saturated = nearest_distance > max_range_m
    payload = {
        "schema": SCHEMA,
        "tick": state.tick,
        "room_revision": state.revision,
        "max_range_m": float(max_range_m),
        "distance_m": float(max_range_m if saturated else nearest_distance),
        "saturated": saturated,
        "nearest": None if saturated else nearest,
        "provenance": PROVENANCE,
        "physical_contact": False,
        "actuating": False,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return {**payload, "digest_sha256": hashlib.sha256(encoded).hexdigest()}
