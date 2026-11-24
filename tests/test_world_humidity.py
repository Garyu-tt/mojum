import unittest

from mojum.world_humidity import (
    HumidityProfile, MAX_TICK, observe_humidity, verify_observation,
)

REV = "a" * 64


class HostileStr(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise AssertionError("hostile equality must not execute")


class HumidityTests(unittest.TestCase):
    def profile(self, **changes):
        values = dict(room_revision=REV, width_mm=5000, height_mm=4000,
                      base_milli_percent=50_000,
                      gradient_x_milli_percent_per_km=1000,
                      gradient_y_milli_percent_per_km=-500,
                      drift_milli_percent_per_tick=2, epoch_tick=10)
        values.update(changes)
        return HumidityProfile(**values)

    def observe(self, profile=None, **kwargs):
        args = dict(room_revision=REV, tick=10, x_mm=1000, y_mm=1000)
        args.update(kwargs)
        return observe_humidity(profile or self.profile(), **args)

    def test_deterministic_integer_observation_and_digest(self):
        first = self.observe()
        second = self.observe()
        self.assertEqual(first, second)
        self.assertEqual(first["relative_humidity_milli_percent"], 50_000)
        self.assertTrue(verify_observation(first))
        self.assertFalse(first["physical_sensor"])

    def test_spatial_gradient_and_tick_drift(self):
        out = self.observe(tick=12, x_mm=2000, y_mm=1000)
        self.assertEqual(out["relative_humidity_milli_percent"], 50_005)

    def test_half_open_room_boundaries(self):
        for x, y in ((5000, 1), (1, 4000), (-1, 1), (1, -1)):
            with self.subTest(x=x, y=y):
                with self.assertRaisesRegex(ValueError, "outside"):
                    self.observe(x_mm=x, y_mm=y)

    def test_room_revision_and_tick_admission(self):
        with self.assertRaisesRegex(ValueError, "revision"):
            self.observe(room_revision="b" * 64)
        with self.assertRaises(ValueError):
            self.observe(tick=9)
        with self.assertRaises(ValueError):
            self.observe(tick=MAX_TICK + 1)

    def test_exact_native_integer_admission(self):
        for kwargs in ({"tick": True}, {"x_mm": 1.0}, {"y_mm": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.observe(**kwargs)

    def test_profile_bounds_and_subclass_rejection(self):
        for kwargs in ({"width_mm": 0}, {"width_mm": 1_000_000_001},
                       {"base_milli_percent": 100_001},
                       {"epoch_tick": True}, {"drift_milli_percent_per_tick": 1001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.observe(self.profile(**kwargs))
        with self.assertRaises(ValueError):
            self.observe(self.profile(room_revision=HostileStr(REV)))

    def test_out_of_physical_range_result_rejected(self):
        profile = self.profile(base_milli_percent=100_000,
                               drift_milli_percent_per_tick=1)
        with self.assertRaisesRegex(ValueError, "outside 0..100"):
            self.observe(profile, tick=11)

    def test_verifier_rejects_tampering_and_extra_fields(self):
        observation = self.observe()
        changed = dict(observation, tick=11)
        self.assertFalse(verify_observation(changed))
        extra = dict(observation, unexpected=1)
        with self.assertRaises(ValueError):
            verify_observation(extra)

    def test_verifier_rejects_hostile_keys_before_set_operations(self):
        observation = self.observe()
        observation[HostileStr("unexpected")] = 1
        with self.assertRaises(ValueError):
            verify_observation(observation)

    def test_verifier_rejects_non_native_nested_values(self):
        observation = self.observe()
        observation["position_mm"] = [True, 1]
        with self.assertRaises(ValueError):
            verify_observation(observation)


if __name__ == "__main__":
    unittest.main()
