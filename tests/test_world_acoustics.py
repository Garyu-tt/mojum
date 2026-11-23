"""Tests for synthetic point-source free-field acoustics."""
import math
import unittest

from mojum.world_acoustics import sample_point_source


class TestWorldAcoustics(unittest.TestCase):
    def test_reference(self):
        r = sample_point_source((0, 0), (1, 0), 60)
        self.assertEqual(r.distance_m, 1)
        self.assertAlmostEqual(r.received_db, 60)

    def test_double_distance(self):
        r = sample_point_source((0, 0), (2, 0), 60)
        self.assertAlmostEqual(r.attenuation_db, 20 * math.log10(2))
        self.assertAlmostEqual(r.received_db, 53.9794000867)

    def test_coincident_points_are_finite(self):
        r = sample_point_source((0, 0), (0, 0), 40)
        self.assertTrue(math.isfinite(r.received_db))
        self.assertEqual(r.distance_m, 0)

    def test_symmetry(self):
        a = sample_point_source((1, 2), (4, 6), 70)
        b = sample_point_source((4, 6), (1, 2), 70)
        self.assertEqual(a, b)

    def test_oversized_integers_fail_closed_without_float_overflow(self):
        huge = 10**1000
        cases = [
            ((huge, 0), (0, 0), 40, {}),
            ((0, 0), (0, huge), 40, {}),
            ((0, 0), (1, 1), huge, {}),
            ((0, 0), (1, 1), 40, {"reference_distance_m": huge}),
            ((0, 0), (1, 1), 40, {"minimum_distance_m": huge}),
            ((0, 0), (1, 1), 40, {"maximum_distance_m": huge}),
        ]
        for source, listener, level, kwargs in cases:
            with self.subTest(kwargs=tuple(kwargs)):
                with self.assertRaises(ValueError):
                    sample_point_source(source, listener, level, **kwargs)

    def test_invalid_inputs(self):
        cases = [
            ((0, 0), (float("nan"), 0), 40, {}),
            ((0, 0), (1, 1), True, {}),
            ((0, 0), (1, 1), 40, {"minimum_distance_m": 0}),
            ((0, 0), (1, 1), 40, {"maximum_distance_m": 0.5}),
            ((0, 0), (1, 1), 40, {"reference_distance_m": float("inf")}),
            ((0, 0), (1, 1), 400, {}),
            ((0, 0), (1, 1), 40, {"reference_distance_m": 0.05}),
            ((0, 0), (1e9 + 1, 0), 40, {}),
        ]
        for source, listener, level, kwargs in cases:
            with self.subTest(case=(source, listener, level, kwargs)):
                with self.assertRaises(ValueError):
                    sample_point_source(source, listener, level, **kwargs)


    def test_rejects_hostile_point_container_subclasses_before_methods(self):
        class HostileList(list):
            def __len__(self):
                raise AssertionError("hostile __len__ must not execute")

        class HostileTuple(tuple):
            def __getitem__(self, index):
                raise AssertionError("hostile __getitem__ must not execute")

        for point in (HostileList([0, 0]), HostileTuple((0, 0))):
            with self.subTest(point_type=type(point).__name__):
                with self.assertRaises(ValueError):
                    sample_point_source(point, (1, 1), 40)



if __name__ == "__main__":
    unittest.main()
