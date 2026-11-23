"""Bounded, deterministic spawn candidates for the synthetic point-room.

This is an offline helper, not an actor spawner or live-world authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from mojum.world_room import Room

MAX_GRID_SIDE = 128
MAX_GRID_CELLS = 16_384
MAX_GEOMETRY_CHECKS = 250_000
MAX_RESULTS = 1_024


@dataclass(frozen=True)
class SpawnCandidate:
    """A valid, float-valued point and its zero-based grid cell."""

    x: float
    y: float
    column: int
    row: int


def _finite_nonnegative(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except (OverflowError, TypeError, ValueError):
        return False


def candidates(
    room: Room,
    *,
    columns: int = 9,
    rows: int = 9,
    clearance: float = 0.0,
    limit: int = 32,
) -> tuple[SpawnCandidate, ...]:
    """Return center-first free grid-cell centers in deterministic order.

    Clearance is an L-infinity (axis-aligned) exclusion margin in metres:
    points on an expanded obstacle boundary are rejected. Room walls allow
    positions exactly at the requested clearance. The grid is not a proof that
    no valid spawn exists; callers can increase resolution within the limits.
    No journal, RNG, network, wall clock or mutable global state is involved.
    """
    if type(room) is not Room:
        raise ValueError("room must be a Room")
    if (type(columns) is not int or not 1 <= columns <= MAX_GRID_SIDE
            or type(rows) is not int or not 1 <= rows <= MAX_GRID_SIDE):
        raise ValueError("grid dimensions outside supported range")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError("limit outside supported range")
    if not _finite_nonnegative(clearance):
        raise ValueError("clearance must be a finite nonnegative number")

    # A frozen dataclass is not proof that object.__setattr__ has not modified
    # it since construction. Work only from a detached, revalidated snapshot.
    room = Room(room.width, room.height, room.obstacles)
    checks = columns * rows * max(1, len(room.obstacles))
    if columns * rows > MAX_GRID_CELLS or checks > MAX_GEOMETRY_CHECKS:
        raise ValueError("spawn grid exceeds bounded geometry budget")

    # Stable integer ordering: center-out by grid distance, then row/column.
    # No float ordering keys, random seeds or nondeterministic sets.
    cells = ((column, row) for row in range(rows) for column in range(columns))
    ordered = sorted(cells, key=lambda cell: (
        (2 * cell[0] + 1 - columns) ** 2 + (2 * cell[1] + 1 - rows) ** 2,
        cell[1], cell[0],
    ))
    selected: list[SpawnCandidate] = []
    seen: set[tuple[float, float]] = set()
    for column, row in ordered:
        # Multiplication by a fraction bounded by one avoids overflow in
        # ordinary finite room dimensions; avoid width*(2*column+1).
        x = float(room.width) * ((column + 0.5) / columns)
        y = float(room.height) * ((row + 0.5) / rows)
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if (x < clearance or x > room.width - clearance
                or y < clearance or y > room.height - clearance):
            continue
        if any(
            ox - clearance <= x <= ox + ow + clearance
            and oy - clearance <= y <= oy + oh + clearance
            for ox, oy, ow, oh in room.obstacles
        ):
            continue
        point = (x, y)
        if point in seen:
            continue
        seen.add(point)
        selected.append(SpawnCandidate(x, y, column, row))
        if len(selected) == limit:
            break
    return tuple(selected)
