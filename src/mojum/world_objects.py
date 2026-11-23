"""Opt-in, deterministic point-object interactions for the synthetic room fixture.

No real contact, authoritative physics, input devices, or persistent journal.
"""

from dataclasses import dataclass
import hashlib
import json
import math
import re

from mojum.world_room import Room, RoomState, _revision, _valid, _segment_hits_rect, advance

SCHEMA = "mojum.world.objects/1"
EVENT_SCHEMA = "mojum.world.object-event/1"
MAX_OBJECTS = 64
MAX_TICK = (1 << 53) - 1  # lossless integer in common JSON consumers
REACH_M = 0.35
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z", re.ASCII)
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


@dataclass(frozen=True)
class ObjectState:
    """Canonical positions; a carried item has (id, None, None)."""

    tick: int
    room_revision: str
    items: tuple[tuple[str, int | float | None, int | float | None], ...]
    carried: str | None
    revision: str


def _number(value):
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _valid_room_state(room, state):
    if (type(room) is not Room or type(state) is not RoomState
            or type(state.tick) is not int or not 0 <= state.tick <= MAX_TICK
            or not _valid(room, state.x, state.y)
            or type(state.revision) is not str or _HEX.fullmatch(state.revision) is None
            or state.revision != _revision(room, state.tick, state.x, state.y)):
        raise ValueError("invalid or forged point-room state")


def _digest(tick, room_revision, items, carried):
    data = [SCHEMA, tick, room_revision, items, carried]
    return hashlib.sha256(json.dumps(data, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()


def _valid_items(room, items, carried):
    if type(items) is not tuple or not 0 <= len(items) <= MAX_OBJECTS:
        raise ValueError("invalid item collection")
    if carried is not None and (type(carried) is not str or _ID.fullmatch(carried) is None):
        raise ValueError("invalid carried item")
    previous = ""
    unplaced = []
    for entry in items:
        if type(entry) is not tuple or len(entry) != 3:
            raise ValueError("invalid item entry")
        ident, x, y = entry
        if type(ident) is not str or _ID.fullmatch(ident) is None or ident <= previous:
            raise ValueError("unsorted, duplicate or invalid item id")
        previous = ident
        if x is None or y is None:
            if x is not None or y is not None:
                raise ValueError("incomplete carried coordinates")
            unplaced.append(ident)
        elif not (_number(x) and _number(y) and _valid(room, x, y)):
            raise ValueError("item outside free synthetic room")
    if unplaced != ([] if carried is None else [carried]):
        raise ValueError("carried item and unplaced positions disagree")


def _check_objects(room, state):
    if type(state) is not ObjectState:
        raise ValueError("invalid object state type")
    if type(state.tick) is not int or not 0 <= state.tick <= MAX_TICK:
        raise ValueError("invalid object tick")
    if type(state.room_revision) is not str or _HEX.fullmatch(state.room_revision) is None:
        raise ValueError("invalid room revision")
    _valid_items(room, state.items, state.carried)
    if (type(state.revision) is not str or _HEX.fullmatch(state.revision) is None
            or state.revision != _digest(state.tick, state.room_revision, state.items, state.carried)):
        raise ValueError("forged object revision")


def initial_objects(room: Room, room_state: RoomState, fixtures=()) -> ObjectState:
    """Birth of standalone object fixtures, strictly at logical room tick zero."""
    _valid_room_state(room, room_state)
    if room_state.tick != 0:
        raise ValueError("object fixture can only start at room birth")
    if type(fixtures) not in (list, tuple) or len(fixtures) > MAX_OBJECTS:
        raise ValueError("invalid fixture collection")
    items = []
    for f in fixtures:
        if type(f) not in (list, tuple) or len(f) != 3:
            raise ValueError("invalid fixture entry")
        items.append(tuple(f))
    items = tuple(sorted(items, key=lambda item: item[0] if type(item[0]) is str else ""))
    _valid_items(room, items, None)
    return ObjectState(0, room_state.revision, items, None,
                       _digest(0, room_state.revision, items, None))


def _near_and_visible(room, ax, ay, bx, by):
    if not _valid(room, bx, by):
        return False
    if math.hypot(ax - bx, ay - by) > REACH_M:
        return False
    return not any(_segment_hits_rect(ax, ay, bx, by, rect)
                   for rect in room.obstacles)


def advance_objects(room: Room, room_state: RoomState, state: ObjectState, *,
                    tick: int, dx: float, dy: float,
                    expected_room_revision: str, expected_objects_revision: str,
                    action: str = "wait", object_id: str | None = None,
                    place: tuple[float, float] | None = None):
    """One canonical room move AND at most one explicit object interaction.

    Raises ValueError for invalid transitions, without modifying inputs.
    Returns (next_room_state, next_object_state, public_synthetic_event).
    """
    _valid_room_state(room, room_state)
    _check_objects(room, state)
    if (state.tick != room_state.tick or state.room_revision != room_state.revision
            or type(expected_objects_revision) is not str
            or state.revision != expected_objects_revision
            or type(expected_room_revision) is not str
            or room_state.revision != expected_room_revision):
        raise ValueError("stale or mismatched room/object CAS")
    if (type(tick) is not int or tick > MAX_TICK or tick != state.tick + 1):
        raise ValueError("invalid joint logical tick")
    if type(action) is not str or action not in ("wait", "pick", "place"):
        raise ValueError("invalid interaction action")
    if action == "wait":
        if object_id is not None or place is not None:
            raise ValueError("wait cannot target an item")
    elif type(object_id) is not str or _ID.fullmatch(object_id) is None:
        raise ValueError("invalid target item")
    if action != "place" and place is not None:
        raise ValueError("unexpected placement coordinates")

    # Canonical advance is authoritative: caller cannot inject a fabricated
    # next-room position or skip swept-obstacle tests.
    next_room, room_event = advance(room, room_state, tick=tick, dx=dx, dy=dy,
                                    expected_revision=expected_room_revision)
    items = list(state.items)
    carried = state.carried
    if action == "pick":
        if carried is not None:
            raise ValueError("carry slot occupied")
        match = next((i for i, entry in enumerate(items) if entry[0] == object_id), None)
        if match is None or items[match][1] is None:
            raise ValueError("item unavailable")
        _, x, y = items[match]
        if not _near_and_visible(room, next_room.x, next_room.y, x, y):
            raise ValueError("item out of reach or occluded")
        items[match] = (object_id, None, None)
        carried = object_id
    elif action == "place":
        if carried is None or carried != object_id:
            raise ValueError("target is not carried")
        if (type(place) not in (tuple, list) or len(place) != 2
                or not all(_number(v) for v in place)):
            raise ValueError("invalid placement coordinates")
        x, y = place
        if not _near_and_visible(room, next_room.x, next_room.y, x, y):
            raise ValueError("placement outside free reachable area")
        i = next(i for i, entry in enumerate(items) if entry[0] == object_id)
        items[i] = (object_id, x, y)
        carried = None
    frozen = tuple(items)
    next_objects = ObjectState(tick, next_room.revision, frozen, carried,
                               _digest(tick, next_room.revision, frozen, carried))
    return next_room, next_objects, {
        "schema": EVENT_SCHEMA,
        "tick": tick,
        "action": action,
        "object_id": object_id,
        "actor_position": [next_room.x, next_room.y],
        "room_revision": next_room.revision,
        "objects_revision": next_objects.revision,
        "carried": carried,
        "move_blocked": room_event["blocked"],
        "provenance": "deterministic-point-room-objects/v1",
        "physical_contact": False,
    }


def snapshot_objects(room: Room, state: ObjectState) -> dict:
    """Make a detached JSON-safe snapshot; only validated states can be exported."""
    if type(room) is not Room:
        raise ValueError("invalid room")
    _check_objects(room, state)
    return {"schema": SCHEMA, "tick": state.tick,
            "room_revision": state.room_revision,
            "items": [list(entry) for entry in state.items],
            "carried": state.carried, "revision": state.revision}


def restore_objects(room: Room, room_state: RoomState, document: dict) -> ObjectState:
    """Fail-closed restore from a parsed JSON snapshot at the matching room tick."""
    _valid_room_state(room, room_state)
    if (type(document) is not dict
            or any(type(k) is not str for k in document)
            or set(document) != {"schema", "tick", "room_revision", "items", "carried", "revision"}
            or type(document["schema"]) is not str or document["schema"] != SCHEMA
            or type(document["items"]) is not list
            or len(document["items"]) > MAX_OBJECTS):
        raise ValueError("invalid object snapshot shape")
    if any(type(entry) is not list or len(entry) != 3 for entry in document["items"]):
        raise ValueError("invalid snapshot item entry")
    state = ObjectState(document["tick"], document["room_revision"],
                        tuple(tuple(entry) for entry in document["items"]),
                        document["carried"], document["revision"])
    _check_objects(room, state)
    if state.tick != room_state.tick or state.room_revision != room_state.revision:
        raise ValueError("snapshot from a different room epoch")
    return state
