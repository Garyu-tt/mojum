"""Contract tests for the offline occupancy rasterizer."""
import unittest
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


if __name__ == "__main__":
    unittest.main()
