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


def _admit_fields(value, required, name):
    """Copy an exact dict only after validating keys without hashing caller keys."""
    if type(value) is not dict or len(value) != len(required):
        raise ValueError(f"{name}: expected exact fields")
    clean = {}
    for key, item in dict.items(value):
        if type(key) is not str or not key or len(key) > 32:
            raise ValueError(f"{name}: invalid field key")
        clean[key] = item
    if len(clean) != len(required) or set(clean) != set(required):
        raise ValueError(f"{name}: expected exact fields")
    return clean


def _rect(value, name):
    fields = _admit_fields(value, ("x0", "y0", "x1", "y1"), name)
    coords = tuple(fields[k] for k in ("x0", "y0", "x1", "y1"))
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
    fields = _admit_fields(
        payload, ("schema", "provenance", "room", "cell_mm", "obstacles"), "payload"
    )
    if type(fields["schema"]) is not str or fields["schema"] != SCHEMA:
        raise ValueError("unsupported schema")
    if type(fields["provenance"]) is not str or fields["provenance"] != "PUBLIC_SYNTHETIC":
        raise ValueError("PUBLIC_SYNTHETIC provenance required")
    room = _rect(fields["room"], "room")
    cell = fields["cell_mm"]
    if type(cell) is not int or not 1 <= cell <= 100_000:
        raise ValueError("cell_mm must be an integer in [1, 100000]")
    obstacles = fields["obstacles"]
    if type(obstacles) is not list or len(obstacles) > MAX_OBSTACLES:
        raise ValueError("invalid obstacle list")
    parsed = tuple(_rect(obstacle, "obstacle") for obstacle in obstacles)
    x0, y0, x1, y1 = room
    width = (x1 - x0 + cell - 1) // cell
    height = (y1 - y0 + cell - 1) // cell
    if width * height > MAX_CELLS:
        raise ValueError("occupancy grid exceeds cell budget")

    # 2D difference grid: each rectangle contributes four corner updates,
    # then one O(width*height) prefix pass paints the final occupancy. This
    # avoids scanning every obstacle for every cell (up to 67M comparisons).
    diff = [[0] * (width + 1) for _ in range(height + 1)]
    for ox0, oy0, ox1, oy1 in parsed:
        ix0 = max(0, (ox0 - x0) // cell)
        iy0 = max(0, (oy0 - y0) // cell)
        ix1 = min(width, (ox1 - x0 + cell - 1) // cell)
        iy1 = min(height, (oy1 - y0 + cell - 1) // cell)
        if ix0 >= ix1 or iy0 >= iy1:
            continue
        diff[iy0][ix0] += 1
        diff[iy0][ix1] -= 1
        diff[iy1][ix0] -= 1
        diff[iy1][ix1] += 1

    rows = []
    for iy in range(height):
        row = []
        for ix in range(width):
            value = diff[iy][ix]
            if iy:
                value += diff[iy - 1][ix]
            if ix:
                value += diff[iy][ix - 1]
            if iy and ix:
                value -= diff[iy - 1][ix - 1]
            diff[iy][ix] = value
            row.append(value > 0)
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
