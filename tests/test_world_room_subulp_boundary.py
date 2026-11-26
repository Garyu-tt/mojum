"""Regression: reject mathematically outward steps hidden by binary rounding.

The synthetic point-room is not MuJoCo. These tests exercise the canonical
runtime `advance`, not a duplicate reference oracle or hardware control.
"""
from __future__ import annotations

import math
import unittest

from mojum.world_room import Room, advance, initial_state


class SubUlpBoundaryAdmissionTests(unittest.TestCase):
    def step(self, room, x, y, dx, dy):
        start = initial_state(room, x, y)
        next_state, observation = advance(
            room, start, tick=1, dx=dx, dy=dy,
            expected_revision=start.revision,
        )
        return start, next_state, observation

    def assert_blocked_without_motion(self, room, x, y, dx, dy):
        initial, final, frame = self.step(room, x, y, dx, dy)
        self.assertTrue(frame["blocked"])
        self.assertEqual((final.x, final.y), (x, y))
        self.assertEqual(frame["position"], [x, y])
        self.assertEqual(final.tick, 1)
        self.assertNotEqual(final.revision, initial.revision)
        self.assertEqual(final.revision, frame["revision"])
        self.assertFalse(frame["physical_contact"])
        self.assertEqual(frame["provenance"], "deterministic-point-room-fixture/v1")

    def test_right_wall_outward_delta_rounded_to_stationary(self):
        room = Room(1.0, 1.0)
        self.assertEqual(1.0 + 1e-16, 1.0)
        self.assert_blocked_without_motion(room, 1.0, 0.5, 1e-16, 0.0)

    def test_top_wall_outward_delta_rounded_to_stationary(self):
        room = Room(1.0, 1.0)
        self.assertEqual(1.0 + 1e-16, 1.0)
        self.assert_blocked_without_motion(room, 0.5, 1.0, 0.0, 1e-16)

    def test_near_right_wall_outward_sum_rounds_to_wall(self):
        room = Room(1.0, 1.0)
        x = math.nextafter(1.0, 0.0)
        dx = (1.0 - x) * 1.5
        self.assertEqual(x + dx, 1.0)
        # The exact rational sum is greater than one, even though rounded
        # floating-point addition reports an exactly-on-wall endpoint.
        self.assert_blocked_without_motion(room, x, 0.5, dx, 0.0)

    def test_near_top_wall_outward_sum_rounds_to_wall(self):
        room = Room(1.0, 1.0)
        y = math.nextafter(1.0, 0.0)
        dy = (1.0 - y) * 1.5
        self.assertEqual(y + dy, 1.0)
        self.assert_blocked_without_motion(room, 0.5, y, 0.0, dy)

    def test_diagonal_fails_atomically_if_either_axis_crosses(self):
        self.assert_blocked_without_motion(
            Room(1.0, 2.0), 1.0, 0.5, 1e-16, 0.25
        )

    def test_left_wall_outward_and_bottom_wall_outward_still_block(self):
        room = Room(1.0, 1.0)
        self.assert_blocked_without_motion(room, 0.0, 0.5, -1e-16, 0.0)
        self.assert_blocked_without_motion(room, 0.5, 0.0, 0.0, -1e-16)

    def test_exact_landing_at_room_edge_is_not_blocked(self):
        room = Room(1.0, 1.0)
        x = math.nextafter(1.0, 0.0)
        _, final, frame = self.step(room, x, 0.5, 1.0 - x, 0.0)
        self.assertFalse(frame["blocked"])
        self.assertEqual(final.x, 1.0)

    def test_interior_exact_sum_rounding_to_edge_is_allowed(self):
        room = Room(1.0, 1.0)
        x = math.nextafter(1.0, 0.0)
        dx = (1.0 - x) * 0.75
        self.assertEqual(x + dx, 1.0)
        _, final, frame = self.step(room, x, 0.5, dx, 0.0)
        self.assertFalse(frame["blocked"])
        self.assertEqual(final.x, 1.0)

    def test_zero_motion_is_not_misclassified(self):
        room = Room(1.0, 1.0)
        _, final, frame = self.step(room, 1.0, 1.0, 0.0, 0.0)
        self.assertFalse(frame["blocked"])
        self.assertEqual((final.x, final.y), (1.0, 1.0))
        self.assertEqual(final.tick, 1)

    def test_subulp_inward_move_is_not_blocked(self):
        room = Room(1.0, 1.0)
        tiny = math.ulp(0.0)
        self.assertEqual(1.0 - tiny, 1.0)
        _, final, frame = self.step(room, 1.0, 0.5, -tiny, 0.0)
        self.assertFalse(frame["blocked"])
        self.assertEqual(final.x, 1.0)

    def test_large_integer_width_does_not_hide_outward_move(self):
        edge = 2 ** 60
        room = Room(edge, 1.0)
        self.assertEqual(edge + 1.0, edge)
        self.assert_blocked_without_motion(room, edge, 0.5, 1.0, 0.0)

    def test_replay_is_identical_and_cas_still_enforced(self):
        room = Room(1.0, 1.0)
        first = self.step(room, 1.0, 0.5, 1e-16, 0.0)
        second = self.step(room, 1.0, 0.5, 1e-16, 0.0)
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            advance(room, first[0], tick=1, dx=1e-16, dy=0,
                    expected_revision="stale")
        with self.assertRaises(ValueError):
            advance(room, first[0], tick=1, dx=float("nan"), dy=0,
                    expected_revision=first[0].revision)


if __name__ == "__main__":
    unittest.main()
