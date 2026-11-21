"""Deterministic point-room fixture; NOT MuJoCo physics."""
from dataclasses import dataclass
import hashlib
import json
import math

MAX_DISPLACEMENT_M = 1000.0


def displacement_within_budget(dx: float, dy: float) -> bool:
    """Whether the requested step fits the room's Euclidean distance limit."""
    return math.hypot(dx, dy) <= MAX_DISPLACEMENT_M


def _finite_scalar(value):
    """Strict finite JSON-compatible numeric scalar; bool is not geometry."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Room:
    width: float
    height: float
    obstacles: tuple[tuple[float, float, float, float], ...] = ()

    def __post_init__(self):
        if not all(_finite_scalar(v) and v > 0 for v in (self.width, self.height)):
            raise ValueError("invalid dimensions")
        if type(self.obstacles) not in (tuple, list) or len(self.obstacles) > 4096:
            raise ValueError("invalid obstacle collection")
        frozen = []
        for rect in self.obstacles:
            if type(rect) not in (tuple, list) or len(rect) != 4:
                raise ValueError("invalid obstacle")
            x, y, w, h = rect
            if not all(_finite_scalar(v) for v in rect) or w <= 0 or h <= 0:
                raise ValueError("invalid obstacle")
            if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
                raise ValueError("obstacle outside room")
            # Do not coerce int to float: existing room manifests distinguish
            # exact JSON number spellings, and replay depends on preserving them.
            frozen.append(tuple(rect))
        # Frozen dataclasses do not recursively freeze nested list inputs.
        # Detach from caller-owned lists so geometry cannot change mid-run.
        object.__setattr__(self, "obstacles", tuple(frozen))

@dataclass(frozen=True)
class RoomState:
    tick: int
    x: float
    y: float
    revision: str

def _valid(room, x, y):
    return (_finite_scalar(x) and _finite_scalar(y)
            and 0 <= x <= room.width and 0 <= y <= room.height
            and not any(a <= x <= a+w and b <= y <= b+h
                        for a, b, w, h in room.obstacles))

def _segment_hits_rect(x0, y0, x1, y1, rect):
    """Liang-Barsky closed AABB intersection; touching a wall counts as blocked."""
    left, bottom, width, height = rect
    dx, dy = x1 - x0, y1 - y0
    t_enter, t_leave = 0.0, 1.0
    for p, q in ((-dx, x0 - left),
                 ( dx, left + width - x0),
                 (-dy, y0 - bottom),
                 ( dy, bottom + height - y0)):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                if r > t_leave:
                    return False
                t_enter = max(t_enter, r)
            else:
                if r < t_enter:
                    return False
                t_leave = min(t_leave, r)
    return t_enter <= t_leave


def _revision(room, tick, x, y):
    payload = [room.width, room.height, room.obstacles, tick, x, y]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

def initial_state(room: Room, x: float, y: float) -> RoomState:
    if not isinstance(room, Room) or not _valid(room, x, y):
        raise ValueError("invalid initial position")
    return RoomState(0, x, y, _revision(room, 0, x, y))

def advance(room: Room, state: RoomState, *, tick: int, dx: float, dy: float,
            expected_revision: str) -> tuple[RoomState, dict]:
    """One logical tick; exact segment-vs-AABB test prevents tunneling."""
    if (not isinstance(room, Room) or not isinstance(state, RoomState)
            or type(state.tick) is not int or state.tick < 0
            or not _valid(room, state.x, state.y)):
        raise ValueError("invalid starting state")
    if (type(tick) is not int or tick != state.tick + 1
            or not _finite_scalar(dx) or not _finite_scalar(dy)):
        raise ValueError("invalid tick or displacement")
    if state.revision != expected_revision or state.revision != _revision(room, state.tick, state.x, state.y):
        raise ValueError("stale or corrupted revision")
    # Sampling in 1 cm increments can miss any thinner obstacle. Test the
    # whole movement segment against closed rectangle boundaries instead.
    if not displacement_within_budget(dx, dy):
        raise ValueError("displacement exceeds fixture budget")
    tx, ty = state.x + dx, state.y + dy
    blocked = (not _valid(room, tx, ty) or
               any(_segment_hits_rect(state.x, state.y, tx, ty, rect)
                   for rect in room.obstacles))
    x, y = (state.x, state.y) if blocked else (tx, ty)
    next_state = RoomState(tick, x, y, _revision(room, tick, x, y))
    return next_state, {"tick": tick, "revision": next_state.revision,
                        "position": [x, y], "blocked": blocked,
                        "provenance": "deterministic-point-room-fixture/v1",
                        "physical_contact": False}

