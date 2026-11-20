"""Deterministic point-room fixture; NOT MuJoCo physics."""
from dataclasses import dataclass
import hashlib
import json
import math

@dataclass(frozen=True)
class Room:
    width: float
    height: float
    obstacles: tuple[tuple[float, float, float, float], ...] = ()

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in (self.width, self.height)):
            raise ValueError("invalid dimensions")
        for x, y, w, h in self.obstacles:
            if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
                raise ValueError("invalid obstacle")
            if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
                raise ValueError("obstacle outside room")

@dataclass(frozen=True)
class RoomState:
    tick: int
    x: float
    y: float
    revision: str

def _valid(room, x, y):
    return (math.isfinite(x) and math.isfinite(y)
            and 0 <= x <= room.width and 0 <= y <= room.height
            and not any(a <= x <= a+w and b <= y <= b+h
                        for a, b, w, h in room.obstacles))

def _revision(room, tick, x, y):
    payload = [room.width, room.height, room.obstacles, tick, x, y]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

def initial_state(room: Room, x: float, y: float) -> RoomState:
    if not _valid(room, x, y):
        raise ValueError("invalid initial position")
    return RoomState(0, x, y, _revision(room, 0, x, y))

def advance(room: Room, state: RoomState, *, tick: int, dx: float, dy: float,
            expected_revision: str) -> tuple[RoomState, dict]:
    """One logical tick; segment sampling prevents fixture obstacle tunneling."""
    if state.revision != expected_revision or state.revision != _revision(room, state.tick, state.x, state.y):
        raise ValueError("stale or corrupted revision")
    if tick != state.tick + 1 or not all(math.isfinite(v) for v in (dx, dy)):
        raise ValueError("invalid tick or displacement")
    steps = max(1, math.ceil(math.hypot(dx, dy) / .01))
    if steps > 100000:
        raise ValueError("displacement exceeds fixture budget")
    blocked = any(not _valid(room, state.x + dx*i/steps, state.y + dy*i/steps)
                  for i in range(1, steps+1))
    x, y = (state.x, state.y) if blocked else (state.x+dx, state.y+dy)
    next_state = RoomState(tick, x, y, _revision(room, tick, x, y))
    return next_state, {"tick": tick, "revision": next_state.revision,
                        "position": [x, y], "blocked": blocked,
                        "provenance": "deterministic-point-room-fixture/v1",
                        "physical_contact": False}
