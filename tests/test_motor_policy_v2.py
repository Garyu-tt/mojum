"""R0.14 opt-in local-cell v2: exact old-journal compatibility and measured QA."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_trace_export import export_private_trace
from mojum.developmental_policy import (
    DevelopmentError, Explorer, Proprioception, SCHEMA as LEGACY_SCHEMA,
)
from mojum.developmental_policy_v2 import (
    LocalCellExplorer, MODE, SCHEMA as V2_SCHEMA,
)
from mojum.noosphere import NoosphereError
from mojum.world_room import Room, advance, initial_state

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class VersionedMotorPolicyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "organism.sqlite3"
        self.other = Path(tmp.name) / "control.sqlite3"
        self.out = Path(tmp.name) / "trace.private.jsonl"

    def aquarium(self, db=None, **kwargs):
        return Aquarium(db or self.db, SEED, GRAPH,
                        exploration_mode="goal_free", **kwargs)

    def test_default_manifest_and_events_keep_legacy_v1_contract(self):
        with self.aquarium() as a:
            self.assertNotIn("motor_policy", a.manifest)
            self.assertNotIn("motor_policy_schema", a.manifest)
            self.assertEqual(a.manifest["developmental_schema"], LEGACY_SCHEMA)
            frame = a.step()
            self.assertEqual(frame["developmental"]["schema"], LEGACY_SCHEMA)
            self.assertEqual(frame["developmental"]["learning"]["schema"],
                             LEGACY_SCHEMA)
            self.assertEqual(a.checkpoint()["developmental_state"]["schema"],
                             LEGACY_SCHEMA)
            before = a.checkpoint()
        with self.aquarium(motor_policy="coarse_v1") as legacy:
            self.assertEqual(legacy.checkpoint(), before)
            self.assertEqual(legacy.manifest["developmental_schema"], LEGACY_SCHEMA)

    def test_v2_bootstrap_pins_distinct_policy_and_replays_exactly(self):
        with self.aquarium(motor_policy=MODE) as first, \
                self.aquarium(self.other, motor_policy=MODE) as twin:
            self.assertEqual(first.manifest["developmental_schema"], V2_SCHEMA)
            self.assertEqual(first.manifest["motor_policy"], MODE)
            self.assertEqual(first.manifest["motor_policy_schema"], V2_SCHEMA)
            for _ in range(35):
                self.assertEqual(first.step(), twin.step())
            checkpoint = first.checkpoint()
            expected_next = twin.step()
            self.assertEqual(checkpoint["developmental_state"]["schema"], V2_SCHEMA)
            self.assertEqual(first.store.count(), 36)
            self.assertEqual(first.store.dataset()["projection"]["items"], [])
            only_private = first.store.dataset(
                include_nonpublic=True)["projection"]["items"]
            self.assertTrue(all(entry["event"]["visibility"] == "PRIVATE"
                                for entry in only_private))
            self.assertEqual(only_private[1]["event"]["payload"]["developmental"]["schema"],
                             V2_SCHEMA)
        with self.aquarium(motor_policy=MODE) as resumed:
            self.assertEqual(resumed.checkpoint(), checkpoint)
            self.assertEqual(resumed.step(), expected_next)
            self.assertEqual(resumed.checkpoint()["tick"], 36)

    def test_v1_and_v2_journals_cannot_be_silently_reinterpreted(self):
        with self.aquarium() as legacy:
            legacy.step()
            legacy_head = legacy.checkpoint()["journal_head"]
            self.assertEqual(legacy.store.count(), 2)
        with self.assertRaisesRegex(AquariumError, "mismatch"):
            self.aquarium(motor_policy=MODE)
        with self.aquarium() as restored:
            self.assertEqual(restored.store.count(), 2)
            self.assertEqual(restored.checkpoint()["journal_head"], legacy_head)
        with self.aquarium(self.other, motor_policy=MODE) as modern:
            modern.step()
            modern_head = modern.checkpoint()["journal_head"]
        with self.assertRaisesRegex(AquariumError, "mismatch"):
            self.aquarium(self.other)
        with self.aquarium(self.other, motor_policy=MODE) as restored_v2:
            self.assertEqual(restored_v2.checkpoint()["journal_head"], modern_head)
            self.assertEqual(restored_v2.store.count(), 2)

    def test_disabled_scripted_and_bad_types_fail_before_database_birth(self):
        invalid = [
            {"motor_policy": "local_cell_v3", "exploration_mode": "goal_free"},
            {"motor_policy": [], "exploration_mode": "goal_free"},
            {"motor_policy": None, "exploration_mode": "goal_free"},
            {"motor_policy": MODE, "exploration_mode": "scripted"},
        ]
        for i, opts in enumerate(invalid):
            db = self.db.with_name(f"invalid-{i}.sqlite3")
            with self.subTest(opts=opts), self.assertRaises(AquariumError):
                Aquarium(db, SEED, GRAPH, **opts)
            self.assertFalse(db.exists())

    def test_new_v2_state_never_advances_on_failed_append(self):
        with self.aquarium(motor_policy=MODE, sensory_mode="toy_probe") as a:
            before = a.checkpoint()
            with patch.object(a, "_append_record", side_effect=NoosphereError("forced")):
                with self.assertRaises(NoosphereError):
                    a.step()
            self.assertEqual(a.checkpoint(), before)
            self.assertEqual(a.store.count(), 1)
            self.assertEqual(a.sensory_bridge.model.updates, 0)
            self.assertEqual(a.explorer.updates, 0)

    def test_v2_cas_stale_writer_fails_without_local_state_mutation(self):
        with self.aquarium(motor_policy=MODE) as first, \
                self.aquarium(motor_policy=MODE) as stale:
            before = stale.explorer.snapshot()
            first.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.tick, 0)
            self.assertEqual(stale.explorer.snapshot(), before)
            with self.assertRaisesRegex(AquariumError, "stale or unadopted"):
                stale.checkpoint()

    def test_v2_crash_after_commit_reconstructs_policy_in_replay(self):
        with self.aquarium(motor_policy=MODE) as a:
            payload, _, _ = a._calculate(None, None)
            a._append_record("aquarium.frame", "frame-000000000000",
                             payload, 0, a.genome.revision)
            self.assertEqual(a.explorer.updates, 0)
        with self.aquarium(motor_policy=MODE) as restored:
            self.assertEqual(restored.tick, 1)
            self.assertEqual(restored.explorer.updates, 1)
            self.assertEqual(restored.store.count(), 2)

    def test_v2_snapshot_fail_closed_and_keeps_legacy_snapshots_incompatible(self):
        versioned = LocalCellExplorer().snapshot()
        self.assertEqual(versioned["schema"], V2_SCHEMA)
        for bad in (
            {"schema": LEGACY_SCHEMA},
            {"schema": []},
            {"records": {"001:0": {"rest": [1, 0]}}, "last_tick": 0, "updates": 1},
            {"records": {"25001:0": {"rest": [1, 0]}},
             "last_tick": 0, "updates": 1},
            {"records": {"0:0": {"rest": [True, 0]}},
             "last_tick": 0, "updates": 1},
            {"records": {"0:0": {"rest": [1, 2]}},
             "last_tick": 0, "updates": 1},
            {"records": {"0:0": {"unknown": [1, 0]}},
             "last_tick": 0, "updates": 1},
            {"updates": 1},
        ):
            snap = copy.deepcopy(versioned)
            snap.update(bad)
            with self.subTest(snap=snap), self.assertRaises(DevelopmentError):
                LocalCellExplorer.from_snapshot(snap)
        with self.assertRaises(DevelopmentError):
            Explorer.from_snapshot(versioned)
        self.assertEqual(LocalCellExplorer.from_snapshot(versioned).snapshot(),
                         versioned)

    def test_v2_snapshot_rejects_unreachable_blocked_action_histories(self):
        # In a given cell, the first blocked action becomes inhibited.
        # The same action therefore cannot accumulate two blocked outcomes;
        # valid no-motion rest cannot be blocked even once.
        for action, blocked_count, total_count in (
            ("x+", 2, 2),
            ("y-", 2, 3),
            ("rest", 1, 1),
        ):
            with self.subTest(action=action, blocked=blocked_count):
                state = {
                    "schema": V2_SCHEMA,
                    "last_tick": total_count - 1,
                    "updates": total_count,
                    "records": {"2:2": {action: [total_count, blocked_count]}},
                }
                original = copy.deepcopy(state)
                with self.assertRaisesRegex(DevelopmentError, "outcome counters"):
                    LocalCellExplorer.from_snapshot(state)
                self.assertEqual(state, original)
        # Two successful measurements followed by one blocked attempt
        # are feasible and must not be rejected by the narrowed contract.
        feasible = {
            "schema": V2_SCHEMA, "last_tick": 2, "updates": 3,
            "records": {"2:2": {"x+": [3, 1]}},
        }
        self.assertEqual(LocalCellExplorer.from_snapshot(feasible).snapshot(),
                         feasible)

    def test_v2_rejects_forged_blocked_rest_before_mutating_ram(self):
        policy = LocalCellExplorer()
        cue = Proprioception(0, "0" * 64, .08, .08, False)
        intent = policy.decide(cue)
        self.assertEqual(intent["action"], "rest")
        before = policy.snapshot()
        with self.assertRaisesRegex(DevelopmentError, "rest cannot"):
            policy.observe(cue, intent, True)
        self.assertEqual(policy.snapshot(), before)
        self.assertFalse(policy.observe(cue, intent, False)["measured_blocked"])
        self.assertEqual(policy.updates, 1)

    def test_local_cell_control_avoids_legacy_rest_collapse_in_tiny_corner(self):
        # Independent, deterministic measured environment; no reward, geometry
        # oracle or future sensor state is given to either controller.
        room = Room(.16, .16)
        results = {}
        for kind in ("coarse_v1", MODE):
            policy = Explorer() if kind == "coarse_v1" else LocalCellExplorer()
            world = initial_state(room, .16, .16)
            rest = blocked = successful = 0
            for tick in range(120):
                cue = Proprioception(tick, world.revision, world.x, world.y,
                                    tick % 2 == 0)
                intent = policy.decide(cue)
                next_world, observed = advance(
                    room, world, tick=tick + 1, dx=intent["dx"], dy=intent["dy"],
                    expected_revision=world.revision,
                )
                policy.observe(cue, intent, observed["blocked"])
                rest += intent["action"] == "rest"
                blocked += observed["blocked"]
                successful += (intent["action"] != "rest" and
                               not observed["blocked"])
                world = next_world
            self.assertEqual(policy.updates, 120)
            results[kind] = (rest, blocked, successful)
        self.assertEqual(results["coarse_v1"], (110, 4, 6))
        self.assertEqual(results[MODE], (24, 3, 93))

    def test_v2_private_export_requires_matching_policy_and_replays(self):
        with self.aquarium(motor_policy=MODE) as a:
            for _ in range(3):
                a.step()
            expected = a.checkpoint()
        with self.assertRaisesRegex(AquariumError, "mismatch"):
            export_private_trace(self.db, self.out, SEED, GRAPH,
                                 allow_private_export=True,
                                 exploration_mode="goal_free")
        self.assertFalse(self.out.exists())
        result = export_private_trace(
            self.db, self.out, SEED, GRAPH,
            allow_private_export=True, exploration_mode="goal_free",
            motor_policy=MODE,
        )
        self.assertEqual(result["frames"], 3)
        self.assertEqual(result["journal_head"], expected["journal_head"])
        rows = [json.loads(l) for l in self.out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["developmental"]["schema"], V2_SCHEMA)
        self.assertEqual(rows[-1]["schema"], "mojum.aquarium.checkpoint/1")
        self.assertEqual(rows[-1]["trace_integrity"]["frame_count"], 3)

    def test_cli_opt_in_and_mismatch_are_explicit(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        def run(*args):
            return subprocess.run(
                [sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
                 "--ticks", "2", *args],
                cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=25,
            )
        # Bad mode must fail BEFORE creating a new organism.
        bad = run("--motor-policy", MODE)
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse(self.db.exists())
        good = run("--exploration-mode", "goal_free", "--motor-policy", MODE)
        self.assertEqual(good.returncode, 0, good.stderr)
        first = json.loads(good.stdout)
        self.assertEqual(first["tick"], 2)
        same = run("--exploration-mode", "goal_free", "--motor-policy", MODE)
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertEqual(json.loads(same.stdout)["tick"], 4)
        wrong = run("--exploration-mode", "goal_free")
        self.assertNotEqual(wrong.returncode, 0)
        with self.aquarium(motor_policy=MODE) as a:
            self.assertEqual(a.checkpoint()["tick"], 4)


if __name__ == "__main__":
    unittest.main()
