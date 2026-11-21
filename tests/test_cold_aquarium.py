"""Cold Aquarium source -> archive -> new-process SQLite replay, synthetic only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.checkpoint_bundle import unpack
from mojum.cold_aquarium import (
    capture_quiescent_aquarium, export_quiescent_aquarium, restore_cold_aquarium,
)
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples" / "genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))
ID = SEED["genome_id"]


def acquired_at(tick, delta=20):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": "cold-acq",
        "genome_id": ID,
        "gene": "heartbeat_bpm",
        "delta": delta,
        "valid_from_tick": tick,
        "observed_at_ns": 0,
        "recorded_at_ns": tick * 10_000_000,
        "source": "synthetic-test",
        "evidence": "not-a-live-organism",
    }


class ColdAquariumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite3"
        self.target = self.root / "target.sqlite3"
        self.archive = self.root / "cold.mojum.json"

    def source_aquarium(self, **kw):
        return Aquarium(self.source, SEED, GRAPH, **kw)

    def test_quiescent_capture_of_real_private_journal(self):
        with self.source_aquarium() as src:
            for _ in range(6):
                src.step(dx=0.01)
            src.acquire(acquired_at(src.tick))
            for _ in range(4):
                src.step(dx=0.01)
            checkpoint = src.checkpoint()
            bundle = capture_quiescent_aquarium(src)
            parts = unpack(bundle, expected_revision=10,
                           expected_organism_id=ID)
            memory = json.loads(parts["memory"])
            ledger = json.loads(parts["genome"])
            neural = json.loads(parts["neural"])
            self.assertEqual(bundle["manifest"]["revision"], 10)
            self.assertEqual(memory["count"], 12)  # bootstrap + ten ticks + acquisition
            self.assertTrue(all(i["event"]["visibility"] == "PRIVATE"
                                for i in memory["items"]))
            self.assertEqual(memory["head"], checkpoint["journal_head"])
            self.assertEqual(ledger["revision"], checkpoint["genome_revision"])
            self.assertEqual(neural["state"], checkpoint["neural_state"])
            self.assertEqual(bundle, capture_quiescent_aquarium(src))

    def test_replay_new_sqlite_and_future_steps_match(self):
        # Integer room dimensions matter: world revision hashes use the
        # original type, while bootstrap manifests intentionally use floats.
        with self.source_aquarium(room=Room(4, 4, ((2, 1, 0.1, 2),))) as src:
            for _ in range(7):
                src.step(dx=.02)
            src.acquire(acquired_at(src.tick))
            for _ in range(16):
                src.step(dx=.025)
            before = src.checkpoint()
            manifest = export_quiescent_aquarium(src, self.archive)
            restored = restore_cold_aquarium(
                self.archive, self.target, expected_organism_id=ID,
                expected_tick=before["tick"],
                expected_manifest_sha256=manifest["manifest_sha256"],
            )
            self.assertEqual(restored, before)
            with Aquarium(self.target, SEED, GRAPH, room=Room(4, 4, ((2, 1, .1, 2),)),
                          external_expected_head=before["journal_head"]) as replica:
                self.assertEqual(replica.checkpoint(), before)
                self.assertEqual(replica.step(dx=.01), src.step(dx=.01))
                self.assertEqual(replica.checkpoint(), src.checkpoint())

    def test_cross_process_export_and_restore_cli(self):
        with self.source_aquarium() as q:
            for _ in range(8):
                q.step(dx=.02)
            expected = q.checkpoint()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        base = [sys.executable, "-m", "mojum.cold_aquarium"]
        exp = json.loads(subprocess.check_output(
            [*base, "export", "--db", str(self.source),
             "--genome", str(ROOT / "examples" / "genesis_seed.json"),
             "--graph", str(ROOT / "examples" / "first_loop.json"),
             "--organism-id", ID, "--output", str(self.archive)],
            env=env, text=True,
        ))
        self.assertEqual(exp["status"], "SYNTHETIC_COLD_ARCHIVE_NOT_LIVE")
        self.assertEqual(exp["tick"], expected["tick"])
        result = json.loads(subprocess.check_output(
            [*base, "restore", "--archive", str(self.archive),
             "--db", str(self.target), "--organism-id", ID,
             "--tick", str(exp["tick"]),
             "--manifest-sha256", exp["manifest_sha256"]],
            env=env, text=True,
        ))
        self.assertEqual(result["status"], "SYNTHETIC_COLD_REPLAY_VERIFIED_NO_MOTOR")
        self.assertEqual(result["journal_head"], expected["journal_head"])
        with Aquarium(self.target, SEED, GRAPH) as q:
            self.assertEqual(q.checkpoint(), expected)

    def test_disabled_shadow_learning_stays_disabled_after_replay(self):
        with self.source_aquarium(neural_mode="disabled") as q:
            for _ in range(3):
                q.step()
            manifest = export_quiescent_aquarium(q, self.archive)
        restore_cold_aquarium(
            self.archive, self.target, expected_organism_id=ID,
            expected_tick=3, expected_manifest_sha256=manifest["manifest_sha256"])
        with Aquarium(self.target, SEED, GRAPH, neural_mode="disabled") as q:
            self.assertEqual(q.tick, 3)
            self.assertIsNone(q.learner)
            self.assertIsNone(q.checkpoint()["neural_state"])

    def test_wrong_identity_revision_and_trusted_anchor_leave_no_files(self):
        with self.source_aquarium() as src:
            src.step()
            manifest = export_quiescent_aquarium(src, self.archive)
        for kwargs in (
            {"expected_organism_id": "other", "expected_tick": 1},
            {"expected_organism_id": ID, "expected_tick": 2},
            {"expected_organism_id": ID, "expected_tick": 1,
             "expected_manifest_sha256": "f" * 64
             if manifest["manifest_sha256"] != "f" * 64 else "0" * 64},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                restore_cold_aquarium(self.archive, self.target, **kwargs)
            self.assertFalse(self.target.exists())
            self.assertEqual(list(self.root.glob(".mojum-cold-*")), [])

    def test_corrupt_archive_no_publication(self):
        with self.source_aquarium() as src:
            src.step()
            export_quiescent_aquarium(src, self.archive)
        data = self.archive.read_bytes()
        self.archive.write_bytes(data[:-1] + b" ")
        with self.assertRaises(ValueError):
            restore_cold_aquarium(self.archive, self.target,
                                  expected_organism_id=ID, expected_tick=1)
        self.assertFalse(self.target.exists())

    def test_existing_target_never_overwritten(self):
        with self.source_aquarium() as src:
            src.step()
            export_quiescent_aquarium(src, self.archive)
        self.target.write_bytes(b"existing independent organism")
        with self.assertRaisesRegex(ValueError, "already exists"):
            restore_cold_aquarium(self.archive, self.target,
                                  expected_organism_id=ID, expected_tick=1)
        self.assertEqual(self.target.read_bytes(), b"existing independent organism")

    def test_no_motor_authority_in_captured_checkpoint(self):
        with self.source_aquarium() as src:
            parts = unpack(capture_quiescent_aquarium(src))
        body = json.loads(parts["body"])
        self.assertEqual(body["status"], "NOT_CAPTURED_ACTUATORS_DISABLED")
        self.assertNotIn("emergency_stop", body)
        self.assertNotIn("motor_intention", body)

    def test_replay_at_tick_zero_without_frames(self):
        with self.source_aquarium() as src:
            before = src.checkpoint()
            export_quiescent_aquarium(src, self.archive)
        got = restore_cold_aquarium(
            self.archive, self.target, expected_organism_id=ID, expected_tick=0)
        self.assertEqual(got, before)
        with Aquarium(self.target, SEED, GRAPH) as q:
            self.assertEqual(q.tick, 0)
            self.assertEqual(q.store.count(), 1)

    def test_noninteger_expected_tick_rejected(self):
        with self.source_aquarium() as src:
            export_quiescent_aquarium(src, self.archive)
        for value in (True, None, "0", -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                restore_cold_aquarium(self.archive, self.target,
                                      expected_organism_id=ID, expected_tick=value)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
