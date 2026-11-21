"""Independent QA for malformed association snapshot cue keys (R0.9).

Keys originate from untrusted snapshots, not a trusted near-field sensor.
Huge decimal fields must fail under the public CuriosityError contract on
Python 3.11–3.13, regardless of CPython's integer conversion digit limit.
"""

import copy
import unittest

from mojum.sensory_curiosity_lab import ContactAssociations, CuriosityError, MODEL_SCHEMA


def snapshot_with(key):
    return {
        "schema": MODEL_SCHEMA,
        "table": {key: {"count": 1, "mean_deflection": 0.5}},
        "updates": 1,
    }


class BoundedCueSnapshotTests(unittest.TestCase):
    def test_oversized_decimal_fields_and_delimiters_fail_closed(self):
        # Prior to the guard, int("9" * 5000) raised a raw ValueError in
        # CPython 3.11–3.13 instead of a CuriosityError.
        invalid_keys = (
            "9" * 5000 + ":0:0",
            "0:" + "9" * 5000 + ":0",
            "0:0:" + "9" * 5000,
            ":" * 5000,
            "0:0:0" + "x" * 5000,
            "255:255:255 ",
        )
        for key in invalid_keys:
            with self.subTest(length=len(key), prefix=key[:14]):
                original = snapshot_with(key)
                before = copy.deepcopy(original)
                with self.assertRaisesRegex(CuriosityError, "invalid numeric cue"):
                    ContactAssociations.from_snapshot(original)
                self.assertEqual(original, before)

    def test_noncanonical_and_nonstring_keys_are_rejected(self):
        for key in ("01:2:3", "256:0:0", "-1:0:0", "1:2", "0:0:0:0",
                    "１:2:3", 123):
            with self.subTest(key=key):
                with self.assertRaisesRegex(CuriosityError, "invalid numeric cue"):
                    ContactAssociations.from_snapshot(snapshot_with(key))

    def test_canonical_rgb_limits_and_round_trip_remain_valid(self):
        for key in ("0:0:0", "220:70:30", "255:255:255"):
            with self.subTest(key=key):
                state = snapshot_with(key)
                model = ContactAssociations.from_snapshot(state)
                self.assertEqual(model.snapshot(), state)


if __name__ == "__main__":
    unittest.main()
