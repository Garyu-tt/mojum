"""Regression: coarse cell IDs must not alias small negative positions into cell 0."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mojum.infant_play import InfantError, _cell  # noqa: E402


def _at(x, y):
    return SimpleNamespace(x=x, y=y)


class CellBoundsTests(unittest.TestCase):
    def test_small_negative_coordinates_fail_closed(self):
        for x, y in ((-0.3, 1.0), (1.0, -0.3), (-0.49, 0.2), (-1e-12, 0.0), (-0.5, 1.0)):
            with self.subTest(x=x, y=y), self.assertRaises(InfantError):
                _cell(_at(x, y))

    def test_valid_positions_keep_existing_cell_ids(self):
        cases = {(0.0, 0.0): "0:0", (-0.0, 0.0): "0:0", (0.49, 0.2): "0:0",
                 (0.5, 1.0): "1:2", (0.6, 0.6): "1:1", (1.0, 2.0): "2:4",
                 (4999.9, 1.0): "9999:2"}
        for (x, y), expected in cases.items():
            with self.subTest(x=x, y=y):
                self.assertEqual(_cell(_at(x, y)), expected)

    def test_upper_budget_and_non_finite_still_rejected(self):
        for x, y in ((5000.0, 1.0), (float("inf"), 1.0), (float("nan"), 1.0), (True, 1.0)):
            with self.subTest(x=x, y=y), self.assertRaises(InfantError):
                _cell(_at(x, y))


if __name__ == "__main__":
    unittest.main()
