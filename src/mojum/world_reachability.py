"""Opt-in, offline route witness for the deterministic point-room fixture.

A sampled grid can certify a valid route when one is found; it cannot certify
unreachability. Never feed the grid or route oracle to a learning policy.
No journal, network, mutable global cache, or actual physics is involved.
"""

from collections import deque
import math

from .world_room import (
    Room, _segment_hits_rect, _valid, advance,
    displacement_within_budget, initial_state,
)

SCHEMA = "mojum.world-route-witness/1"
MAX_GRID_CELLS = 1024
MAX_GRID_AXIS = 32
MAX_OBSTACLES = 128
MAX_ROOM_SPAN_M = 10_000


def _point(value, name):
    if type(value) not in (tuple, list) or len(value) != 2:
        raise ValueError(f"{name} must be a 2D point")
    if any(type(x) not in (int, float) for x in value):
        raise ValueError(f"{name} must contain numbers, not bool/string")
    try:
        result = (float(value[0]), float(value[1]))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name} is not representable") from exc
    if not all(math.isfinite(x) for x in result):
        raise ValueError(f"{name} must be finite")
    return result


def _guard(room, columns, rows):
    if not isinstance(room, Room):
        raise ValueError("expected a point-room Room")
    if (type(columns) is not int or type(rows) is not int
            or not (1 <= columns <= MAX_GRID_AXIS)
            or not (1 <= rows <= MAX_GRID_AXIS)
            or columns * rows > MAX_GRID_CELLS):
        raise ValueError("invalid sampled-grid budget")
    if (room.width > MAX_ROOM_SPAN_M or room.height > MAX_ROOM_SPAN_M
            or len(room.obstacles) > MAX_OBSTACLES):
        raise ValueError("room exceeds opt-in audit budget")
    if room.width / columns == 0.0 or room.height / rows == 0.0:
        raise ValueError("grid resolution underflows float budget")


def _clear(room, first, second):
    """A possible one-tick witness edge, using the fixture's closed AABBs."""
    dx, dy = second[0] - first[0], second[1] - first[1]
    if (not displacement_within_budget(dx, dy)
            or first[0] + dx != second[0] or first[1] + dy != second[1]
            or not _valid(room, *first) or not _valid(room, *second)):
        return False
    return not any(_segment_hits_rect(*first, *second, rect)
                   for rect in room.obstacles)


def _near_cells(point, room, columns, rows):
    """At most nine deterministic nearby grid slots, even at room borders."""
    cell_w, cell_h = room.width / columns, room.height / rows
    center_col = min(columns - 1, int(point[0] / cell_w))
    center_row = min(rows - 1, int(point[1] / cell_h))
    for row in range(max(0, center_row - 1), min(rows, center_row + 2)):
        for col in range(max(0, center_col - 1), min(columns, center_col + 2)):
            yield row, col


def _route_frames(room, nodes, indices):
    """Re-run every successful edge through canonical CAS + swept collision."""
    start = nodes[indices[0]]
    state = initial_state(room, *start)
    frames = [{"tick": 0, "position": list(start), "revision": state.revision}]
    for index in indices[1:]:
        target = nodes[index]
        state, observation = advance(
            room, state, tick=state.tick + 1,
            dx=target[0] - state.x, dy=target[1] - state.y,
            expected_revision=state.revision,
        )
        if observation["blocked"] or (state.x, state.y) != target:
            raise ValueError("sampled route failed canonical point-room replay")
        frames.append({"tick": state.tick, "position": [state.x, state.y],
                       "revision": state.revision})
    return frames


def find_sampled_path(room, start, goal, *, columns=16, rows=16):
    """Find a deterministic, bounded route witness or return INCONCLUSIVE.

    All coordinates become float. Every returned step is verified by the
    actual point-room advance contract. Grid undersampling is never treated
    as proof that the goal cannot be reached.
    """
    _guard(room, columns, rows)
    origin, target = _point(start, "start"), _point(goal, "goal")
    origin_state = initial_state(room, *origin)
    initial_state(room, *target)  # Fail closed if requested goal is inside wall.

    # Two special nodes, followed by walkable row-major grid centers.
    nodes = [origin, target]
    slots = {}
    cell_w, cell_h = room.width / columns, room.height / rows
    for row in range(rows):
        for col in range(columns):
            point = ((col + .5) * cell_w, (row + .5) * cell_h)
            if _valid(room, *point):
                slots[(row, col)] = len(nodes)
                nodes.append(point)

    origin_neighbors = [slots[k] for k in _near_cells(origin, room, columns, rows)
                        if k in slots]
    target_neighbors = frozenset(slots[k]
                                for k in _near_cells(target, room, columns, rows)
                                if k in slots)

    def neighbor_ids(index):
        if index == 0:
            # Direct path first. Else join only local cells, not a remote oracle.
            yield 1
            yield from origin_neighbors
            return
        if index == 1:
            return
        row, col = position_to_slot[index]
        if index in target_neighbors:
            yield 1
        for delta_row, delta_col in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            other = slots.get((row + delta_row, col + delta_col))
            if other is not None:
                yield other

    position_to_slot = {index: slot for slot, index in slots.items()}
    previous = {0: None}
    pending = deque([0])
    while pending:
        current = pending.popleft()
        if current == 1:
            break
        for next_index in neighbor_ids(current):
            if (next_index not in previous
                    and _clear(room, nodes[current], nodes[next_index])):
                previous[next_index] = current
                pending.append(next_index)

    frames = []
    status = "inconclusive"
    if 1 in previous:
        indices = []
        cursor = 1
        while cursor is not None:
            indices.append(cursor)
            cursor = previous[cursor]
        indices.reverse()
        frames = _route_frames(room, nodes, indices)
        status = "witness_found"

    return {
        "schema": SCHEMA,
        "status": status,
        "start_revision": origin_state.revision,
        "goal": list(target),
        "grid": {"columns": columns, "rows": rows},
        "sampled_cells": columns * rows,
        "walkable_centers": len(slots),
        "visited_vertices": len(previous),
        "path": frames,
    }


def verify_witness(room, report):
    """Fail closed unless a complete report replays against the SAME room.

    Replay validates geometry/CAS/ticks; it does not authenticate authorship.
    """
    expected = {"schema", "status", "start_revision", "goal", "grid",
                "sampled_cells", "walkable_centers", "visited_vertices", "path"}
    if type(report) is not dict or set(report) != expected:
        raise ValueError("invalid route witness shape")
    if (type(report["schema"]) is not str or report["schema"] != SCHEMA
            or type(report["status"]) is not str
            or report["status"] != "witness_found"):
        raise ValueError("no route witness to verify")
    grid = report["grid"]
    if type(grid) is not dict or set(grid) != {"columns", "rows"}:
        raise ValueError("invalid grid metadata")
    columns, rows = grid["columns"], grid["rows"]
    _guard(room, columns, rows)
    cells = columns * rows
    if (type(report["sampled_cells"]) is not int
            or report["sampled_cells"] != cells
            or type(report["walkable_centers"]) is not int
            or not (0 <= report["walkable_centers"] <= cells)
            or type(report["visited_vertices"]) is not int
            or not (1 <= report["visited_vertices"] <= cells + 2)):
        raise ValueError("invalid search counts")
    goal = _point(report["goal"], "goal")
    path = report["path"]
    if type(path) is not list or not (1 <= len(path) <= cells + 2):
        raise ValueError("invalid route witness length")
    state = None
    for step, frame in enumerate(path):
        if (type(frame) is not dict
                or set(frame) != {"tick", "position", "revision"}
                or type(frame["tick"]) is not int or frame["tick"] != step
                or type(frame["revision"]) is not str
                or len(frame["revision"]) != 64
                or any(ch not in "0123456789abcdef" for ch in frame["revision"])):
            raise ValueError("invalid route frame")
        point = _point(frame["position"], "position")
        if step == 0:
            state = initial_state(room, *point)
            start_revision = report["start_revision"]
            if (type(start_revision) is not str or len(start_revision) != 64
                    or any(ch not in "0123456789abcdef" for ch in start_revision)
                    or start_revision != state.revision):
                raise ValueError("mismatched start revision")
        else:
            state, observation = advance(
                room, state, tick=step,
                dx=point[0] - state.x, dy=point[1] - state.y,
                expected_revision=state.revision,
            )
            if observation["blocked"] or (state.x, state.y) != point:
                raise ValueError("route step does not reach its waypoint")
        if state.revision != frame["revision"]:
            raise ValueError("route revision mismatch")
    if (state.x, state.y) != goal:
        raise ValueError("route does not reach the requested goal")
    return True
