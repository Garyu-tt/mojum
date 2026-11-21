"""Regression: invalid synthetic room starts never create a private journal."""

import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class RoomStartPreflightTests(unittest.TestCase):
    def test_invalid_start_fails_before_creating_sqlite(self):
        invalid = (
            (float("nan"), 2.0),
            (1.0, float("inf")),
            (float("-inf"), 2.0),
            (True, 2.0),
            ("1", 2.0),
            (10**1000, 2.0),
            (999.0, 2.0),
            (1.0,),
            (1.0, 2.0, 3.0),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, start in enumerate(invalid):
                db = Path(directory) / f"invalid-{index}.sqlite3"
                with self.subTest(start=str(start)[:80]):
                    with self.assertRaises(AquariumError):
                        with Aquarium(db, SEED, GRAPH, start=start):
                            self.fail("invalid start unexpectedly accepted")
                    self.assertFalse(db.exists(), "rejected start created a journal")

    def test_valid_start_reopens_with_identical_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "valid.sqlite3"
            with Aquarium(db, SEED, GRAPH, start=(1.0, 2.0)) as first:
                first.step(dx=0.01)
                expected = first.checkpoint()
                self.assertEqual(first.store.count(), 2)
            with Aquarium(db, SEED, GRAPH, start=(1.0, 2.0)) as second:
                self.assertEqual(second.checkpoint(), expected)
                self.assertEqual(second.store.count(), 2)


if __name__ == "__main__":
    unittest.main()
