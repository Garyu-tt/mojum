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
        # Python equality treats True == 1 and 0.0 == 0 as equal.
        # Explicitly forbid type-coerced epoch/local_tick in persisted /4.
        for epoch, local_tick in ((True, 0), (1.0, 0), (1, False), (1, 0.0)):
            with self.subTest(epoch=epoch, local_tick=local_tick):
                with self.assertRaises(DevelopmentError):
                    EpochProprioception(9999, epoch, local_tick, REV, 1, 2, False)
        with self.assertRaises(DevelopmentError):
            EpochProprioception.at(5, "x", 1, 2, False)
        for epoch, local in ((True, 0), (1.0, 0), (1, False), (1, 0.0)):
            with self.subTest(epoch=epoch, local_tick=local):
                with self.assertRaises(DevelopmentError):
                    EpochProprioception(9999, epoch, local, REV, 1, 2, False)

    def test_exported_timestamp_stays_json_safe(self):
        # The public nanosecond integer crosses the 53-bit JSON boundary
        # far earlier than the absolute tick itself.
        self.assertLessEqual(observed_at_ns(MAX_ABSOLUTE_TICK), 2**53 - 1)
        self.assertEqual(float(observed_at_ns(MAX_ABSOLUTE_TICK)),
                         observed_at_ns(MAX_ABSOLUTE_TICK))
        with self.assertRaises(DevelopmentError):
            observed_at_ns(MAX_ABSOLUTE_TICK + 1)

    def test_epoch_and_local_tick_reject_boolean_aliases(self):
        with self.assertRaises(DevelopmentError):
            EpochProprioception(9999, True, 0, REV, 1, 2, False)
        with self.assertRaises(DevelopmentError):
            EpochProprioception(1, 0, True, REV, 1, 2, False)

    def test_public_schema_distinct(self):
        public = EpochProprioception.at(10000, REV, 1, 2, True).public()
        self.assertTrue(public["schema"].endswith("/4"))
        self.assertEqual(public["epoch"], 1)


if __name__ == "__main__":
    unittest.main()
