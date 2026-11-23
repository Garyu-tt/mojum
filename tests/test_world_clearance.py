"""Hermetic CPU-only regression tests for synthetic nearest-surface frames."""

import hashlib
import json
import math
import unittest

from mojum.world_clearance import (
    ClearanceError, SCHEMA, clearance_frame,
)
from mojum.world_room import Room, RoomState, advance, initial_state


class ClearanceFrameTests(unittest.TestCase):
    def test_wall_and_deterministic_digest(self):
        room = Room(10, 10)
        state = initial_state(room, 1, 2)
        frame = clearance_frame(room, state)
        self.assertEqual(frame, clearance_frame(room, state))
        self.assertEqual(frame["schema"], SCHEMA)
        self.assertEqual(frame["distance_m"], 1.0)
        self.assertEqual(frame["nearest"], {"kind": "wall", "name": "west"})
        self.assertFalse(frame["physical_contact"])
        self.assertFalse(frame["actuating"])
        digest = frame["digest_sha256"]
        serialized = json.dumps({k: v for k, v in frame.items()
                                 if k != "digest_sha256"},
                                sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True, allow_nan=False).encode("ascii")
        self.assertEqual(digest, hashlib.sha256(serialized).hexdigest())

    def test_wall_touch_has_zero_clearance(self):
        frame = clearance_frame(Room(10, 10), initial_state(Room(10, 10), 0, 5))
        self.assertEqual(frame["distance_m"], 0)
        self.assertEqual(frame["nearest"], {"kind": "wall", "name": "west"})

    def test_equal_wall_distances_choose_canonical_order(self):
        room = Room(10, 10)
        frame = clearance_frame(room, initial_state(room, 1, 1))
        self.assertEqual(frame["nearest"]["name"], "west")

    def test_orthogonal_obstacle_is_nearest(self):
        room = Room(20, 20, ((8, 6, 1, 2),))
        frame = clearance_frame(room, initial_state(room, 7, 7))
        self.assertEqual(frame["distance_m"], 1)
        self.assertEqual(frame["nearest"], {"kind": "obstacle", "index": 0})

    def test_euclidean_diagonal_corner_distance(self):
        room = Room(20, 20, ((8, 8, 1, 1),))
        frame = clearance_frame(room, initial_state(room, 6, 6))
        self.assertEqual(frame["distance_m"], math.hypot(2, 2))
        self.assertEqual(frame["nearest"], {"kind": "obstacle", "index": 0})

    def test_obstacle_tie_preserves_insertion_order(self):
        room = Room(20, 20, ((8, 9, 1, 2), (11, 9, 1, 2)))
        frame = clearance_frame(room, initial_state(room, 10, 10))
        self.assertEqual(frame["nearest"], {"kind": "obstacle", "index": 0})

    def test_saturated_range_masks_feature(self):
        room = Room(20, 20, ((8, 8, 1, 1),))
        state = initial_state(room, 6, 6)
        frame = clearance_frame(room, state, max_range_m=2)
        self.assertTrue(frame["saturated"])
        self.assertIsNone(frame["nearest"])
        self.assertEqual(frame["distance_m"], 2.0)
        full = clearance_frame(room, state, max_range_m=math.hypot(2, 2))
        self.assertFalse(full["saturated"])
        self.assertEqual(full["nearest"], {"kind": "obstacle", "index": 0})

    def test_read_only_even_after_normal_advance(self):
        room = Room(10, 10, ((5, 5, 1, 1),))
        state = initial_state(room, 2, 2)
        later, observation = advance(room, state, tick=1, dx=1, dy=0,
                                     expected_revision=state.revision)
        self.assertFalse(observation["blocked"])
        original = (room.obstacles, later.revision, later.tick)
        frame = clearance_frame(room, later)
        self.assertEqual(frame["tick"], 1)
        self.assertEqual((room.obstacles, later.revision, later.tick), original)

    def test_reject_tampered_or_stale_room_state(self):
        room = Room(10, 10)
        state = initial_state(room, 2, 2)
        invalid = (
            RoomState(0, 3, 2, state.revision),
            RoomState(1, 2, 2, state.revision),
            RoomState(0, 2, 2, "f" * 64),
            RoomState(0, 2, 2, "invalid"),
            RoomState(True, 2, 2, state.revision),
            RoomState(-1, 2, 2, state.revision),
            RoomState(2**64, 2, 2, state.revision),
            RoomState(0, float("nan"), 2, state.revision),
            RoomState(0, -1, 2, state.revision),
            RoomState(0, True, 2, state.revision),
        )
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(ClearanceError):
                clearance_frame(room, bad)
        with self.assertRaises(ClearanceError):
            clearance_frame(Room(11, 10), state)

    def test_reject_forged_obstacle_contact(self):
        room = Room(10, 10, ((3, 3, 1, 1),))
        state = RoomState(0, 3, 3, "0" * 64)
        with self.assertRaises(ClearanceError):
            clearance_frame(room, state)

    def test_revalidate_mutated_frozen_room(self):
        room = Room(10, 10)
        state = initial_state(room, 2, 2)
        object.__setattr__(room, "obstacles", ((-1, 3, 2, 2),))
        with self.assertRaises(ClearanceError):
            clearance_frame(room, state)

    def test_reject_invalid_budget_or_types(self):
        room = Room(10, 10)
        state = initial_state(room, 2, 2)
        for value in (True, False, 0, -1, None, "10", float("nan"),
                      float("inf"), 1_000_001, 10**1000):
            with self.subTest(value=repr(value)), self.assertRaises(ClearanceError):
                clearance_frame(room, state, max_range_m=value)
        with self.assertRaises(ClearanceError):
            clearance_frame(Room(2_000_000, 1), initial_state(Room(2_000_000, 1), 0, 0))
        with self.assertRaises(ClearanceError):
            clearance_frame({"width": 10}, state)


if __name__ == "__main__":
    unittest.main()
