"""Bounded CPU-only reactive point-room controller, ported without changing Aquarium ABI.

The controller is an opt-in pure function. These tests never open a journal or
request an actuator command, and they use the world_room API from current main.
"""

import math
import unittest

from mojum.reactive_controller import ControllerError, SCHEMA, checked_goal, decide
from mojum.world_room import Room, advance, initial_state


class ReactiveControllerStandaloneTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, 0.1, 2),))
        self.state = initial_state(self.room, 1.0, 2.0)

    @staticmethod
    def sensory(tick=0, pulse=True):
        return {
            "kind": "interoception",
            "observed_at_ns": tick * 10_000_000,
            "heartbeat": {"pulse": pulse},
        }

    def test_no_actuator_and_bounded_first_step(self):
        command = decide(self.room, self.state, self.sensory(), (1.3, 2.0))
        self.assertEqual(command["schema"], SCHEMA)
        self.assertEqual(command["status"], "DETERMINISTIC_NONLEARNING_FIXTURE")
        self.assertEqual(command["world_revision"], self.state.revision)
        self.assertEqual(command["observed_at_ns"], 0)
        self.assertEqual((command["dx"], command["dy"]), (0.02, 0.0))
        self.assertIsNone(command["motor_command"])
        self.assertEqual(command["actuation"], "SYNTHETIC_POINT_ROOM_ONLY")
        self.assertEqual(command, decide(self.room, self.state, self.sensory(), (1.3, 2.0)))

    def test_prior_blocked_horizontal_attempt_uses_vertical_axis(self):
        open_room = Room(4, 4)
        state = initial_state(open_room, 1.0, 1.0)
        previous = {"dx": 0.04, "dy": 0.0, "blocked": True}
        command = decide(open_room, state, self.sensory(pulse=False), (2.0, 2.0), previous)
        self.assertEqual(command["axis"], "y")
        self.assertEqual((command["dx"], command["dy"]), (0.0, 0.04))
        self.assertTrue(command["prior_blocked"])
        after, feedback = advance(open_room, state, tick=1, dx=command["dx"], dy=command["dy"],
                                  expected_revision=state.revision)
        self.assertFalse(feedback["blocked"])
        self.assertAlmostEqual(after.y, 1.04)

    def test_repeated_world_steps_stay_under_four_centimeters(self):
        state = self.state
        previous = None
        for tick in range(20):
            command = decide(self.room, state, self.sensory(tick, tick % 2 == 0),
                             (1.3, 2.0), previous)
            self.assertLessEqual(math.hypot(command["dx"], command["dy"]), 0.04 + 1e-12)
            self.assertIsNone(command["motor_command"])
            after, feedback = advance(self.room, state, tick=tick + 1,
                                      dx=command["dx"], dy=command["dy"],
                                      expected_revision=state.revision)
            previous = {
                "dx": command["dx"], "dy": command["dy"], "blocked": feedback["blocked"],
            }
            state = after
        self.assertAlmostEqual(state.x, 1.3)
        self.assertAlmostEqual(state.y, 2.0)

    def test_invalid_goals_fail_closed(self):
        for i, goal in enumerate((None, (), (True, 2), ("1", 2), (float("nan"), 2),
                     (float("inf"), 2), (-1, 0), (5, 5), (2, 2),
                     (10 ** 5000, 1))):
            with self.subTest(case=i), self.assertRaises(ControllerError):
                checked_goal(self.room, goal)
        self.assertEqual(self.state, initial_state(self.room, 1.0, 2.0))

    def test_invalid_observations_and_prior_feedback_fail_closed(self):
        valid = self.sensory()
        bad_inputs = (
            None, {}, {"kind": "other"}, {**valid, "observed_at_ns": True},
            {**valid, "observed_at_ns": -1},
            {**valid, "heartbeat": {"pulse": 1}},
            {**valid, "heartbeat": {}},
        )
        for bad in bad_inputs:
            with self.subTest(sensor=str(bad)[:64]), self.assertRaises(ControllerError):
                decide(self.room, self.state, bad, (1.3, 2.0))
        for previous in ({}, {"dx": 0, "dy": 0, "blocked": 1},
                         {"dx": float("inf"), "dy": 0, "blocked": False},
                         {"dx": 0, "dy": 0, "blocked": False, "extra": 1}):
            with self.subTest(previous=previous), self.assertRaises(ControllerError):
                decide(self.room, self.state, valid, (1.3, 2.0), previous)


if __name__ == "__main__":
    unittest.main()
