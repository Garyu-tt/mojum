"""Decimal room preflight must agree with the native point-room fixture."""
import unittest

from mojum.room_spec import validate_room_bytes
from mojum.world_room import Room, initial_state


class RuntimeGeometryParityTests(unittest.TestCase):
    def test_decimal_exact_fit_can_exceed_runtime_float_wall(self):
        # Exact decimals: 0.1 + 0.2 == 0.3. Native floats disagree.
        raw = (
            b'{"width":0.3,"height":1,"obstacles":[[0.1,0.1,0.2,0.2]],'
            b'"start":[0,0]}'
        )
        with self.assertRaisesRegex(ValueError, "obstacle outside room"):
            Room(0.3, 1, ((0.1, 0.1, 0.2, 0.2),))
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIsNone(report.summary)
        self.assertIn("runtime numeric conversion", report.errors[0])

    def test_decimal_gap_disappears_after_float_rounding(self):
        # Decimal start.x < obstacle.x, but both become 0.1 at runtime.
        raw = (
            b'{"width":1,"height":1,'
            b'"obstacles":[[0.10000000000000001,0.1,0.2,0.2]],'
            b'"start":[0.1,0.2]}'
        )
        room = Room(1, 1, ((0.10000000000000001, 0.1, 0.2, 0.2),))
        with self.assertRaisesRegex(ValueError, "invalid initial position"):
            initial_state(room, 0.1, 0.2)
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIsNone(report.summary)
        self.assertIn("runtime numeric conversion", report.errors[0])

    def test_valid_decimal_spellings_remain_unmodified_in_report(self):
        raw = (
            b'{"width":4e0,"height":4,"obstacles":'
            b'[[2,1,0.10000000000000001,2]],"start":[1e0,2.00]}'
        )
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["width"], "4e0")
        self.assertEqual(
            report.summary["obstacles"][0][2], "0.10000000000000001"
        )
        self.assertEqual(report.summary["start"], ["1e0", "2.00"])

    def test_native_integer_room_is_not_silently_rounded_to_float(self):
        # JSON integer geometry uses Python int; only Aquarium's start is float.
        width = 9007199254740993
        raw = (
            '{"width":%d,"height":1,"obstacles":'
            '[[9007199254740992,0,1,1]],"start":[0,0]}' % width
        ).encode("ascii")
        room = Room(width, 1, ((9007199254740992, 0, 1, 1),))
        initial_state(room, 0.0, 0.0)
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)


if __name__ == "__main__":
    unittest.main()
