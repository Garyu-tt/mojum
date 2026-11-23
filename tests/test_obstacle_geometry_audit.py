import unittest

from mojum.obstacle_geometry_audit import audit_obstacles


def rect(name, x0, y0, x1, y1):
    return {"id": name, "x_min": x0, "y_min": y0, "x_max": x1, "y_max": y1}


class HostileKey(str):
    def __hash__(self):
        return hash("id")

    def __eq__(self, other):
        raise RuntimeError("hostile equality executed")


class ObstacleGeometryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(audit_obstacles([])["overlaps"], [])

    def test_metrics_and_overlap(self):
        result = audit_obstacles([rect("b", 2, 2, 5, 5), rect("a", 0, 0, 3, 3)])
        self.assertEqual(result["metrics"][0]["area_mm2"], 9)
        self.assertEqual(result["metrics"][0]["perimeter_mm"], 12)
        self.assertEqual(result["overlaps"], [{"a": "a", "b": "b", "area_mm2": 1}])

    def test_order_independent(self):
        items = [rect("b", 2, 2, 5, 5), rect("a", 0, 0, 3, 3)]
        self.assertEqual(audit_obstacles(items), audit_obstacles(list(reversed(items))))

    def test_touch_is_not_overlap(self):
        self.assertEqual(audit_obstacles([rect("a", 0, 0, 1, 1), rect("b", 1, 0, 2, 1)])["overlaps"], [])

    def test_reject_hostile_string_subclass_key_before_comparison(self):
        item = {
            HostileKey("id"): "a",
            "x_min": 0,
            "y_min": 0,
            "x_max": 1,
            "y_max": 1,
        }
        with self.assertRaisesRegex(ValueError, "exact native strings"):
            audit_obstacles([item])

    def test_reject_oversized_native_field_name_before_set(self):
        item = rect("a", 0, 0, 1, 1)
        item["x" * 100_000] = None
        with self.assertRaisesRegex(ValueError, "field names exceed"):
            audit_obstacles([item])

    def test_reject_oversized_record_cardinality(self):
        item = rect("a", 0, 0, 1, 1)
        item.update({f"extra-{index}": index for index in range(10_000)})
        with self.assertRaisesRegex(ValueError, "field names exceed|invalid rectangle fields"):
            audit_obstacles([item])

    def test_invalid(self):
        invalid = [
            "oops", [1], [rect("a", 0, 0, 0, 1)],
            [rect("a", True, 0, 1, 1)],
            [rect("a", 0, 0, 1, 1)] * 2,
            [rect("a", 0, 0, 1_000_001, 1)],
            [rect("bad id", 0, 0, 1, 1)],
            [rect("a", 0, 0, 1, 1), rect("a", 2, 2, 3, 3)],
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                audit_obstacles(item)

    def test_no_mutation(self):
        items = [rect("a", 0, 0, 1, 1)]
        audit_obstacles(items)
        self.assertEqual(items, [rect("a", 0, 0, 1, 1)])


if __name__ == "__main__":
    unittest.main()
