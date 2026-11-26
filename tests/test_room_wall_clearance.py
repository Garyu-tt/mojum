"""Focused stdlib tests for offline wall clearance."""
import unittest

from mojum.room_wall_clearance import audit_wall_clearance


class WallClearanceTests(unittest.TestCase):
    def test_geometry_and_order(self):
        report = audit_wall_clearance((0, 0, 100, 80),
                                      [("z", 100, 40), ("a", 25, 30)])
        self.assertEqual([p["id"] for p in report["points"]], ["a", "z"])
        self.assertEqual(report["points"][0]["walls_mm"],
                         {"left": 25, "right": 75, "bottom": 30, "top": 50})
        self.assertEqual(report["points"][0]["nearest_mm"], 25)
        self.assertEqual(report["points"][1]["nearest_mm"], 0)
        self.assertEqual(len(report["sha256"]), 64)

    def test_deterministic_and_detached(self):
        points = [("b", 3, 4), ("a", 1, 2)]
        first = audit_wall_clearance((0, 0, 10, 10), points)
        second = audit_wall_clearance((0, 0, 10, 10), list(reversed(points)))
        self.assertEqual(first, second)
        first["points"][0]["walls_mm"]["left"] = 999
        self.assertEqual(second["points"][0]["walls_mm"]["left"], 1)

    def test_invalid_inputs(self):
        invalid = [
            ((0, 0, 0, 10), []),
            ((0, 0, 10, 10), [("a", True, 1)]),
            ((0, 0, 10, 10), [("a", 11, 1)]),
            ((0, 0, 10, 10), [("a", 1, 1), ("a", 2, 2)]),
            ((0, 0, 10, 10), [("bad id", 1, 1)]),
            ((0, 0, 10, 10), [("a", 1.0, 1)]),
            ((0, 0, 10, 10), [("a", 1, 1)] * 4097),
            ((0, 0, 10, 10), (("a", 1, 1),)),
        ]
        for room, points in invalid:
            with self.subTest(room=room, count=len(points)):
                with self.assertRaises(ValueError):
                    audit_wall_clearance(room, points)


if __name__ == "__main__":
    unittest.main()
