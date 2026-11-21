"""Actual Aquarium/SQLite R0.14 tests, not an isolated Explorer toy loop.

All comparative metrics are evaluation-only diagnostics. No model is trained
on a desired position, benchmark reward, pain, or hidden wall geometry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_trace_export import export_private_trace
from mojum.developmental_policy import SCHEMA as V1_SCHEMA
from mojum.developmental_policy_v2 import MODE, SCHEMA as V2_SCHEMA
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
ROOM = Room(0.16, 0.16)
START = (0.16, 0.16)
TICKS = 120


def run_frames(database, *, policy, ticks, start=START, room=ROOM):
    with Aquarium(
        database, SEED, GRAPH, exploration_mode="goal_free",
        motor_policy=policy, room=room, start=start,
    ) as aquarium:
        frames = [aquarium.step() for _ in range(ticks)]
        checkpoint = aquarium.checkpoint()
        assert aquarium.store.count() == checkpoint["tick"] + 1
        assert all(f["physical_model"] == "NONE_FIXTURE_ONLY" for f in frames)
        return frames, checkpoint


def metrics(frames):
    longest = running = 0
    for f in frames:
        resting = f["developmental"]["intent"]["action"] == "rest"
        running = running + 1 if resting else 0
        longest = max(longest, running)
    rest = sum(f["developmental"]["intent"]["action"] == "rest" for f in frames)
    blocked = sum(bool(f["world"]["blocked"]) for f in frames)
    return {
        "rest": rest, "blocked": blocked,
        "successful": len(frames) - rest - blocked,
        "longest_rest": longest,
    }


class CanonicalLocalCellV2Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.coarse_db = self.root / "coarse.sqlite3"
        self.local_db = self.root / "local.sqlite3"
        self.twin_db = self.root / "twin.sqlite3"

    def test_real_sqlite_room_motor_learning_detects_v1_collapse_and_v2_motion(self):
        v1, before_v1 = run_frames(self.coarse_db, policy="coarse_v1", ticks=TICKS)
        v2, before_v2 = run_frames(self.local_db, policy=MODE, ticks=TICKS)
        self.assertEqual(before_v1["tick"], TICKS)
        self.assertEqual(before_v2["tick"], TICKS)
        self.assertNotEqual(before_v1["manifest_sha256"],
                            before_v2["manifest_sha256"])
        self.assertEqual(before_v1["developmental_state"]["schema"], V1_SCHEMA)
        self.assertEqual(before_v2["developmental_state"]["schema"], V2_SCHEMA)
        for i, (a, b) in enumerate(zip(v1, v2)):
            self.assertEqual(a["tick"], i)
            self.assertEqual(b["tick"], i)
            # Heartbeat belongs to the same virtual clock; no external pulse
            # script or privileged geometry is passed into either policy.
            self.assertEqual(a["interoception"], b["interoception"])
            self.assertEqual(a["developmental"]["observation"]["heartbeat_pulse"],
                             b["developmental"]["observation"]["heartbeat_pulse"])
            for frame, schema in ((a, V1_SCHEMA), (b, V2_SCHEMA)):
                dev = frame["developmental"]
                self.assertEqual(dev["schema"], schema)
                self.assertEqual(dev["learning"]["schema"], schema)
                self.assertEqual(dev["learning"]["measured_blocked"],
                                 frame["world"]["blocked"])
                self.assertEqual(dev["learning"]["total_updates"], i + 1)
                self.assertEqual(
                    set(dev["observation"]),
                    {"schema", "tick", "world_revision", "position_xy",
                     "heartbeat_pulse", "source"},
                )
                self.assertEqual(dev["motor_capability"],
                                 "SYNTHETIC_POINT_ROOM_ONLY")
                self.assertIsNone(frame["brain"]["action"])
        coarse, fine = metrics(v1), metrics(v2)
        # This is a regression on a known historical failure, not evidence of
        # general movement intelligence. Do not assert collision count alone.
        self.assertGreater(coarse["longest_rest"], 30)
        self.assertLess(fine["longest_rest"], 30)
        self.assertGreater(coarse["rest"], fine["rest"])
        self.assertGreater(fine["successful"], coarse["successful"])
        with Aquarium(self.local_db, SEED, GRAPH,
                      exploration_mode="goal_free", motor_policy=MODE,
                      room=ROOM, start=START) as reopened:
            self.assertEqual(reopened.checkpoint(), before_v2)
            self.assertEqual(reopened.store.count(), TICKS + 1)
            self.assertEqual(reopened.store.dataset()["projection"]["items"], [])

    def test_exact_v2_cold_resume_matches_uninterrupted_sqlite_history(self):
        first, cp60 = run_frames(self.local_db, policy=MODE, ticks=60)
        full, cp120 = run_frames(self.twin_db, policy=MODE, ticks=120)
        self.assertEqual(first, full[:60])
        self.assertEqual(cp60["tick"], 60)
        second, restored = run_frames(self.local_db, policy=MODE, ticks=60)
        self.assertEqual(second, full[60:])
        self.assertEqual(cp120, restored)
        self.assertEqual(restored["developmental_state"]["updates"], 120)
        self.assertEqual(restored["developmental_state"]["schema"], V2_SCHEMA)

    def test_wrong_policy_reopen_and_private_export_fail_without_history_mutation(self):
        run_frames(self.local_db, policy=MODE, ticks=10)
        with Aquarium(
            self.local_db, SEED, GRAPH, exploration_mode="goal_free",
            motor_policy=MODE, room=ROOM, start=START,
        ) as right:
            prior = right.checkpoint()
            self.assertEqual(right.store.count(), 11)
        with self.assertRaisesRegex(AquariumError, "mismatch"):
            Aquarium(self.local_db, SEED, GRAPH, exploration_mode="goal_free",
                     room=ROOM, start=START)
        path = self.root / "private.jsonl"
        with self.assertRaisesRegex(AquariumError, "mismatch"):
            export_private_trace(
                self.local_db, path, SEED, GRAPH, room=ROOM, start=START,
                exploration_mode="goal_free", allow_private_export=True,
            )
        self.assertFalse(path.exists())
        info = export_private_trace(
            self.local_db, path, SEED, GRAPH, room=ROOM, start=START,
            exploration_mode="goal_free", motor_policy=MODE,
            allow_private_export=True,
            expected_journal_head=prior["journal_head"],
        )
        self.assertEqual(info["journal_head"], prior["journal_head"])
        self.assertEqual(info["frames"], 10)
        lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[0]["developmental"]["schema"], V2_SCHEMA)
        self.assertEqual(lines[-1]["trace_integrity"]["frame_count"], 10)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with Aquarium(
            self.local_db, SEED, GRAPH, exploration_mode="goal_free",
            motor_policy=MODE, room=ROOM, start=START,
        ) as right:
            self.assertEqual(right.checkpoint(), prior)
            self.assertEqual(right.store.count(), 11)

    def test_frozen_v1_event_contract_survives_many_ticks_after_v2_added(self):
        _, first = run_frames(self.coarse_db, policy="coarse_v1", ticks=60)
        with Aquarium(self.coarse_db, SEED, GRAPH, room=ROOM, start=START,
                      exploration_mode="goal_free") as old:
            self.assertNotIn("motor_policy", old.manifest)
            self.assertNotIn("motor_policy_schema", old.manifest)
            self.assertEqual(old.manifest["developmental_schema"], V1_SCHEMA)
            self.assertEqual(old.checkpoint(), first)
            for _ in range(60):
                old.step()
            second = old.checkpoint()
            self.assertEqual(second["developmental_state"]["updates"], 120)
        with Aquarium(self.coarse_db, SEED, GRAPH, room=ROOM, start=START,
                      exploration_mode="goal_free", motor_policy="coarse_v1") as old:
            self.assertEqual(old.checkpoint(), second)
            self.assertEqual(old.store.count(), 121)


if __name__ == "__main__":
    unittest.main()
