"""Independent QA of canonical goal-free mode public input/error boundaries.

Stacked on PR #59; no modifications to the owner branch or main.
"""
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.developmental_policy import DevelopmentError, Proprioception


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class CanonicalGoalFreeInputBoundaryQA(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "canonical.sqlite3"

    def test_legacy_scripted_mode_rejects_explicit_none_without_writing(self):
        with Aquarium(self.db, SEED, GRAPH) as original:
            before = original.checkpoint()
            for kwargs in ({"dx": None}, {"dy": None},
                           {"dx": None, "dy": None}):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaisesRegex(AquariumError, "movement"):
                        original.step(**kwargs)
                    self.assertEqual(original.checkpoint(), before)
                    self.assertEqual(original.store.count(), 1)
            legacy = original.step()
            self.assertEqual(legacy["movement_request"],
                             {"dx": 0.02, "dy": 0.0})
            self.assertEqual(original.store.count(), 2)
        with Aquarium(self.db, SEED, GRAPH) as restored:
            self.assertEqual(restored.tick, 1)

    def test_goal_free_refuses_explicit_none_as_external_command(self):
        with Aquarium(self.db, SEED, GRAPH,
                      exploration_mode="goal_free") as child:
            baseline = child.checkpoint()
            for kwargs in ({"dx": None}, {"dy": None},
                           {"dx": None, "dy": None},
                           {"dx": 0.02}, {"dy": 0.0}):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaisesRegex(AquariumError, "externally scripted"):
                        child.step(**kwargs)
                    self.assertEqual(child.checkpoint(), baseline)
                    self.assertEqual(child.store.count(), 1)
            frame = child.step()
            self.assertIn("developmental", frame)
            self.assertEqual(child.explorer.updates, 1)
        with Aquarium(self.db, SEED, GRAPH,
                      exploration_mode="goal_free") as restored:
            self.assertEqual(restored.tick, 1)
            self.assertEqual(restored.explorer.updates, 1)

    def test_huge_integer_proprioception_fails_as_typed_development_error(self):
        enormous = 10 ** 5000
        for field in ("x", "y"):
            for value in (enormous, -enormous):
                with self.subTest(field=field, positive=value > 0):
                    args = {"tick": 0, "revision": "a" * 64,
                            "x": 1.0, "y": 2.0, "pulse": True}
                    args[field] = value
                    with self.assertRaisesRegex(DevelopmentError, "numeric proprioception"):
                        Proprioception(**args)
        # Keep genuine finite bounded observations intact.
        cue = Proprioception(0, "a" * 64, 1.0, 2.0, False)
        self.assertEqual(cue.public()["position_xy"], [1.0, 2.0])

    def test_mode_switch_cannot_reinterpret_existing_journal(self):
        with Aquarium(self.db, SEED, GRAPH) as original:
            original.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free")


if __name__ == "__main__":
    unittest.main()
