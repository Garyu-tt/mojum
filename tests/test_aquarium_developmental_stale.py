"""Independent stale-writer regression for opt-in synthetic motor play."""

import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class StaleDevelopmentalWriterTests(unittest.TestCase):
    def test_stale_writer_cannot_adopt_uncommitted_motor_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "motor-private.sqlite3"

            def open_aquarium():
                return Aquarium(path, SEED, GRAPH, motor_mode="developmental_fixture")

            with open_aquarium() as writer, open_aquarium() as stale:
                initial = stale.checkpoint()
                writer.step()
                with self.assertRaisesRegex(NoosphereError, "stale event head"):
                    stale.step()
                self.assertEqual(stale.tick, 0)
                self.assertEqual(stale.world.revision, initial["world_revision"])
                self.assertEqual(stale.motor.snapshot(), initial["motor_state"])
                self.assertEqual(stale.learner.snapshot(), initial["neural_state"])
                with self.assertRaises(AquariumError):
                    stale.checkpoint()

            with open_aquarium() as resumed:
                self.assertEqual(resumed.tick, 1)
                self.assertEqual(resumed.motor.observations, 1)
                self.assertEqual(resumed.store.count(), 2)


if __name__ == "__main__":
    unittest.main()
