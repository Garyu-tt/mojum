"""Bounded, deterministic, offline wall clearance for synthetic 2D rooms.

All coordinates are integer millimetres. No I/O, state or runtime integration.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = "mojum.room-wall-clearance/1"
MAX_POINTS = 4096
MAX_COORD = 1_000_000_000


def _integer(value: object, name: str) -> int:
    if type(value) is not int or not -MAX_COORD <= value <= MAX_COORD:
        raise ValueError(f"{name} must be a bounded native integer")
    return value


def audit_wall_clearance(room: object, points: object) -> dict:
    """Return detached wall distances for unique synthetic point IDs.

    room: (min_x, min_y, max_x, max_y), integer mm.
    points: list of (identifier, x, y). Points on walls are valid.
    """
    if type(room) is not tuple or len(room) != 4:
        raise ValueError("room must be a four-element native tuple")
    x0, y0, x1, y1 = (_integer(v, "room coordinate") for v in room)
    if x0 >= x1 or y0 >= y1:
        raise ValueError("room must have positive width and height")
    if type(points) is not list or len(points) > MAX_POINTS:
        raise ValueError("points must be a bounded native list")
    seen = set()
    result = []
    for item in points:
        if type(item) is not tuple or len(item) != 3:
            raise ValueError("point must be a three-element native tuple")
        identifier, raw_x, raw_y = item
        if (type(identifier) is not str or not 1 <= len(identifier) <= 64
                or not identifier.isascii()
                or not all(c.isalnum() or c in "_-" for c in identifier)):
            raise ValueError("invalid point identifier")
        if identifier in seen:
            raise ValueError("duplicate point identifier")
        seen.add(identifier)
        x, y = _integer(raw_x, "point x"), _integer(raw_y, "point y")
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            raise ValueError("point outside room")
        walls = {"left": x - x0, "right": x1 - x,
                 "bottom": y - y0, "top": y1 - y}
        result.append({"id": identifier, "walls_mm": walls,
                       "nearest_mm": min(walls.values())})
    result.sort(key=lambda entry: entry["id"])
    payload = {"schema": SCHEMA, "points": result,
               "room_mm": [x0, y0, x1, y1]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=True
                                       ).encode("ascii")).hexdigest()
    return {**payload, "sha256": digest}
