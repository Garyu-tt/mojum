import unittest
from mojum.world_room import Room, initial_state, advance
from mojum.world_thermal import ThermalFieldError, ThermalProfile, observe_thermal_field


class WorldThermalTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 3, ((1, 1, 1, 1),))
        self.state = initial_state(self.room, 0.5, 0.5)

    def observe(self, **kwargs):
        args = dict(room=self.room, state=self.state, tick=0,
                    expected_revision=self.state.revision, profile=ThermalProfile(),
                    points_mm=[(0, 0), (1000, 500)])
        args.update(kwargs)
        return observe_thermal_field(**args)

    def test_deterministic_digest_and_schema(self):
        a, b = self.observe(), self.observe()
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], "mojum.world-thermal/1")
        self.assertFalse(a["physical_sensor"])
        self.assertEqual(len(a["digest_sha256"]), 64)

    def test_integer_spatial_gradient(self):
        result = self.observe(profile=ThermalProfile(
            base_millic=20_000, gradient_x_millic_per_m=1500,
            gradient_y_millic_per_m=-500))
        self.assertEqual(result["samples"][1]["temperature_millic"], 21_250)

    def test_cycle_uses_logical_tick(self):
        profile = ThermalProfile(cycle_period_ticks=8, cycle_amplitude_millic=1000)
        moved, _ = advance(self.room, self.state, tick=1, dx=0, dy=0,
                           expected_revision=self.state.revision)
        result = observe_thermal_field(self.room, moved, tick=1,
            expected_revision=moved.revision, profile=profile, points_mm=[(0, 0)])
        self.assertEqual(result["samples"][0]["temperature_millic"], 19_750)

    def test_stale_revision_rejected(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(expected_revision="0" * 64)

    def test_tick_mismatch_rejected(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(tick=1)

    def test_closed_obstacle_rejected(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(points_mm=[(1000, 1500)])

    def test_bool_coordinate_rejected(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(points_mm=[(True, 0)])

    def test_bool_profile_value_rejected(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(profile=ThermalProfile(base_millic=True))

    def test_partial_cycle_rejected(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(profile=ThermalProfile(cycle_period_ticks=8))

    def test_temperature_bound_enforced(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(profile=ThermalProfile(base_millic=99_000,
                max_abs_temp_millic=99_000, gradient_x_millic_per_m=1000))

    def test_point_budget_enforced(self):
        with self.assertRaises(ThermalFieldError):
            self.observe(points_mm=[(0, 0)] * 257)

    def test_profile_remains_immutable(self):
        profile = ThermalProfile()
        result = self.observe(profile=profile)
        self.assertEqual(profile.base_millic, 20_000)
        self.assertEqual(result["samples"][0]["temperature_millic"], 20_000)


if __name__ == "__main__":
    unittest.main()
