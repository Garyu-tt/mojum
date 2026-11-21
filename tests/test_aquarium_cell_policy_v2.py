"""Version-pinned Aquarium local-cell v2: real PRIVATE SQLite compatibility QA.

No modifications to a legacy database, no neural/sentience claims, no
physical actuator, network or privileged policy inputs.
"""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.cell_policy_v2 import (
    CellExplorer, LocalCellError, SCHEMA as CELL_SCHEMA,
    LEARNING_SCHEMA,
)
from mojum.developmental_policy import (
    Explorer, Proprioception, SCHEMA as LEGACY_SCHEMA,
)
from mojum.neutral_probe_trial import trial
from mojum.world_room import Room, advance, initial_state


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class CellPolicyUnitTests(unittest.TestCase):
    def test_cell_policy_matches_preexisting_research_arm_exactly(self):
        room = Room(0.16, 0.16)
        pulses = tuple(i % 2 == 0 for i in range(120))
        reference = trial(
            room=room, start=(0.16, 0.16),
            pulses=pulses, mode="local_cell",
        )
        learner = CellExplorer()
        state = initial_state(room, 0.16, 0.16)
        for tick, pulse in enumerate(pulses):
            cue = Proprioception(
                tick, state.revision, state.x, state.y, pulse,
            )
            intended = learner.decide(cue)
            saved = reference["trace"][tick]
            self.assertEqual(saved["action"], intended["action"])
            self.assertEqual(saved["requested_delta_xy"],
                             [intended["dx"], intended["dy"]])
            next_state, measured = advance(
                room, state, tick=tick + 1, dx=intended["dx"],
                dy=intended["dy"], expected_revision=state.revision,
            )
            self.assertEqual(saved["blocked"], measured["blocked"])
            self.assertEqual(saved["position_xy"], list(measured["position"]))
            result = learner.observe(cue, intended, measured["blocked"])
            self.assertEqual(result["schema"], LEARNING_SCHEMA)
            self.assertEqual(result["total_updates"], tick + 1)
            state = next_state
        self.assertEqual(learner.updates, 120)
        self.assertEqual(
            CellExplorer.from_snapshot(learner.snapshot()).snapshot(),
            learner.snapshot(),
        )

    def test_snapshot_hard_validation_and_replayed_causal_revision(self):
        model = CellExplorer()
        snapshot = model.snapshot()
        self.assertEqual(CellExplorer.from_snapshot(snapshot).snapshot(),
                         snapshot)
        bad = []
        for key, value in (
            ("schema", LEGACY_SCHEMA),
            ("cell_size_m", 0.5),
            ("cell_size_m", True),
            ("last_tick", 1),
            ("updates", True),
            ("records", {"00:0": {"x+": [1, 1]}}),
            ("records", {"25001:0": {"x+": [1, 1]}}),
            ("records", {"0:0": {"x+": [1, 1]}}),
            ("records", {"0:0": {"x+": [0, 0]}}),
            ("records", {"0:0": {"x+": [1, 2]}}),
        ):
            changed = deepcopy(snapshot)
            changed[key] = value
            bad.append(changed)
        bad.append(dict(snapshot, unexpected="private"))
        for obj in bad:
            with self.subTest(obj=obj):
                with self.assertRaises(LocalCellError):
                    CellExplorer.from_snapshot(obj)
        cue = Proprioception(0, "0" * 64, .16, .16, True)
        intent = model.decide(cue)
        with self.assertRaises(LocalCellError):
            model.observe(cue, intent, "False")
        with self.assertRaises(LocalCellError):
            model.observe(cue, dict(intent, dx=0.0), True)
        self.assertEqual(model.snapshot(), snapshot)
        model.observe(cue, intent, True)
        with self.assertRaises(LocalCellError):
            model.decide(cue)
        with self.assertRaises(LocalCellError):
            model.observe(cue, intent, False)


class VersionedAquariumSQLiteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "PRIVATE-dante.sqlite3"
        self.room = Room(0.16, 0.16)
        self.start = (0.16, 0.16)

    def open(self, policy="legacy"):
        return Aquarium(
            self.db, SEED, GRAPH, room=self.room, start=self.start,
            exploration_mode="goal_free", sensory_mode="none",
            developmental_policy=policy,
        )

    def test_optin_v2_sqlite_roundtrip_and_stale_policy_refusal(self):
        with self.open("local_cell_v2") as aquarium:
            self.assertEqual(aquarium.manifest["developmental_schema"],
                             CELL_SCHEMA)
            self.assertEqual(aquarium.manifest["developmental_policy"],
                             "local_cell_v2")
            for _ in range(120):
                frame = aquarium.step()
                self.assertEqual(frame["developmental"]["schema"],
                                 CELL_SCHEMA)
                self.assertEqual(
                    frame["developmental"]["learning"]["schema"],
                    LEARNING_SCHEMA,
                )
                self.assertEqual(frame["brain"]["status"], "UNIMPLEMENTED")
            checkpoint = aquarium.checkpoint()
            original_head = checkpoint["journal_head"]
            self.assertEqual(aquarium.tick, 120)
            self.assertEqual(aquarium.store.count(), 121)
            self.assertEqual(checkpoint["developmental_state"]["schema"],
                             CELL_SCHEMA)
            self.assertEqual(aquarium.store.verify_chain()["head"],
                             original_head)
        with self.assertRaisesRegex(AquariumError, "seed, room, graph or clock"):
            with self.open("legacy"):
                pass
        with self.open("local_cell_v2") as reopened:
            self.assertEqual(reopened.tick, 120)
            self.assertEqual(reopened.checkpoint(), checkpoint)
            self.assertEqual(reopened.store.count(), 121)
            reopened.step()
            self.assertEqual(reopened.tick, 121)
        with self.open("local_cell_v2") as final:
            self.assertEqual(final.tick, 121)
            self.assertNotEqual(final.checkpoint()["journal_head"],
                                original_head)

    def test_legacy_manifest_remains_old_exact_contract(self):
        with self.open() as old:
            self.assertEqual(old.manifest["developmental_schema"], LEGACY_SCHEMA)
            self.assertNotIn("developmental_policy", old.manifest)
            self.assertIsInstance(old.explorer, Explorer)
            for _ in range(24):
                frame = old.step()
                self.assertEqual(frame["developmental"]["schema"],
                                 LEGACY_SCHEMA)
            snapshot = old.checkpoint()
            head = snapshot["journal_head"]
            events = old.store.count()
        with self.open("legacy") as same:
            self.assertEqual(snapshot, same.checkpoint())
            self.assertEqual(events, same.store.count())
        with self.assertRaisesRegex(AquariumError, "seed, room, graph or clock"):
            with self.open("local_cell_v2"):
                pass
        with self.open("legacy") as same_again:
            self.assertEqual(same_again.store.count(), events)
            self.assertEqual(same_again.checkpoint()["journal_head"], head)

    def test_invalid_policy_and_outside_intent_fail_before_persist(self):
        for bad in ("local_cell_V1", "unknown", True, None, 5):
            with self.subTest(bad=bad):
                with self.assertRaises(AquariumError):
                    Aquarium(
                        self.db, SEED, GRAPH,
                        exploration_mode="goal_free",
                        developmental_policy=bad,
                    )
                self.assertFalse(self.db.exists())
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH,
                     exploration_mode="scripted",
                     developmental_policy="local_cell_v2")
        self.assertFalse(self.db.exists())
        with self.open("local_cell_v2") as aquarium:
            initial = aquarium.checkpoint()
            with self.assertRaisesRegex(AquariumError,
                                        "externally scripted movement"):
                aquarium.step(dx=0.04)
            self.assertEqual(aquarium.checkpoint(), initial)

    def test_command_line_rejects_policy_mismatch_without_db(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        run = subprocess.run([
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.db), "--developmental-policy", "local_cell_v2",
            "--exploration-mode", "scripted",
        ], cwd=ROOT, env=env, text=True, capture_output=True, timeout=15)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("requires --exploration-mode goal_free", run.stderr)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
