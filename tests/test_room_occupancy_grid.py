"""Contract tests for the offline occupancy rasterizer."""
import unittest
from unittest.mock import patch
from mojum.room_occupancy_grid import rasterize_occupancy, SCHEMA


def payload(**overrides):
    result = dict(schema=SCHEMA, provenance="PUBLIC_SYNTHETIC",
                  room=dict(x0=0, y0=0, x1=25, y1=20),
                  cell_mm=10, obstacles=[dict(x0=10, y0=0, x1=20, y1=10)])
    result.update(overrides)
    return result


class OccupancyTests(unittest.TestCase):
    def test_grid(self):
        out = rasterize_occupancy(payload())
        self.assertEqual((out["width"], out["height"]), (3, 2))
        self.assertEqual(out["rows"], ((False, True, False), (False, False, False)))
        self.assertEqual(out["occupied"], 1)

    def test_determinism(self):
        self.assertEqual(rasterize_occupancy(payload())["sha256"],
                         rasterize_occupancy(payload())["sha256"])

    def test_edge_touch_not_occupied(self):
        out = rasterize_occupancy(payload(obstacles=[
            dict(x0=25, y0=0, x1=26, y1=20)]))
        self.assertEqual(out["occupied"], 0)

    def test_partial_edge_cell(self):
        out = rasterize_occupancy(payload(obstacles=[
            dict(x0=24, y0=19, x1=26, y1=21)]))
        self.assertEqual(out["rows"][1][2], True)

    def test_empty_obstacles(self):
        self.assertEqual(rasterize_occupancy(payload(obstacles=[]))["occupied"], 0)

    def test_invalid_types(self):
        cases = [
            {"cell_mm": True}, {"cell_mm": 0}, {"cell_mm": 1.5},
            {"obstacles": ()}, {"obstacles": [dict(x0=0, y0=0, x1=0, y1=1)]},
            {"room": dict(x0=0, y0=0, x1=True, y1=10)},
            {"provenance": "PRIVATE"}, {"schema": "other"},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                rasterize_occupancy(payload(**change))

    def test_budget(self):
        with self.assertRaises(ValueError):
            rasterize_occupancy(payload(room=dict(x0=0, y0=0, x1=1000, y1=1000),
                                        cell_mm=1))

    def test_immutable(self):
        out = rasterize_occupancy(payload())
        with self.assertRaises(TypeError):
            out["occupied"] = 42

    def test_maximum_grid_and_overlapping_obstacles(self):
        full = dict(x0=0, y0=0, x1=256, y1=256)
        out = rasterize_occupancy(payload(
            room=dict(x0=0, y0=0, x1=256, y1=256),
            cell_mm=1,
            obstacles=[
                dict(full),
                dict(x0=0, y0=0, x1=128, y1=128),
                dict(x0=64, y0=64, x1=192, y1=192),
            ],
        ))
        self.assertEqual((out["width"], out["height"]), (256, 256))
        self.assertEqual(out["occupied"], 65536)
        self.assertTrue(all(all(row) for row in out["rows"]))

    def test_hostile_string_subclass_keys_rejected_before_hash_or_equality(self):
        class HostileKey(str):
            def __hash__(self):
                return hash(str(self))

            def __eq__(self, other):
                raise AssertionError("hostile key equality must not execute")

        top = payload()
        del top["schema"]
        top[HostileKey("schema")] = SCHEMA
        with self.assertRaises(ValueError):
            rasterize_occupancy(top)

        nested = payload()
        rect = nested["room"]
        del rect["x0"]
        rect[HostileKey("x0")] = 0
        with self.assertRaises(ValueError):
            rasterize_occupancy(nested)

    def test_hostile_schema_subclass_value_rejected_before_comparison(self):
        class HostileValue(str):
            def __eq__(self, other):
                raise AssertionError("hostile value comparison must not execute")

        item = payload()
        item["schema"] = HostileValue(SCHEMA)
        with self.assertRaises(ValueError):
            rasterize_occupancy(item)

    def test_hostile_string_keys_rejected_before_hash(self):
        class HostileKey(str):
            __hash__ = str.__hash__
            explode = False
            def __hash__(self):
                if self.explode:
                    raise RuntimeError("hostile hash executed")
                return str.__hash__(self)

        value = payload()
        key = HostileKey("schema")
        value[key] = value.pop("schema")
        key.explode = True
        with self.assertRaises(ValueError):
            rasterize_occupancy(value)

    def test_oversized_rectangle_rejected_before_key_set(self):
        room = dict(x0=0, y0=0, x1=10, y1=10)
        room.update({f"extra-{i}": i for i in range(10_000)})
        with self.assertRaises(ValueError):
            rasterize_occupancy(payload(room=room))

    def test_obstacle_cell_marking_budget_enforced(self):
        obstacle = dict(x0=0, y0=0, x1=256, y1=256)
        with self.assertRaisesRegex(ValueError, "marking budget"):
            rasterize_occupancy(payload(
                room=dict(x0=0, y0=0, x1=256, y1=256),
                cell_mm=1,
                obstacles=[dict(obstacle) for _ in range(16)],
            ))

    def test_oversized_schema_key_rejected_before_set(self):
        room = dict(x0=0, y0=0, x1=10, y1=10)
        room["x" * 1_000_000] = room.pop("x0")
        native_set = set

        def guarded_set(values):
            values = tuple(values)
            if any(type(value) is str and len(value) > 32 for value in values):
                raise AssertionError("oversized schema key reached set hashing")
            return native_set(values)

        with patch("mojum.room_occupancy_grid.set", side_effect=guarded_set, create=True):
            with self.assertRaisesRegex(ValueError, "bounded native string keys"):
                rasterize_occupancy(payload(room=room))


if __name__ == "__main__":
    unittest.main()
