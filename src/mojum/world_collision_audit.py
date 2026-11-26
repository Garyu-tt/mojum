"""Offline exact-rational differential audit of the synthetic point-room collider.

Reference geometry uses rational interpretations of input Python numbers.
This is not a replacement for world_room.advance or real physics.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
import random

from mojum.world_room import Room, RoomState, advance

SCHEMA = "mojum.world.collision-audit/1"
MAX_ROOM_M = 100_000
MAX_OBSTACLES = 64
MAX_STEPS = 128
MAX_TICK = 1_000_000
MAX_STEP_M = 1_000


def _number(value: object, limit: float) -> bool:
    return (type(value) in (int, float) and -limit <= value <= limit
            and math.isfinite(value))


def _fraction(value: int | float) -> Fraction:
    # Fraction(float) uses the exact IEEE-754 binary value, not str(value).
    return Fraction(value)


def _intersects_closed_aabb(x0: Fraction, y0: Fraction,
                            dx: Fraction, dy: Fraction, rect: tuple) -> bool:
    x, y, width, height = (_fraction(v) for v in rect)
    lo, hi = Fraction(0), Fraction(1)
    for p, velocity, start, end in (
        (x0, dx, x, x + width), (y0, dy, y, y + height),
    ):
        if velocity == 0:
            if not start <= p <= end:
                return False
            continue
        t0, t1 = (start - p) / velocity, (end - p) / velocity
        lo = max(lo, min(t0, t1))
        hi = min(hi, max(t0, t1))
        if lo > hi:
            return False
    return True  # Exact tangent / endpoint contact counts as blocked.


def _reference_blocked(room: Room, state: RoomState,
                       dx: int | float, dy: int | float) -> bool:
    x, y, vx, vy = map(_fraction, (state.x, state.y, dx, dy))
    end_x, end_y = x + vx, y + vy
    if not (0 <= end_x <= _fraction(room.width)
            and 0 <= end_y <= _fraction(room.height)):
        return True
    return any(_intersects_closed_aabb(x, y, vx, vy, rect)
               for rect in room.obstacles)


def _hash(value: object) -> str:
    wire = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=True)
    return hashlib.sha256(wire.encode("ascii")).hexdigest()


def _validate(room: Room, state: RoomState,
              dx: int | float, dy: int | float) -> None:
    if type(room) is not Room or type(state) is not RoomState:
        raise ValueError("only canonical synthetic Room and RoomState are supported")
    if (not _number(room.width, MAX_ROOM_M)
            or not _number(room.height, MAX_ROOM_M)
            or len(room.obstacles) > MAX_OBSTACLES):
        raise ValueError("room exceeds collision-audit bounds")
    for rect in room.obstacles:
        if any(not _number(v, MAX_ROOM_M) for v in rect):
            raise ValueError("obstacle exceeds collision-audit bounds")
    if (type(state.tick) is not int or not 0 <= state.tick < MAX_TICK
            or not _number(state.x, MAX_ROOM_M)
            or not _number(state.y, MAX_ROOM_M)
            or type(state.revision) is not str):
        raise ValueError("invalid collision-audit state")
    if (not _number(dx, MAX_STEP_M) or not _number(dy, MAX_STEP_M)
            or math.hypot(dx, dy) > MAX_STEP_M):
        raise ValueError("invalid collision-audit displacement")


def audit_step(room: Room, state: RoomState, *,
               dx: int | float, dy: int | float) -> tuple[RoomState, dict]:
    """Compare one canonical step to an independently evaluated exact path.

    Always advances using the canonical simulation even on a disagreement.
    A receipt is a checksum, never a signature or policy authority.
    """
    _validate(room, state, dx, dy)
    reference_blocked = _reference_blocked(room, state, dx, dy)
    next_state, frame = advance(room, state, tick=state.tick + 1,
                                dx=dx, dy=dy, expected_revision=state.revision)
    if (type(frame) is not dict or type(frame.get("blocked")) is not bool
            or type(frame.get("physical_contact")) is not bool
            or frame["physical_contact"] is not False
            or frame.get("revision") != next_state.revision
            or frame.get("tick") != next_state.tick):
        raise ValueError("invalid canonical synthetic observation")
    observed_blocked = frame["blocked"]
    receipt = {
        "schema": SCHEMA, "visibility": "PUBLIC_SYNTHETIC",
        "tick": next_state.tick,
        "source_revision": state.revision,
        "next_revision": next_state.revision,
        "input_sha256": _hash([room.width, room.height, room.obstacles,
                               state.tick, state.x, state.y,
                               state.revision, dx, dy]),
        "reference_blocked": reference_blocked,
        "runtime_blocked": observed_blocked,
        "agrees": reference_blocked is observed_blocked,
        "physical_contact": False,
    }
    receipt["receipt_sha256"] = _hash(receipt)
    return next_state, receipt


def audit_path(room: Room, start: RoomState, steps: list | tuple) -> dict:
    """Bounded offline audit. Disagreements are reported, not patched."""
    if type(steps) not in (list, tuple) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("invalid collision-audit step count")
    # Validate the entire batch before running its first step.
    for move in steps:
        if type(move) not in (list, tuple) or len(move) != 2:
            raise ValueError("invalid step shape")
        _validate(room, start, move[0], move[1])
    if start.tick + len(steps) > MAX_TICK:
        raise ValueError("collision-audit tick budget exceeded")
    state = start
    receipts = []
    for dx, dy in steps:
        state, receipt = audit_step(room, state, dx=dx, dy=dy)
        receipts.append(receipt)
    mismatch_ticks = [r["tick"] for r in receipts if not r["agrees"]]
    summary = {
        "schema": SCHEMA, "visibility": "PUBLIC_SYNTHETIC",
        "checked": len(receipts), "mismatch_count": len(mismatch_ticks),
        "mismatch_ticks": mismatch_ticks,
        "start_revision": start.revision, "final_revision": state.revision,
        "receipts_sha256": _hash(receipts), "physical_contact": False,
    }
    summary["summary_sha256"] = _hash(summary)
    return summary


def audit_seeded(room: Room, start: RoomState, *,
                 seed: int, count: int = 32) -> dict:
    """Repeatable finite walk using getrandbits, not a clock or global RNG."""
    if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("seed must be uint32")
    if type(count) is not int or not 1 <= count <= MAX_STEPS:
        raise ValueError("count outside bounded walk")
    rng = random.Random(seed)
    steps = [((rng.getrandbits(10) - 512) / 1024,
              (rng.getrandbits(10) - 512) / 1024)
             for _ in range(count)]
    return audit_path(room, start, steps)
