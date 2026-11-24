import unittest

from mojum.world_room import Room, RoomState, initial_state, advance, _revision
from mojum.world_odor import create_profile, sample, verify_receipt


class OdorFieldTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(5.0, 5.0, ((2.0, 0.0, 0.5, 4.0),))
        self.state = initial_state(self.room, 1.0, 1.0)
        self.profile = create_profile(
            self.room, created_tick=0, decay_per_tick=2,
            sources=[{"id": "vent", "x_mm": 1000, "y_mm": 1000, "concentration": 100}],
        )

    def run_sample(self, points, tick=0, occlusion=True):
        state = self.state
        if tick:
            state, _ = advance(self.room, state, tick=1, dx=0, dy=0,
                               expected_revision=state.revision)
        return sample(self.profile, self.room, state, tick=tick,
                      expected_revision=state.revision, points=points, occlusion=occlusion)

    def test_source_peak_and_distance_attenuation(self):
        result = self.run_sample([
            {"id": "near", "x_mm": 1000, "y_mm": 2000},
            {"id": "source", "x_mm": 1000, "y_mm": 1000},
        ], occlusion=False)
        values = {row["point_id"]: row["concentration"] for row in result["samples"]}
        self.assertEqual(values["source"], 100)
        self.assertLess(values["near"], values["source"])

    def test_opaque_obstacle_blocks_and_can_be_disabled(self):
        blocked = self.run_sample([{"id": "behind", "x_mm": 3000, "y_mm": 1000}])
        open_field = self.run_sample([{"id": "behind", "x_mm": 3000, "y_mm": 1000}], occlusion=False)
        self.assertEqual(blocked["samples"][0]["concentration"], 0)
        self.assertGreater(open_field["samples"][0]["concentration"], 0)

    def test_logical_tick_decay(self):
        result = self.run_sample([{"id": "source", "x_mm": 1000, "y_mm": 1000}], tick=1)
        self.assertEqual(result["samples"][0]["concentration"], 98)

    def test_deterministic_order_and_receipt(self):
        a = self.run_sample([{"id": "z", "x_mm": 1000, "y_mm": 1000},
                             {"id": "a", "x_mm": 1500, "y_mm": 1000}])
        b = self.run_sample([{"id": "a", "x_mm": 1500, "y_mm": 1000},
                             {"id": "z", "x_mm": 1000, "y_mm": 1000}])
        self.assertEqual(a, b)
        self.assertTrue(verify_receipt(a))
        changed = dict(a)
        changed["tick"] = 5
        self.assertFalse(verify_receipt(changed))

    def test_rejects_bool_as_integer_and_duplicate_ids(self):
        with self.assertRaises(ValueError):
            create_profile(self.room, created_tick=True, decay_per_tick=0,
                           sources=[{"id": "x", "x_mm": 1, "y_mm": 1, "concentration": 1}])
        with self.assertRaises(ValueError):
            create_profile(self.room, created_tick=0, decay_per_tick=0, sources=[
                {"id": "x", "x_mm": 1, "y_mm": 1, "concentration": 1},
                {"id": "x", "x_mm": 2, "y_mm": 2, "concentration": 1},
            ])

    def test_rejects_bad_points_and_stale_revision(self):
        with self.assertRaises(ValueError):
            self.run_sample([{"id": "p", "x_mm": True, "y_mm": 1000}])
        with self.assertRaises(ValueError):
            sample(self.profile, self.room, self.state, tick=0,
                   expected_revision="0" * 64, points=[{"id": "p", "x_mm": 1, "y_mm": 1}])

    def test_forged_profile_snapshot_is_rejected(self):
        from mojum.world_odor import OdorProfile
        forged = OdorProfile(self.profile.room_digest, 0, 0,
                             (("vent", True, 1000, 100),))
        with self.assertRaises(ValueError):
            sample(forged, self.room, self.state, tick=0,
                   expected_revision=self.state.revision,
                   points=[{"id": "p", "x_mm": 1000, "y_mm": 1000}])

    def test_profile_is_bound_to_room_geometry(self):
        other = Room(6.0, 5.0)
        with self.assertRaises(ValueError):
            sample(self.profile, other, self.state, tick=0,
                   expected_revision=self.state.revision,
                   points=[{"id": "p", "x_mm": 1000, "y_mm": 1000}])

    def test_create_profile_rejects_post_construction_room_obstacle_forgery(self):
        forged = Room(5.0, 5.0)
        object.__setattr__(forged, "obstacles", ((4.5, 4.5, 1.0, 1.0),))
        with self.assertRaisesRegex(ValueError, "canonical room geometry"):
            create_profile(
                forged, created_tick=0, decay_per_tick=0,
                sources=[{"id": "vent", "x_mm": 1000, "y_mm": 1000, "concentration": 1}],
            )

    def test_sample_rejects_post_construction_room_dimension_forgery(self):
        forged = Room(5.0, 5.0)
        object.__setattr__(forged, "width", 0.0)
        forged_state = RoomState(0, 1.0, 1.0, _revision(forged, 0, 1.0, 1.0))
        with self.assertRaisesRegex(ValueError, "canonical room geometry"):
            sample(
                self.profile, forged, forged_state, tick=0,
                expected_revision=forged_state.revision,
                points=[{"id": "nose", "x_mm": 1000, "y_mm": 1000}],
            )


if __name__ == "__main__":
    unittest.main()
