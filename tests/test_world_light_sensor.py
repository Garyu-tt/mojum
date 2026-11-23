"""Regression tests for the bounded point-room illumination sensor."""
import math
import unittest
from dataclasses import FrozenInstanceError

from mojum.world_room import Room, RoomState, initial_state
from mojum.world_light_sensor import (
    IlluminationFrame, LightSensorError, measure_illumination,
)


class WorldLightSensorTests(unittest.TestCase):
    def measure(self, room, state, sources=(), ambient=0.0, **overrides):
        args = {
            "tick": state.tick,
            "expected_revision": state.revision,
            "sources": sources,
            "ambient": ambient,
        }
        args.update(overrides)
        return measure_illumination(room, state, **args)

    def test_inverse_square_fixture_contribution(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        frame = self.measure(room, state, (("lamp", 5.0, 1.0, 1.0),))
        self.assertAlmostEqual(frame.direct, 1.0 / 17.0)
        self.assertEqual(frame.visible_sources, ("lamp",))
        self.assertEqual(frame.occluded_sources, ())

    def test_wall_blocks_direct_light(self):
        room = Room(10.0, 10.0, ((4.0, 4.0, 1.0, 2.0),))
        state = initial_state(room, 1.0, 5.0)
        frame = self.measure(room, state, (("lamp", 9.0, 5.0, 1.0),))
        self.assertEqual(frame.direct, 0.0)
        self.assertEqual(frame.occluded_sources, ("lamp",))
        self.assertEqual(frame.visible_sources, ())

    def test_path_around_obstacle_remains_visible(self):
        room = Room(10.0, 10.0, ((4.0, 4.0, 1.0, 2.0),))
        state = initial_state(room, 1.0, 1.0)
        frame = self.measure(room, state, (("lamp", 9.0, 2.0, 1.0),))
        self.assertEqual(frame.visible_sources, ("lamp",))
        self.assertGreater(frame.direct, 0.0)

    def test_corner_tangency_counts_as_shadow(self):
        room = Room(10.0, 10.0, ((4.0, 1.0, 1.0, 3.0),))
        state = initial_state(room, 1.0, 1.0)
        frame = self.measure(room, state, (("lamp", 9.0, 9.0, 1.0),))
        self.assertEqual(frame.occluded_sources, ("lamp",))

    def test_ambient_and_direct_are_capped(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        sources = (("a", 1.0, 1.0, 1.0), ("b", 1.0, 1.0, 1.0))
        frame = self.measure(room, state, sources, ambient=0.75)
        self.assertEqual(frame.direct, 1.0)
        self.assertEqual(frame.total, 1.0)

    def test_empty_sources_returns_ambient_only(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 2.0, 2.0)
        frame = self.measure(room, state, ambient=0.3)
        self.assertEqual((frame.direct, frame.total), (0.0, 0.3))

    def test_deterministic_digest_and_immutable_output(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        args = (("lamp", 8.0, 8.0, 0.7),)
        first = self.measure(room, state, args)
        second = self.measure(room, state, args)
        self.assertEqual(first, second)
        self.assertEqual(len(first.digest), 64)
        self.assertIsInstance(first, IlluminationFrame)
        with self.assertRaises(FrozenInstanceError):
            first.total = 0.0

    def test_integer_coordinates_preserve_room_revision_encoding(self):
        room = Room(10, 10)
        state = initial_state(room, 1, 1)
        frame = self.measure(room, state, (("lamp", 2, 1, 1),))
        self.assertGreater(frame.direct, 0.0)

    def test_stale_revision_is_rejected(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(LightSensorError):
            self.measure(room, state, expected_revision="0" * 64)

    def test_tick_must_match_state(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(LightSensorError):
            self.measure(room, state, tick=state.tick + 1)

    def test_mutated_state_with_old_revision_is_rejected(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        forged = RoomState(state.tick, 2.0, state.y, state.revision)
        with self.assertRaises(LightSensorError):
            self.measure(room, forged)

    def test_source_inside_obstacle_is_rejected(self):
        room = Room(10.0, 10.0, ((4.0, 4.0, 2.0, 2.0),))
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(LightSensorError):
            self.measure(room, state, (("lamp", 5.0, 5.0, 1.0),))

    def test_sensor_inside_obstacle_is_rejected(self):
        room = Room(10.0, 10.0, ((4.0, 4.0, 2.0, 2.0),))
        state = RoomState(0, 5.0, 5.0, "0" * 64)
        with self.assertRaises(LightSensorError):
            self.measure(room, state)

    def test_invalid_nonfinite_and_bool_values_are_rejected(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        for bad in (True, float("nan"), float("inf"), -0.1, 1.1, 10**500):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(LightSensorError):
                    self.measure(room, state, (("lamp", 2.0, 2.0, bad),))

    def test_duplicate_and_invalid_source_ids_are_rejected(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(LightSensorError):
            self.measure(room, state, (("same", 2, 2, 1), ("same", 3, 3, 1)))
        with self.assertRaises(LightSensorError):
            self.measure(room, state, (("../bad", 2, 2, 1),))

    def test_source_count_and_container_are_bounded(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        many = tuple((f"s{i}", 2.0, 2.0, 0.1) for i in range(17))
        with self.assertRaises(LightSensorError):
            self.measure(room, state, many)
        with self.assertRaises(LightSensorError):
            self.measure(room, state, list(many[:1]))

    def test_large_room_is_rejected_by_sensor_budget(self):
        room = Room(1_000_001.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(LightSensorError):
            self.measure(room, state)

    def test_metadata_disclaims_physical_contact_and_photometry(self):
        room = Room(10.0, 10.0)
        state = initial_state(room, 1.0, 1.0)
        frame = self.measure(room, state)
        self.assertFalse(frame.physical_contact)
        self.assertEqual(frame.provenance, "public-synthetic-point-room-illumination/v1")
        self.assertEqual(frame.schema, "mojum.world.illumination/1")


if __name__ == "__main__":
    unittest.main()
