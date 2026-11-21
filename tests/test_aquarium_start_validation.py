"""Malformed start coordinates must fail before creating a PRIVATE journal."""
import json
import math
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.world_room import Room


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

    def test_valid_start_in_large_room_is_not_movement(self):
        seed = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
        graph = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
        room = Room(2000.0, 2000.0, ())
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "valid-large-room.sqlite3"
            with Aquarium(db, seed, graph, room=room, start=(1500.0, 1.0)) as a:
                checkpoint = a.checkpoint()
                self.assertEqual((a.world.x, a.world.y), (1500.0, 1.0))
            with Aquarium(db, seed, graph, room=room, start=(1500.0, 1.0)) as a:
                self.assertEqual(a.checkpoint(), checkpoint)


if __name__ == "__main__":
    unittest.main()
