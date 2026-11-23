import unittest

from mojum.world_room import Room, initial_state
from mojum.world_airflow import (
    AirflowError, MAX_SOURCES, MAX_SOURCE_SPEED_MM_S, SCHEMA,
    sample_room_airflow,
)


class WorldAirflowTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(20.0, 20.0)
        self.state = initial_state(self.room, 1.0, 1.0)
        self.kw = {
            "tick": self.state.tick,
            "expected_revision": self.state.revision,
            "x_mm": 1000,
            "y_mm": 1000,
            "sources": [{"id": "east", "x_mm": 11000, "y_mm": 1000,
                         "vx_mm_s": 10000, "vy_mm_s": 0}],
            "public_synthetic": True,
        }

    def sample(self, room=None, state=None, **changes):
        args = dict(self.kw)
        args.update(changes)
        return sample_room_airflow(room or self.room, state or self.state, **args)

    def test_known_vector_and_schema(self):
        result = self.sample()
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual(result.vx_mm_s, 5000)
        self.assertEqual(result.vy_mm_s, 0)
        self.assertEqual(result.contributing_source_ids, ("east",))
        self.assertEqual(result.occluded_source_ids, ())
        self.assertEqual(len(result.digest), 64)

    def test_deterministic_and_source_order_independent(self):
        first = self.sample()
        second = self.sample(sources=list(reversed(self.kw["sources"])))
        self.assertEqual(first, second)

    def test_multiple_sources_compose(self):
        sources = [
            {"id": "b", "x_mm": 11000, "y_mm": 1000, "vx_mm_s": 10000, "vy_mm_s": 0},
            {"id": "a", "x_mm": 1000, "y_mm": 11000, "vx_mm_s": 0, "vy_mm_s": 10000},
        ]
        result = self.sample(sources=sources)
        self.assertEqual((result.vx_mm_s, result.vy_mm_s), (5000, 5000))
        self.assertEqual(result.contributing_source_ids, ("a", "b"))

    def test_obstacle_occludes_segment(self):
        room = Room(20.0, 10.0, ((5.0, 0.5, 1.0, 1.0),))
        state = initial_state(room, 1.0, 1.0)
        result = self.sample(room, state, expected_revision=state.revision, tick=state.tick)
        self.assertEqual((result.vx_mm_s, result.vy_mm_s), (0, 0))
        self.assertEqual(result.occluded_source_ids, ("east",))
        self.assertEqual(result.contributing_source_ids, ())

    def test_source_on_sample_point_has_full_weight(self):
        sources = [{"id": "local", "x_mm": 1000, "y_mm": 1000,
                    "vx_mm_s": -1234, "vy_mm_s": 567}]
        result = self.sample(sources=sources)
        self.assertEqual((result.vx_mm_s, result.vy_mm_s), (-1234, 567))

    def test_sample_rejects_outside_or_inside_obstacle(self):
        room = Room(20.0, 10.0, ((5.0, 0.5, 1.0, 1.0),))
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(AirflowError):
            self.sample(room, state, x_mm=6000, y_mm=1000,
                        expected_revision=state.revision)
        with self.assertRaises(AirflowError):
            self.sample(x_mm=20001)

    def test_rejects_private_or_stale_frame(self):
        with self.assertRaises(AirflowError):
            self.sample(public_synthetic=False)
        with self.assertRaises(AirflowError):
            self.sample(tick=1)
        with self.assertRaises(AirflowError):
            self.sample(expected_revision="0" * 64)

    def test_rejects_bool_and_float_sensor_coordinates(self):
        with self.assertRaises(AirflowError):
            self.sample(x_mm=True)
        with self.assertRaises(AirflowError):
            self.sample(x_mm=1000.0)

    def test_rejects_malformed_source_and_bool_numeric(self):
        malformed = [dict(self.kw["sources"][0], vx_mm_s=True)]
        with self.assertRaises(AirflowError):
            self.sample(sources=malformed)
        with self.assertRaises(AirflowError):
            self.sample(sources=[{"id": "x", "x_mm": 1, "y_mm": 1,
                                  "vx_mm_s": 2, "vy_mm_s": 3, "extra": 4}])

    def test_rejects_duplicate_ids_and_hostile_container_subclass(self):
        rows = self.kw["sources"] * 2
        with self.assertRaises(AirflowError):
            self.sample(sources=rows)
        class HostileList(list):
            pass
        with self.assertRaises(AirflowError):
            self.sample(sources=HostileList(self.kw["sources"]))

    def test_rejects_non_millimetre_room_geometry(self):
        room = Room(20.0001, 10.0)
        state = initial_state(room, 1.0, 1.0)
        with self.assertRaises(AirflowError):
            self.sample(room, state, expected_revision=state.revision)

    def test_empty_sources_yield_calm_field(self):
        result = self.sample(sources=[])
        self.assertEqual((result.vx_mm_s, result.vy_mm_s, result.magnitude_mm_s), (0, 0, 0))
        self.assertEqual(result.contributing_source_ids, ())

    def test_rejects_mapping_subclasses_and_source_inside_obstacle(self):
        class HostileDict(dict):
            pass
        with self.assertRaises(AirflowError):
            self.sample(sources=[HostileDict(self.kw["sources"][0])])
        room = Room(20.0, 10.0, ((5.0, 0.5, 1.0, 1.0),))
        state = initial_state(room, 1.0, 1.0)
        sources = [{"id": "inside", "x_mm": 5500, "y_mm": 1000,
                    "vx_mm_s": 0, "vy_mm_s": 0}]
        with self.assertRaises(AirflowError):
            self.sample(room, state, expected_revision=state.revision,
                        tick=state.tick, sources=sources)

    def test_source_and_result_speed_bounds(self):
        with self.assertRaises(AirflowError):
            self.sample(sources=[{"id": "fast", "x_mm": 11000, "y_mm": 1000,
                                  "vx_mm_s": MAX_SOURCE_SPEED_MM_S + 1, "vy_mm_s": 0}])
        many = [{"id": f"s{i:02d}", "x_mm": 1000, "y_mm": 1000,
                 "vx_mm_s": MAX_SOURCE_SPEED_MM_S, "vy_mm_s": 0}
                for i in range(MAX_SOURCES)]
        with self.assertRaises(AirflowError):
            self.sample(sources=many)

    def test_huge_native_integer_is_rejected_as_domain_error(self):
        with self.assertRaises(AirflowError):
            self.sample(sources=[{"id": "huge", "x_mm": 10**1000, "y_mm": 0,
                                  "vx_mm_s": 1, "vy_mm_s": 0}])


if __name__ == "__main__":
    unittest.main()
