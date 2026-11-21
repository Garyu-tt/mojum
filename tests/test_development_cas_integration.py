"""Acceptance regression: journal CAS for new development and naming events."""
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
IDENTITY = {"schema": "mojum.agent.identity/1", "agent_id": SEED["genome_id"],
            "display_name": "Dante", "assigned_by": "human"}


class DevelopmentCasIntegrationTests(unittest.TestCase):
    def test_development_commit_rejects_stale_writer_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "fixture.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as writer, \
                 Aquarium(db, SEED, GRAPH, neural_mode="disabled") as stale:
                writer.advance_development(1)
                checkpoint = writer.checkpoint()
                with self.assertRaises(NoosphereError):
                    stale.assign_human_name(IDENTITY, actor_id="human-owner")
                with self.assertRaises(AquariumError):
                    stale.checkpoint()
                self.assertIsNone(stale.identity)
                self.assertEqual(stale.development_months, 72)
                writer.step(dx=0, dy=0)
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as recovered:
                self.assertEqual(recovered.development_months, 73)
                self.assertEqual(recovered.tick, 1)
                self.assertNotEqual(recovered.checkpoint()["journal_head"], checkpoint["journal_head"])

    def test_dev_and_name_interleave_with_developmental_motor_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "fixture.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="shadow",
                          motor_mode="developmental_fixture") as writer:
                writer.step()
                writer.advance_development(3, reason="optional lesson")
                writer.assign_human_name(IDENTITY, actor_id="human-owner")
                writer.step()
                expected = writer.checkpoint()
                self.assertEqual(expected["development_months"], 75)
                self.assertEqual(expected["motor_state"]["observations"], 2)
            with Aquarium(db, SEED, GRAPH, neural_mode="shadow",
                          motor_mode="developmental_fixture") as reopened:
                self.assertEqual(reopened.checkpoint(), expected)
                reopened.step()
                self.assertEqual(reopened.motor.observations, 3)

    def test_name_commit_rejects_stale_development_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "fixture.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as writer, \
                 Aquarium(db, SEED, GRAPH, neural_mode="disabled") as stale:
                writer.assign_human_name(IDENTITY, actor_id="human-owner")
                self.assertEqual(writer.checkpoint()["display_name"], "Dante")
                with self.assertRaises(NoosphereError):
                    stale.advance_development(1)
                self.assertEqual(stale.development_months, 72)
                writer.step(dx=0, dy=0)
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as recovered:
                self.assertEqual(recovered.identity["display_name"], "Dante")
                self.assertEqual(recovered.tick, 1)


if __name__ == "__main__":
    unittest.main()
