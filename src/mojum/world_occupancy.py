"""Bounded, deterministic read-only occupancy preview for the synthetic point-room.

This is offline authoring/QA geometry, never an organism observation, planner,
physics solver, or alternative source of canonical world state.
"""

from bisect import bisect_left, bisect_right
import hashlib
import json

from .world_room import Room

SCHEMA = "mojum.world.point-room-occupancy/1"
MAX_AXIS_CELLS = 128
MAX_TOTAL_CELLS = 16_384


def _edges(length, count):
    # Avoid `length * i` overflow for large finite rooms. Endpoint is exact.
    edges = [length * (i / count) for i in range(count)] + [length]
    if any(not a < b for a, b in zip(edges, edges[1:])):
        raise ValueError("grid resolution collapses room coordinates")
    return edges


def _intersecting_cells(edges, start, end):
    """Inclusive geometry: touching a cell boundary counts as occupied."""
    first = max(0, bisect_left(edges, start) - 1)
    last = min(len(edges) - 2, bisect_right(edges, end) - 1)
    return first, last


def rasterize(room: Room, *, columns: int = 32, rows: int = 32) -> dict:
    """Render closed obstacles into a bounded, bottom-up occupancy grid.

    A cell is `#` if its closed rectangle intersects a closed obstacle,
    including edge/corner touches; otherwise it is `.`. Boundary of the
    outer room is legal inside, consistent with world_room._valid().

    Raster is a read-only conservative visualization: a free *cell* does
    not grant permission for movement and cannot replace swept collision.
    """
    if not isinstance(room, Room):
        raise ValueError("expected point-room geometry")
    if (type(columns) is not int or type(rows) is not int
            or not 1 <= columns <= MAX_AXIS_CELLS
            or not 1 <= rows <= MAX_AXIS_CELLS
            or columns * rows > MAX_TOTAL_CELLS):
        raise ValueError("invalid or excessive occupancy grid size")

    x_edges = _edges(room.width, columns)
    y_edges = _edges(room.height, rows)
    # 2D difference grid: 4 updates per obstacle, then one O(cells) scan.
    # This avoids 4096 full-room obstacles causing ~67 million cell writes.
    delta = [[0] * (columns + 1) for _ in range(rows + 1)]
    for x, y, width, height in room.obstacles:
        first_x, last_x = _intersecting_cells(x_edges, x, x + width)
        first_y, last_y = _intersecting_cells(y_edges, y, y + height)
        delta[first_y][first_x] += 1
        delta[first_y][last_x + 1] -= 1
        delta[last_y + 1][first_x] -= 1
        delta[last_y + 1][last_x + 1] += 1

    blocked_rows = []
    for j in range(rows):
        row_sum = 0
        cells = []
        for i in range(columns):
            row_sum += delta[j][i]
            delta[j][i] = row_sum + (delta[j - 1][i] if j else 0)
            cells.append("#" if delta[j][i] > 0 else ".")
        blocked_rows.append("".join(cells))

    geometry_json = json.dumps(
        [room.width, room.height, room.obstacles],
        separators=(",", ":"), allow_nan=False,
    )
    return {
        "schema": SCHEMA,
        "geometry_sha256": hashlib.sha256(geometry_json.encode("utf-8")).hexdigest(),
        "columns": columns,
        "rows": rows,
        "row_order": "bottom_to_top",
        "blocked_rows": blocked_rows,
        "provenance": "offline-point-room-authoring-qa-only",
    }


def verify_raster(room: Room, frame: dict) -> bool:
    """Fail closed if a saved QA preview differs from current geometry."""
    if (type(frame) is not dict or set(frame) != {
            "schema", "geometry_sha256", "columns", "rows", "row_order",
            "blocked_rows", "provenance"}):
        raise ValueError("invalid occupancy preview envelope")
    # Equality on untrusted objects can invoke arbitrary __eq__ methods, or
    # accept malformed fields through forged always-true comparisons. Validate
    # exact wire primitive types before comparing against the expected frame.
    if (any(type(frame[key]) is not str for key in (
            "schema", "geometry_sha256", "row_order", "provenance"))
            or type(frame["columns"]) is not int
            or type(frame["rows"]) is not int
            or type(frame["blocked_rows"]) is not list):
        raise ValueError("invalid occupancy preview field types")
    columns, rows = frame["columns"], frame["rows"]
    if (not 1 <= columns <= MAX_AXIS_CELLS
            or not 1 <= rows <= MAX_AXIS_CELLS
            or columns * rows > MAX_TOTAL_CELLS
            or len(frame["blocked_rows"]) != rows
            or len(frame["geometry_sha256"]) != 64):
        raise ValueError("invalid occupancy preview dimensions or digest")
    if any(type(line) is not str or len(line) != columns
           or not set(line).issubset({".", "#"})
           for line in frame["blocked_rows"]):
        raise ValueError("invalid occupancy rows")
    expected = rasterize(room, columns=columns, rows=rows)
    if frame != expected:
        raise ValueError("stale or tampered occupancy preview")
    return True
