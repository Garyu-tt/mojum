"""Fail-closed acquisition tick type regression on real SQLite aquarium."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.genesis import ACQUISITION_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text())
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text())


class AcquisitionTickTypeTests(unittest.TestCase):
    def test_bad_tick_types_do_not_append_or_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.sqlite3"
            with Aquarium(path, SEED, GRAPH) as aquarium:
                for index, bad_tick in enumerate((None, True, 0.0, "0", [], {})):
                    event = {
                        "schema": ACQUISITION_SCHEMA,
                        "event_id": f"bad-tick-{index}",
                        "genome_id": SEED["genome_id"],
                        "gene": "heartbeat_bpm",
                        "delta": 1,
                        "valid_from_tick": bad_tick,
                        "observed_at_ns": 0,
                        "recorded_at_ns": 0,
                        "source": "synthetic-unit-test",
                        "evidence": "invalid tick must not commit",
                    }
                    before = copy.deepcopy(aquarium.checkpoint())
                    with self.subTest(bad_tick=repr(bad_tick)):
                        with self.assertRaises(AquariumError):
                            aquarium.acquire(event)
                        self.assertEqual(aquarium.checkpoint(), before)
                        self.assertEqual(aquarium.store.count(), 1)
            with Aquarium(path, SEED, GRAPH) as reopened:
                self.assertEqual(reopened.store.count(), 1)
                self.assertEqual(reopened.tick, 0)


if __name__ == "__main__":
    unittest.main()
