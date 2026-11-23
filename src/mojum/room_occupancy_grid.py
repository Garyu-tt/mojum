"""Deterministic, offline occupancy rasterization for synthetic rectangular rooms.

Coordinates are integer millimetres. Cells are occupied when their open interior
intersects an obstacle's open interior. Touching edges alone does not occupy.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

SCHEMA = "mojum.room.occupancy/1"
MAX_CELLS = 65536
MAX_OBSTACLES = 1024
MAX_CELL_MARKS = 1_000_000
_RECT_FIELDS = frozenset({"x0", "y0", "x1", "y1"})
_PAYLOAD_FIELDS = frozenset({"schema", "provenance", "room", "cell_mm", "obstacles"})


def _native_keys(value, expected, name):
    if type(value) is not dict or len(value) != len(expected):
        raise ValueError(f"{name}: expected exact native fields")
    keys = tuple(value)
    if any(type(key) is not str for key in keys) or set(keys) != expected:
        raise ValueError(f"{name}: expected exact native fields")


def _rect(value, name):
    _native_keys(value, _RECT_FIELDS, name)
    coords = tuple(value[k] for k in ("x0", "y0", "x1", "y1"))
    if any(type(v) is not int or abs(v) > 1_000_000 for v in coords):
        raise ValueError(f"{name}: coordinates must be bounded integers")
    x0, y0, x1, y1 = coords
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"{name}: empty or reversed rectangle")
    return coords


def rasterize_occupancy(payload):
    """Return immutable occupancy rows and a stable SHA256 consistency digest.

    Input must be an exact native dict with schema, provenance, room, cell_mm
    and obstacles. Cell marking is capped at one million obstacle-cell marks.
    The final edge cells may be smaller than cell_mm.
    """
    _native_keys(payload, _PAYLOAD_FIELDS, "payload")
    if type(payload["schema"]) is not str or payload["schema"] != SCHEMA:
        raise ValueError("unsupported schema")
    if type(payload["provenance"]) is not str or payload["provenance"] != "PUBLIC_SYNTHETIC":
        raise ValueError("PUBLIC_SYNTHETIC provenance required")
    room = _rect(payload["room"], "room")
    cell = payload["cell_mm"]
    if type(cell) is not int or not 1 <= cell <= 100_000:
        raise ValueError("cell_mm must be an integer in [1, 100000]")
    obstacles = payload["obstacles"]
    if type(obstacles) is not list or len(obstacles) > MAX_OBSTACLES:
        raise ValueError("invalid obstacle list")
    parsed = tuple(_rect(obstacle, "obstacle") for obstacle in obstacles)
    x0, y0, x1, y1 = room
    width = (x1 - x0 + cell - 1) // cell
    height = (y1 - y0 + cell - 1) // cell
    if width * height > MAX_CELLS:
        raise ValueError("occupancy grid exceeds cell budget")

    # Mark cells touched by each obstacle instead of testing every obstacle
    # against every cell. Bound total writes before entering the marking loops.
    spans = []
    total_marks = 0
    for ox0, oy0, ox1, oy1 in parsed:
        if ox1 <= x0 or ox0 >= x1 or oy1 <= y0 or oy0 >= y1:
            continue
        ix0 = max(0, (ox0 - x0) // cell)
        iy0 = max(0, (oy0 - y0) // cell)
        ix1 = min(width, (ox1 - x0 + cell - 1) // cell)
        iy1 = min(height, (oy1 - y0 + cell - 1) // cell)
        marks = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        total_marks += marks
        if total_marks > MAX_CELL_MARKS:
            raise ValueError("obstacle-cell marking budget exceeded")
        spans.append((ix0, iy0, ix1, iy1))

    rows = [[False] * width for _ in range(height)]
    for ix0, iy0, ix1, iy1 in spans:
        for iy in range(iy0, iy1):
            row = rows[iy]
            for ix in range(ix0, ix1):
                row[ix] = True
    cells = tuple(tuple(row) for row in rows)
    normalized = {"schema": SCHEMA, "room": room, "cell_mm": cell,
                  "width": width, "height": height, "rows": cells}
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return MappingProxyType({
        "schema": SCHEMA, "width": width, "height": height,
        "rows": cells, "occupied": sum(sum(row) for row in cells),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    })
