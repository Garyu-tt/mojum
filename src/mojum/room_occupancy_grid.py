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


def _rect(value, name):
    if type(value) is not dict or set(value) != {"x0", "y0", "x1", "y1"}:
        raise ValueError(f"{name}: expected exact rectangle fields")
    coords = tuple(value[k] for k in ("x0", "y0", "x1", "y1"))
    if any(type(v) is not int or abs(v) > 1_000_000 for v in coords):
        raise ValueError(f"{name}: coordinates must be bounded integers")
    x0, y0, x1, y1 = coords
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"{name}: empty or reversed rectangle")
    return coords


def rasterize_occupancy(payload):
    """Return immutable occupancy rows and a stable SHA256 consistency digest.

    Input must be an exact dict with schema, provenance, room, cell_mm and
    obstacles. The final edge cells may be smaller than cell_mm.
    """
    if type(payload) is not dict or set(payload) != {
        "schema", "provenance", "room", "cell_mm", "obstacles"
    }:
        raise ValueError("invalid occupancy payload fields")
    if payload["schema"] != SCHEMA or type(payload["schema"]) is not str:
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
    rows = []
    for iy in range(height):
        cy0, cy1 = y0 + iy * cell, min(y1, y0 + (iy + 1) * cell)
        row = []
        for ix in range(width):
            cx0, cx1 = x0 + ix * cell, min(x1, x0 + (ix + 1) * cell)
            row.append(any(
                ox0 < cx1 and ox1 > cx0 and oy0 < cy1 and oy1 > cy0
                for ox0, oy0, ox1, oy1 in parsed
            ))
        rows.append(tuple(row))
    cells = tuple(rows)
    normalized = {"schema": SCHEMA, "room": room, "cell_mm": cell,
                  "width": width, "height": height, "rows": cells}
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return MappingProxyType({
        "schema": SCHEMA, "width": width, "height": height,
        "rows": cells, "occupied": sum(sum(row) for row in cells),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    })
