import math
import unittest
from unittest.mock import patch

from mojum.world_pressure import PressureProfile, observe_pressure, verify_observation
from mojum.world_room import Room, RoomState, initial_state, _revision


class HostileStr(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise AssertionError("hostile equality must not execute")


class PressureTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(5.0, 4.0)
        self.state = initial_state(self.room, 1.0, 1.0)
        self.profile = PressureProfile(
            self.state.revision, 5000, 4000, 101000,
            gradient_x_pa_per_km=100, gradient_y_pa_per_km=-50,
            drift_pa_per_tick=2, epoch_tick=0,
        )

    def observe(self, **kwargs):
        args = dict(room=self.room, state=self.state, tick=0, x_mm=1000, y_mm=1000)
        args.update(kwargs)
        return observe_pressure(self.profile, **args)

    def test_deterministic_integer_observation_and_digest(self):
        first = self.observe()
        self.assertEqual(first, self.observe())
        self.assertEqual(first["pressure_pa"], 100999)
        self.assertTrue(verify_observation(first))
        self.assertIs(first["physical_sensor"], False)

    def test_spatial_gradient_and_logical_tick_drift(self):
        state = RoomState(1, 1.0, 1.0, _revision(self.room, 1, 1.0, 1.0))
        profile = PressureProfile(
            state.revision, 5000, 4000, 101000,
            gradient_x_pa_per_km=100, gradient_y_pa_per_km=-50,
            drift_pa_per_tick=2, epoch_tick=0,
        )
        out = observe_pressure(profile, room=self.room, state=state,
                               tick=1, x_mm=2000, y_mm=1000)
        self.assertEqual(out["pressure_pa"], 101001)

    def test_room_state_revision_and_tick_are_bound(self):
        with self.assertRaisesRegex(ValueError, "revision or tick"):
            self.observe(tick=1)
        bad = RoomState(0, 1.0, 1.0, "b" * 64)
        with self.assertRaisesRegex(ValueError, "stale or invalid"):
            observe_pressure(self.profile, room=self.room, state=bad,
                             tick=0, x_mm=1000, y_mm=1000)

    def test_exact_native_types_and_forged_profile(self):
        for kwargs in ({"tick": True}, {"x_mm": 1.0}, {"y_mm": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.observe(**kwargs)
        forged = PressureProfile(HostileStr(self.state.revision), 5000, 4000, 101000)
        with self.assertRaises(ValueError):
            observe_pressure(forged, room=self.room, state=self.state,
                             tick=0, x_mm=1000, y_mm=1000)

    def test_half_open_bounds_and_obstacle_rejection(self):
        for x, y in ((5000, 1), (1, 4000), (-1, 1), (1, -1)):
            with self.subTest(x=x, y=y), self.assertRaises(ValueError):
                self.observe(x_mm=x, y_mm=y)
        room = Room(5.0, 4.0, ((1.0, 1.0, 1.0, 1.0),))
        state = initial_state(room, 3.0, 3.0)
        profile = PressureProfile(state.revision, 5000, 4000, 101000)
        with self.assertRaisesRegex(ValueError, "inside obstacle"):
            observe_pressure(profile, room=room, state=state,
                             tick=0, x_mm=1500, y_mm=1500)

    def test_forged_room_geometry_is_revalidated_before_sampling(self):
        cases = (
            ("width", -1.0),
            ("obstacles", ((4.5, 3.5, 1.0, 1.0),)),
        )
        for attribute, forged_value in cases:
            with self.subTest(attribute=attribute):
                room = Room(5.0, 4.0)
                object.__setattr__(room, attribute, forged_value)
                state = RoomState(0, 1.0, 1.0, _revision(room, 0, 1.0, 1.0))
                profile = PressureProfile(state.revision, 5000, 4000, 101000)
                with self.assertRaisesRegex(ValueError, "canonical room geometry"):
                    observe_pressure(profile, room=room, state=state,
                                     tick=0, x_mm=1000, y_mm=1000)

    def test_profile_bounds_and_room_dimensions(self):
        cases = (
            {"width_mm": 0}, {"base_pressure_pa": 120001},
            {"gradient_x_pa_per_km": 501}, {"drift_pa_per_tick": 11},
            {"epoch_tick": True},
        )
        for changes in cases:
            values = dict(room_revision=self.state.revision, width_mm=5000,
                          height_mm=4000, base_pressure_pa=101000)
            values.update(changes)
            profile = PressureProfile(**values)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                observe_pressure(profile, room=self.room, state=self.state,
                                 tick=0, x_mm=1000, y_mm=1000)
        mismatch = PressureProfile(self.state.revision, 5001, 4000, 101000)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            observe_pressure(mismatch, room=self.room, state=self.state,
                             tick=0, x_mm=1000, y_mm=1000)

    def test_millimetre_room_dimensions_are_admitted_on_both_axes(self):
        for axis in (0, 1):
            for dimension_mm in (1001, 1003, 1005, 1007, 1009):
                with self.subTest(axis=axis, dimension_mm=dimension_mm):
                    dimensions_mm = [1000, 1000]
                    dimensions_mm[axis] = dimension_mm
                    room = Room(*(value / 1000 for value in dimensions_mm))
                    state = initial_state(room, 0.5, 0.5)
                    profile = PressureProfile(state.revision, *dimensions_mm, 101000)
                    observation = observe_pressure(
                        profile, room=room, state=state, tick=0, x_mm=500, y_mm=500
                    )
                    self.assertEqual(observation["pressure_pa"], 101000)
                    self.assertTrue(verify_observation(observation))

    def test_fractional_and_nearby_mismatched_room_dimensions_are_rejected(self):
        canonical_dimension = 1001 / 1000
        mismatches = (
            1002 / 1000,
            1.0015,
            math.nextafter(canonical_dimension, -math.inf),
            math.nextafter(canonical_dimension, math.inf),
        )
        for axis in (0, 1):
            for mismatch in mismatches:
                with self.subTest(axis=axis, mismatch=mismatch):
                    dimensions = [1.0, 1.0]
                    dimensions[axis] = mismatch
                    dimensions_mm = [1000, 1000]
                    dimensions_mm[axis] = 1001
                    room = Room(*dimensions)
                    state = initial_state(room, 0.5, 0.5)
                    profile = PressureProfile(state.revision, *dimensions_mm, 101000)
                    with self.assertRaisesRegex(ValueError, "dimensions"):
                        observe_pressure(
                            profile, room=room, state=state,
                            tick=0, x_mm=500, y_mm=500,
                        )

    def test_out_of_range_computed_pressure_rejected(self):
        state = RoomState(1, 1.0, 1.0, _revision(self.room, 1, 1.0, 1.0))
        profile = PressureProfile(state.revision, 5000, 4000, 120000,
                                  drift_pa_per_tick=1)
        with self.assertRaisesRegex(ValueError, "supported"):
            observe_pressure(profile, room=self.room, state=state,
                             tick=1, x_mm=1000, y_mm=1000)

    def test_verifier_rejects_tampering_and_extra_fields(self):
        observation = self.observe()
        self.assertFalse(verify_observation(dict(observation, tick=1)))
        with self.assertRaises(ValueError):
            verify_observation(dict(observation, unexpected=1))

    def test_verifier_rejects_hostile_and_oversized_keys_before_set(self):
        observation = self.observe()
        observation[HostileStr("unexpected")] = 1
        with self.assertRaises(ValueError):
            verify_observation(observation)
        observation = self.observe()
        observation["x" * 1000000] = observation.pop("digest")
        with patch("builtins.set", side_effect=AssertionError("set must not run")):
            with self.assertRaisesRegex(ValueError, "invalid observation key"):
                verify_observation(observation)

    def test_verifier_rejects_non_native_nested_values(self):
        observation = self.observe()
        observation["position_mm"] = [True, 1]
        with self.assertRaises(ValueError):
            verify_observation(observation)


if __name__ == "__main__":
    unittest.main()
