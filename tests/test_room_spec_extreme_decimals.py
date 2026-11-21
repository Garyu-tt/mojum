"""Extreme JSON decimal tokens must fail closed, not crash the room CLI."""

import unittest

from mojum.room_spec import validate_room_bytes


class ExtremeDecimalInputTests(unittest.TestCase):
    def test_extreme_finite_exponents_rejected_before_geometry_math(self):
        cases = (
            b'{"width":1e999999999,"height":4,"obstacles":[],"start":[1,2]}',
            b'{"width":4,"height":1e999999999,"obstacles":[],"start":[1,2]}',
            b'{"width":4,"height":4,"obstacles":[[0,0,1e999999999,1]],"start":[1,2]}',
            b'{"width":4,"height":4,"obstacles":[],"start":[1e-999999999,2]}',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                report = validate_room_bytes(raw)
                self.assertFalse(report.valid)
                self.assertTrue(
                    any("supported decimal magnitude" in error for error in report.errors),
                    report.errors,
                )

    def test_extremely_long_integer_rejected_without_arithmetic_overflow(self):
        value = b"9" * 11000
        raw = b'{"width":' + value + b',"height":4,"obstacles":[],"start":[1,2]}'
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIn("supported decimal magnitude", report.errors[0])


if __name__ == "__main__":
    unittest.main()
