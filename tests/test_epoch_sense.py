"""Regression coverage for the isolated epoch-aware proprioception schema."""

import unittest

from mojum.epoch_sense import (
    EPOCH_LENGTH,
    MAX_ABSOLUTE_TICK,
    EpochProprioception,
    observed_at_ns,
    split_tick,
)
from mojum.local_cell_sense import DevelopmentError

REV = "a" * 64


class EpochSenseTests(unittest.TestCase):
    def test_crosses_old_boundary_monotonically(self):
        prev = -1
        for tick in (9998, 9999, 10000, 10050):
            sense = EpochProprioception.at(tick, REV, 1, 2, False)
            instant = observed_at_ns(tick)
            self.assertGreater(instant, prev)
            prev = instant
            self.assertEqual(sense.epoch * EPOCH_LENGTH + sense.local_tick, tick)
        self.assertEqual(split_tick(9999), (1, 0))
        self.assertEqual(split_tick(10050), (1, 51))

    def test_inconsistent_or_invalid_rejected(self):
        for bad in (-1, True, 1.0, MAX_ABSOLUTE_TICK + 1):
            with self.subTest(bad=bad):
                with self.assertRaises(DevelopmentError):
                    split_tick(bad)
        with self.assertRaises(DevelopmentError):
            EpochProprioception(10000, 0, 10000, REV, 1, 2, False)
        with self.assertRaises(DevelopmentError):
            EpochProprioception.at(5, "x", 1, 2, False)

    def test_public_schema_distinct(self):
        public = EpochProprioception.at(10000, REV, 1, 2, True).public()
        self.assertTrue(public["schema"].endswith("/4"))
        self.assertEqual(public["epoch"], 1)


if __name__ == "__main__":
    unittest.main()
