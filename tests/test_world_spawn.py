"""CPU-only tests for the bounded synthetic point-room spawn grid."""

import math
import unittest

from mojum.world_room import Room, initial_state
from mojum.world_spawn import (
    MAX_GEOMETRY_CHECKS,
    SpawnCandidate,
    candidates,
)


class SpawnGridTests(unittest.TestCase):
    def test_center_first_and_reproducible(self):
        room = Room(5, 5)
        first = candidates(room, columns=5, rows=5, limit=10)
        self.assertEqual(first[0], SpawnCandidate(2.5, 2.5, 2, 2))
        self.assertEqual(first, candidates(room, columns=5, rows=5, limit=10))
        self.assertEqual(first[:3], candidates(room, columns=5, rows=5, limit=3))
        self.assertEqual(len(first), 10)

    def test_obstacle_and_wall_safety_and_initial_state(self):
        room = Room(5, 5, ((2, 2, 1, 1),))
        positions = candidates(room, columns=5, rows=5, limit=25)
        self.assertEqual(len(positions), 24)
        self.assertNotIn((2.5, 2.5), {(p.x, p.y) for p in positions})
        for p in positions:
            with self.subTest(point=p):
                state = initial_state(room, p.x, p.y)
                self.assertEqual((state.x, state.y), (p.x, p.y))

    def test_clearance_blocks_touches_to_expanded_obstacle(self):
        room = Room(4, 4, ((1.5, 1.5, 1, 1),))
        zero = candidates(room, columns=4, rows=4, limit=16)
        wider = candidates(room, columns=4, rows=4, clearance=1, limit=16)
        self.assertTrue(zero)
        self.assertFalse(wider)

    def test_wall_clearance_inclusive(self):
        room = Room(3, 3)
        points = candidates(room, columns=3, rows=3, clearance=0.5, limit=9)
        self.assertEqual(len(points), 9)
        self.assertIn((0.5, 0.5), {(p.x, p.y) for p in points})
        self.assertEqual(candidates(room, columns=3, rows=3, clearance=1.1),
                         (SpawnCandidate(1.5, 1.5, 1, 1),))

    def test_no_candidates_when_covered(self):
        self.assertEqual(candidates(Room(2, 2, ((0, 0, 2, 2),))), ())

    def test_float_rounding_deduplicates_positions(self):
        room = Room(5e-324, 5e-324)
        positions = candidates(room, columns=100, rows=100, limit=1024)
        self.assertEqual(len({(p.x, p.y) for p in positions}), len(positions))
        for p in positions:
            initial_state(room, p.x, p.y)

    def test_mutated_room_is_revalidated_without_side_effects(self):
        room = Room(4, 4)
        object.__setattr__(room, "obstacles", ((3, 3, 2, 2),))
        with self.assertRaisesRegex(ValueError, "obstacle"):
            candidates(room)

    def test_numeric_types_are_strict(self):
        room = Room(4, 4)
        for value in (True, 0, -1, 129, 1.0, "4", math.inf, 10**1000):
            with self.subTest(columns=repr(value)[:30]):
                with self.assertRaises(ValueError):
                    candidates(room, columns=value)
        for value in (True, -1, math.nan, math.inf, -math.inf, "1", 10**1000):
            with self.subTest(clearance=repr(value)[:30]):
                with self.assertRaises(ValueError):
                    candidates(room, clearance=value)
        for value in (True, 0, -1, 1025, 1.0):
            with self.subTest(limit=repr(value)[:30]):
                with self.assertRaises(ValueError):
                    candidates(room, limit=value)
        with self.assertRaises(ValueError):
            candidates(object())

    def test_cpu_work_budget(self):
        room = Room(200, 200, tuple((i, i, .25, .25) for i in range(200)))
        self.assertGreater(128 * 128 * len(room.obstacles), MAX_GEOMETRY_CHECKS)
        with self.assertRaisesRegex(ValueError, "budget"):
            candidates(room, columns=128, rows=128)

    def test_does_not_mutate_room_or_obstacles(self):
        room = Room(4, 4, ((1.5, 1.5, 1, 1),))
        before = repr(room)
        candidates(room)
        self.assertEqual(repr(room), before)


if __name__ == "__main__":
    unittest.main()
