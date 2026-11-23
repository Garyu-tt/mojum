"""Hermetic tests for the synthetic room-climate sampler."""
from dataclasses import FrozenInstanceError, replace
import unittest

from mojum.world_climate import (
    ClimateError,
    MAX_TICK,
    PUBLIC_SYNTHETIC,
    sample_room_climate,
)
from mojum.world_room import Room, RoomState, initial_state


class WorldClimateTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(10, 8)
        self.state = initial_state(self.room, 1.5, 2)

    def sample(self, **kwargs):
        defaults = {
            "base_temperature_millic": 20_000,
            "gradient_x_millic_per_m": 1_000,
            "gradient_y_millic_per_m": 500,
            "humidity_permille": 650,
            "provenance": PUBLIC_SYNTHETIC,
        }
        defaults.update(kwargs)
        return sample_room_climate(self.room, self.state, **defaults)

    def test_affine_field_and_metadata(self):
        frame = self.sample()
        self.assertEqual(frame.temperature_millic, 22_500)
        self.assertEqual(frame.humidity_permille, 650)
        self.assertEqual(frame.tick, 0)
        self.assertEqual(frame.room_revision, self.state.revision)
        self.assertEqual(frame.provenance, PUBLIC_SYNTHETIC)

    def test_origin_has_base_temperature(self):
        state = initial_state(self.room, 0, 0)
        frame = sample_room_climate(
            self.room, state, base_temperature_millic=18_500,
            humidity_permille=0, provenance=PUBLIC_SYNTHETIC,
        )
        self.assertEqual(frame.temperature_millic, 18_500)
        self.assertEqual(frame.humidity_permille, 0)

    def test_exact_half_rounds_away_from_zero(self):
        positive = Room(1, 1)
        pos_state = initial_state(positive, 0.5, 0)
        pos = sample_room_climate(
            positive, pos_state, base_temperature_millic=20_000,
            gradient_x_millic_per_m=1, provenance=PUBLIC_SYNTHETIC,
        )
        negative = sample_room_climate(
            positive, pos_state, base_temperature_millic=20_000,
            gradient_x_millic_per_m=-1, provenance=PUBLIC_SYNTHETIC,
        )
        self.assertEqual(pos.temperature_millic, 20_001)
        self.assertEqual(negative.temperature_millic, 19_999)

    def test_repeat_is_deterministic_and_frame_is_frozen(self):
        first = self.sample()
        self.assertEqual(first, self.sample())
        with self.assertRaises(FrozenInstanceError):
            first.temperature_millic = 0

    def test_requires_explicit_provenance(self):
        for provenance in (None, "", "PUBLIC", "PUBLIC_SYNTHETIC "):
            with self.subTest(provenance=provenance):
                with self.assertRaises(ClimateError):
                    self.sample(provenance=provenance)

    def test_hostile_provenance_comparison_is_never_invoked(self):
        class HostileProvenance:
            def __eq__(self, other):
                raise AssertionError("untrusted equality must not run")

        with self.assertRaises(ClimateError):
            self.sample(provenance=HostileProvenance())

    def test_rejects_boolean_and_non_integer_parameters(self):
        for name, value in (
            ("base_temperature_millic", True),
            ("gradient_x_millic_per_m", 1.0),
            ("gradient_y_millic_per_m", False),
            ("humidity_permille", "500"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ClimateError):
                    self.sample(**{name: value})

    def test_rejects_out_of_range_inputs_and_result(self):
        for name, value in (
            ("base_temperature_millic", 100_001),
            ("gradient_x_millic_per_m", 100_001),
            ("humidity_permille", 1001),
            ("humidity_permille", -1),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ClimateError):
                    self.sample(**{name: value})
        with self.assertRaises(ClimateError):
            self.sample(
                base_temperature_millic=100_000,
                gradient_x_millic_per_m=1,
            )

    def test_rejects_stale_revision_and_invalid_tick(self):
        stale = replace(self.state, revision="0" * 64)
        with self.assertRaises(ClimateError):
            sample_room_climate(
                self.room, stale, base_temperature_millic=20_000,
                provenance=PUBLIC_SYNTHETIC,
            )
        bad_tick = replace(self.state, tick=True)
        with self.assertRaises(ClimateError):
            sample_room_climate(
                self.room, bad_tick, base_temperature_millic=20_000,
                provenance=PUBLIC_SYNTHETIC,
            )

    def test_rejects_obstructed_and_noncanonical_inputs(self):
        blocked_room = Room(5, 5, ((1, 1, 2, 2),))
        from mojum.world_room import _revision
        # The position is inside the closed obstacle rectangle. Construct a
        # matching revision so rejection exercises occupancy, not staleness.
        blocked_state = RoomState(
            0, 1.5, 1.5, _revision(blocked_room, 0, 1.5, 1.5)
        )
        with self.assertRaises(ClimateError):
            sample_room_climate(
                blocked_room, blocked_state, base_temperature_millic=20_000,
                provenance=PUBLIC_SYNTHETIC,
            )

        class RoomChild(Room):
            pass

        with self.assertRaises(ClimateError):
            sample_room_climate(
                RoomChild(10, 8), self.state, base_temperature_millic=20_000,
                provenance=PUBLIC_SYNTHETIC,
            )

    def test_tick_upper_bound_is_admitted_only_when_revision_matches(self):
        from mojum.world_room import _revision
        state = RoomState(
            MAX_TICK, self.state.x, self.state.y,
            _revision(self.room, MAX_TICK, self.state.x, self.state.y),
        )
        frame = sample_room_climate(
            self.room, state, base_temperature_millic=20_000,
            provenance=PUBLIC_SYNTHETIC,
        )
        self.assertEqual(frame.tick, MAX_TICK)


if __name__ == "__main__":
    unittest.main()
