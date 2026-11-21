"""Malformed start coordinates must fail before creating a PRIVATE journal."""
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError


ROOT = Path(__file__).resolve().parents[1]


class AquariumStartValidationTests(unittest.TestCase):
    def test_invalid_start_fails_before_storage_open(self):
        seed = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
        graph = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
        bad_starts = (
            None, 42, "", "12", {}, [1], [1, 2, 3],
            (True, 1), (False, 1), ("1", 2), (None, 2),
            (float("nan"), 2), (float("inf"), 2),
            (float("-inf"), 2), (1001, 2), (-(10**400), 2),
        )
        with patch("mojum.aquarium.LocalNoosphere") as store:
            for start in bad_starts:
                with self.subTest(start=repr(start)):
                    with self.assertRaises(AquariumError):
                        Aquarium(":memory:", seed, graph, start=start)
            store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
