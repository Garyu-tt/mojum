"""Hermetic synthetic-only seam admission regressions."""
import unittest

from mojum.cloth_seams import MAX_PAIRS, audit_seams


class ClothSeamTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(audit_seams(0, []).groups, ())

    def test_groups_are_sorted_and_deterministic(self):
        expected = ((0, 1, 2), (5, 6))
        self.assertEqual(audit_seams(8, [(2, 1), (6, 5), (0, 1)]).groups, expected)
        self.assertEqual(audit_seams(8, [(5, 6), (1, 0), (1, 2)]).groups, expected)

    def test_cycle_is_accepted(self):
        self.assertEqual(audit_seams(3, [(0, 1), (1, 2), (2, 0)]).group_count, 1)

    def test_reject_duplicate_reverse(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit_seams(2, [(0, 1), (1, 0)])

    def test_reject_self_link(self):
        with self.assertRaisesRegex(ValueError, "self"):
            audit_seams(2, [(1, 1)])

    def test_reject_out_of_range_and_bool(self):
        for pairs in ([(0, 2)], [(-1, 0)], [(True, 1)], [(0, 1.0)]):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                audit_seams(2, pairs)

    def test_reject_bad_shape(self):
        for pairs in ([(0,)], [(0, 1, 2)], ["01"], [None]):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                audit_seams(3, pairs)

    def test_reject_unbounded_input(self):
        with self.assertRaisesRegex(ValueError, "too many"):
            audit_seams(2, [(0, 1)] * (MAX_PAIRS + 1))

    def test_reject_invalid_count(self):
        for count in (-1, True, 100_001):
            with self.subTest(count=count), self.assertRaises(ValueError):
                audit_seams(count, [])


if __name__ == "__main__":
    unittest.main()
