"""Independent exact-rational collision oracle for PUBLIC_SYNTHETIC point-room QA.

The production fixture uses Liang-Barsky floating-point clipping.  This oracle
uses rational, orientation-based closed segment/edge intersections instead.
The seeded inputs are deliberately *integer-valued*, so no rounding tolerance
is needed and exact expected answers are unambiguous.
"""

from fractions import Fraction
import hashlib
import json
import random
import unittest

from mojum.world_room import Room, advance, initial_state

SEED = 0xC01115A
SCENES = 320
MOVES_PER_SCENE = 5


def _point_inside(point, rect):
    x, y = point
    left, bottom, width, height = rect
    return left <= x <= left + width and bottom <= y <= bottom + height


def _orientation(a, b, c):
    """Exact signed cross product; no slope, division or tolerance."""
    ax, ay = map(Fraction, a)
    bx, by = map(Fraction, b)
    cx, cy = map(Fraction, c)
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_closed_segment(a, b, p):
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _edges_intersect(a, b, c, d):
    """Exact closed segment intersection, including grazing and collinearity."""
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 == 0 and _on_closed_segment(a, b, c):
        return True
    if o2 == 0 and _on_closed_segment(a, b, d):
        return True
    if o3 == 0 and _on_closed_segment(c, d, a):
        return True
    if o4 == 0 and _on_closed_segment(c, d, b):
        return True
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def _segment_meets_closed_rectangle(a, b, rect):
    if _point_inside(a, rect) or _point_inside(b, rect):
        return True
    x, y, width, height = rect
    corners = ((x, y), (x + width, y),
               (x + width, y + height), (x, y + height))
    return any(_edges_intersect(a, b, corners[i], corners[(i + 1) % 4])
               for i in range(4))


def _oracle_blocked(room, position, movement):
    x, y = position
    dx, dy = movement
    destination = (x + dx, y + dy)
    if not (0 <= destination[0] <= room.width and
            0 <= destination[1] <= room.height):
        return True
    return any(_segment_meets_closed_rectangle(position, destination, rect)
               for rect in room.obstacles)


def _scene_corpus():
    """Bounded, reproducible public fixtures with no external data source."""
    rng = random.Random(SEED)
    for scene_id in range(SCENES):
        width = rng.randint(24, 80)
        height = rng.randint(24, 80)
        rects = []
        for _ in range(rng.randint(0, 4)):
            x = rng.randint(1, width - 3)
            y = rng.randint(1, height - 3)
            w = rng.randint(1, min(8, width - x))
            h = rng.randint(1, min(8, height - y))
            rects.append((x, y, w, h))
        room = Room(width, height, tuple(rects))
        # Closed obstacle boundaries are invalid start points. Search a
        # bounded deterministic raster instead of rejection sampling forever.
        eligible = [(x, y) for x in range(width + 1)
                    for y in range(height + 1)
                    if not any(_point_inside((x, y), r) for r in rects)]
        start = eligible[rng.randrange(len(eligible))]
        movements = tuple((rng.randint(-20, 20), rng.randint(-20, 20))
                          for _ in range(MOVES_PER_SCENE))
        yield scene_id, room, start, movements


def _trace(room, start, movements):
    current = initial_state(room, *start)
    records = []
    for dx, dy in movements:
        expected_blocked = _oracle_blocked(room, (current.x, current.y), (dx, dy))
        previous = current
        current, event = advance(room, current, tick=current.tick + 1,
                                 dx=dx, dy=dy, expected_revision=current.revision)
        expected_position = ((previous.x, previous.y) if expected_blocked
                             else (previous.x + dx, previous.y + dy))
        if (event["blocked"] != expected_blocked or
                (current.x, current.y) != expected_position or
                event["position"] != list(expected_position)):
            raise AssertionError(
                f"oracle disagreement tick={current.tick}, start={start}, "
                f"room={room}, move={(dx, dy)}, expected={expected_blocked}, "
                f"event={event}"
            )
        if current.tick != previous.tick + 1:
            raise AssertionError("nonmonotonic tick")
        if current.revision == previous.revision:
            raise AssertionError("revision did not advance with tick")
        if event["tick"] != current.tick or event["revision"] != current.revision:
            raise AssertionError("event/state revision mismatch")
        if event["physical_contact"] is not False:
            raise AssertionError("synthetic room misidentified physical contact")
        records.append((event["blocked"], tuple(event["position"]),
                        current.tick, current.revision))
    return tuple(records)


class ExactRationalRoomOracleTests(unittest.TestCase):
    def test_seeded_cross_product_oracle_and_deterministic_replay(self):
        # No jitter, secrets, wall clock, network, SQLite, or mandatory packages.
        count = 0
        digest = hashlib.sha256()
        for scene_id, room, start, moves in _scene_corpus():
            with self.subTest(scene=scene_id):
                first = _trace(room, start, moves)
                second = _trace(room, start, moves)
                self.assertEqual(first, second)
                digest.update(json.dumps(first, separators=(",", ":")).encode())
                count += len(first)
        self.assertEqual(count, SCENES * MOVES_PER_SCENE)
        self.assertEqual(digest.hexdigest(),
                         "564f405b4d4a8db6adcdcc05a05180aef69d75eea3cfd158d1213d859c5f2258")

    def test_topological_corner_graze_tangent_thin_wall_and_boundary(self):
        cases = (
            # Start, displacement, expected. Rectangle is closed [4,5]².
            ((0, 0), (0, 0), False),
            ((1, 1), (3, 3), True),       # exact corner touch
            ((0, 4), (8, 0), True),      # bottom edge is blocking
            ((0, 3), (8, 0), False),     # parallel, strictly below
            ((3, 5), (3, 0), True),      # full crossing, 1-wide obstacle
            ((0, 0), (6, 4), False),     # near miss below rectangle
            ((0, 0), (-2, 0), True),     # outer room boundary
            ((1, 7), (3, -3), True),     # descending diagonal to corner
            ((1, 1), (2, 2), False),     # near corner, no touch
            ((1, 1), (9, 0), False),     # reachable outer wall
        )
        room = Room(10, 10, ((4, 4, 1, 1),))
        for start, movement, expected in cases:
            with self.subTest(start=start, movement=movement):
                self.assertEqual(_oracle_blocked(room, start, movement), expected)
                state = initial_state(room, *start)
                current, event = advance(room, state, tick=1,
                                         dx=movement[0], dy=movement[1],
                                         expected_revision=state.revision)
                self.assertEqual(event["blocked"], expected)
                if expected:
                    self.assertEqual((current.x, current.y), start)

    def test_obstacle_permutation_does_not_change_collision_decision(self):
        walls = ((6, 4, 1, 8), (3, 12, 8, 1), (18, 10, 1, 2))
        a = Room(30, 30, walls)
        b = Room(30, 30, tuple(reversed(walls)))
        for direction in ((30, 25), (8, 0), (3, 12), (0, 0), (0, 30)):
            with self.subTest(direction=direction):
                expected = _oracle_blocked(a, (1, 1), direction)
                self.assertEqual(expected, _oracle_blocked(b, (1, 1), direction))
                for room in a, b:
                    state = initial_state(room, 1, 1)
                    _, event = advance(room, state, tick=1, dx=direction[0],
                                       dy=direction[1], expected_revision=state.revision)
                    self.assertEqual(event["blocked"], expected)

    def test_stale_revision_and_nonmonotonic_ticks_are_rejected(self):
        room = Room(12, 12, ((5, 5, 2, 2),))
        state = initial_state(room, 1, 1)
        for tick, revision in ((0, state.revision), (2, state.revision),
                               (True, state.revision), (1, "0" * 64)):
            with self.subTest(tick=tick, revision=revision):
                with self.assertRaises(ValueError):
                    advance(room, state, tick=tick, dx=1, dy=0,
                            expected_revision=revision)


if __name__ == "__main__":
    unittest.main()
