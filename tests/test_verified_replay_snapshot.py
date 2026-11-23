"""HMAC PRIVATE Aquarium cache: actual SQLite replay, fail-closed tests.

No arbitrary sidecar can become authoritative; canonical Noosphere is
verified in full and the signed snapshot only skips deterministic simulations
of its already-signed prefix. Test data is synthetic and never published.
"""

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.noosphere import NoosphereError
from mojum.verified_snapshot import (
    SnapshotError, create_key_file, load_snapshot, save_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SignedReplayCacheQA(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory(prefix="mojum-hmac-qa-")
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.db = self.root / "private.sqlite3"
        self.snapshot = self.root / "private.replay.snapshot.json"
        self.key = self.root / "separate.private.hmac.key"
        create_key_file(self.key)

    def aquarium(self, *, signed=True, **kwargs):
        opts = {"exploration_mode":"goal_free", "sensory_mode":"toy_probe"}
        if signed:
            opts.update(replay_snapshot_file=self.snapshot,
                        replay_snapshot_key_file=self.key)
        opts.update(kwargs)
        return Aquarium(self.db, SEED, GRAPH, **opts)

    def make_snapshot(self, ticks=12):
        with self.aquarium() as q:
            for _ in range(ticks):
                q.step()
            pinned = q.checkpoint()
            created = q.save_replay_snapshot()
        return pinned, created

    def test_first_run_and_save_are_private_and_complete(self):
        pinned, created = self.make_snapshot()
        self.assertEqual(pinned["tick"], 12)
        self.assertEqual(len(created["state_sha256"]), 64)
        state = load_snapshot(self.snapshot, self.key)
        self.assertEqual(state["journal_events"], 13)
        self.assertEqual(state["checkpoint"], pinned)
        self.assertEqual(state["world"]["tick"], 12)
        self.assertEqual(state["explorer"]["updates"], 12)
        self.assertGreaterEqual(state["toy_contact_state"]["updates"], 1)
        self.assertEqual(state["journal_head"], pinned["journal_head"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.snapshot.stat().st_mode), 0o600)

    def test_fast_reopen_replays_only_new_tail_and_matches_full_replay(self):
        original, _ = self.make_snapshot(15)
        with self.aquarium(signed=False) as live:
            self.assertEqual(live.checkpoint(), original)
            for _ in range(5):
                live.step()
            final = live.checkpoint()
        actual_recomputed = []
        original_calculate = Aquarium._calculate

        def counted(self, dx, dy):
            actual_recomputed.append(self.tick)
            return original_calculate(self, dx, dy)

        with patch.object(Aquarium, "_calculate", counted):
            with self.aquarium(signed=True) as restored:
                self.assertEqual(restored.tick, 20)
                self.assertEqual(restored.checkpoint(), final)
                self.assertEqual(restored.store.count(), 21)
                self.assertEqual(restored.sensory_bridge.model.snapshot(),
                                 final["toy_contact_state"])
                self.assertEqual(restored.explorer.snapshot(),
                                 final["developmental_state"])
                self.assertEqual(actual_recomputed, list(range(15, 20)))
                restored.save_replay_snapshot()
        self.assertEqual(load_snapshot(self.snapshot, self.key)["tick"], 20)
        with self.aquarium(signed=False) as full_replay:
            self.assertEqual(full_replay.checkpoint(), final)
        with self.aquarium(signed=True) as again:
            self.assertEqual(again.checkpoint(), final)

    def test_acquired_genome_checkpoint_keeps_causal_future_effect(self):
        with self.aquarium() as q:
            q.step()
            q.step()
            change = {
                "schema": ACQUISITION_SCHEMA,
                "event_id": "signed-acquisition",
                "genome_id": SEED["genome_id"],
                "gene": "heartbeat_bpm",
                "delta": 15,
                "valid_from_tick": 2,
                "observed_at_ns": 0,
                "recorded_at_ns": 2 * q.dt_ns,
                "source": "synthetic-replay-unit-test",
                "evidence": "private synthetic event not human acquisition",
            }
            q.acquire(change)
            pinned = q.checkpoint()
            q.save_replay_snapshot()
            next_frame = q.step()
            finished = q.checkpoint()
            self.assertEqual(next_frame["phenotype"]["parameters"]["heartbeat_bpm"],
                             SEED["inherited"]["heartbeat_bpm"] + 15)
        with self.aquarium() as resumed:
            self.assertEqual(resumed.checkpoint(), finished)
            self.assertEqual(resumed.genome.snapshot()["revision"],
                             finished["genome_revision"])
            self.assertEqual(resumed.store.count(), 5)
        with self.aquarium(signed=False) as full:
            self.assertEqual(full.checkpoint(), finished)
        self.assertEqual(pinned["tick"], 2)
        self.assertEqual(load_snapshot(self.snapshot, self.key)["checkpoint"],
                         pinned)

    def test_bad_hmac_refused_before_changing_source_sqlite(self):
        self.make_snapshot()
        before = self.db.read_bytes()
        raw = self.snapshot.read_text()
        self.snapshot.write_text(raw.replace('"tick":12', '"tick":11', 1))
        self.snapshot.chmod(0o600)
        with self.assertRaisesRegex(SnapshotError, "HMAC mismatch"):
            self.aquarium()
        self.assertEqual(before, self.db.read_bytes())
        with self.aquarium(signed=False) as good:
            self.assertEqual(good.tick, 12)

    def test_validly_macd_but_invalid_state_is_refused(self):
        self.make_snapshot()
        state = load_snapshot(self.snapshot, self.key)
        self.assertIsNotNone(state)
        modified = copy.deepcopy(state)
        modified["world"]["x"] = 2.0  # Occupied by the motor-room wall.
        save_snapshot(self.snapshot, self.key, modified)
        with self.assertRaisesRegex(SnapshotError, "point-room"):
            self.aquarium()
        with self.aquarium(signed=False) as good:
            self.assertEqual(good.tick, 12)

    def test_valid_hmac_cannot_fast_forward_after_deterministic_code_change(self):
        # Source byte fingerprints prevent skipping historic simulations under
        # changed runtime logic even when every cache field is re-MACed.
        self.make_snapshot(ticks=6)
        state = load_snapshot(self.snapshot, self.key)
        altered = copy.deepcopy(state)
        altered["runtime_source_sha256"] = "0" * 64
        save_snapshot(self.snapshot, self.key, altered)
        with self.assertRaisesRegex(SnapshotError, "runtime source changed"):
            self.aquarium()
        with self.aquarium(signed=False) as cold:
            self.assertEqual(cold.tick, 6)

    def test_corrupt_source_prefix_rejected_even_with_valid_hmac(self):
        self.make_snapshot()
        with closing(sqlite3.connect(self.db)) as con:
            with con:
                con.execute("DROP TRIGGER noosphere_no_update")
                con.execute("UPDATE events SET topic = 'tampered' WHERE seq=2")
        with self.assertRaises(NoosphereError):
            self.aquarium()

    def test_valid_signed_snapshot_from_future_cannot_bootstrap_other_db(self):
        self.make_snapshot(ticks=8)
        old = self.db
        self.db = self.root / "other.sqlite3"
        with self.aquarium(signed=False) as fresh:
            fresh.step()
            before = fresh.checkpoint()
        with self.assertRaisesRegex(SnapshotError, "count or tick"):
            self.aquarium()
        with self.aquarium(signed=False) as unchanged:
            self.assertEqual(unchanged.checkpoint(), before)
        self.db.unlink()
        with self.assertRaisesRegex(AquariumError, "source is missing"):
            self.aquarium()
        self.db = old

    def test_new_snapshot_requires_old_owner_only_key_and_independent_paths(self):
        self.make_snapshot()
        with self.assertRaisesRegex(AquariumError, "supplied together"):
            self.aquarium(replay_snapshot_key_file=None)
        if os.name == "posix":
            self.key.chmod(0o644)
            with self.assertRaisesRegex(SnapshotError, "deny group/other"):
                self.aquarium()
            self.key.chmod(0o600)
        alias = self.root / "key-alias"
        try:
            alias.symlink_to(self.key)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaisesRegex(SnapshotError, "symlink"):
                load_snapshot(self.snapshot, alias)
        with self.assertRaisesRegex(SnapshotError, "refusing to replace"):
            create_key_file(self.key)

    def test_snapshot_optional_old_full_replay_and_incompatible_scene(self):
        self.make_snapshot(ticks=5)
        with self.aquarium(signed=False) as q:
            self.assertEqual(q.tick, 5)
        with self.assertRaises(AquariumError):
            self.aquarium(sensory_mode="none")
        with self.aquarium() as q:
            self.assertEqual(q.tick, 5)

    def test_real_portable_host_two_subprocesses_persist_private_signed_cache(self):
        cmd = [
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(self.db),
            "--ticks", "4", "--interval-ms", "20",
            "--snapshot-file", str(self.snapshot),
            "--snapshot-key-file", str(self.key),
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        results = []
        for _ in range(2):
            completed = subprocess.run(cmd, cwd=ROOT, env=env, text=True,
                                       capture_output=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn("toy_contact", completed.stdout)
            self.assertNotIn("measured_deflection", completed.stdout)
            result = json.loads(completed.stdout)
            self.assertEqual(result["snapshot_cache"],
                             "HMAC_LOCAL_PLAINTEXT_NOT_ENCRYPTED")
            results.append(result)
        self.assertEqual([r["start_tick"] for r in results], [0, 4])
        self.assertEqual([r["end_tick"] for r in results], [4, 8])
        self.assertEqual(load_snapshot(self.snapshot, self.key)["tick"], 8)
        with self.aquarium(signed=False) as q:
            self.assertEqual(q.checkpoint()["journal_head"],
                             results[-1]["journal_head"])
            self.assertEqual(q.tick, 8)

    def test_deeply_nested_snapshot_json_fails_closed_before_sqlite_bootstrap(self):
        # Below the 1 MiB sidecar limit, but above the CPython JSON nesting
        # limit. Malformed attacker-controlled bytes must become SnapshotError
        # rather than leaking RecursionError, and must not create a new DB.
        raw = "[" * 1500 + "0" + "]" * 1500
        self.snapshot.write_text(raw, encoding="utf-8")
        if os.name == "posix":
            self.snapshot.chmod(0o600)
        self.assertFalse(self.db.exists())
        with self.assertRaisesRegex(SnapshotError, "invalid UTF-8 JSON snapshot"):
            self.aquarium()
        self.assertFalse(self.db.exists())

    def test_deeply_nested_python_state_rejected_as_snapshot_error(self):
        from mojum.verified_snapshot import canonical

        state = 0
        for _ in range(1500):
            state = [state]
        with self.assertRaisesRegex(
                SnapshotError, "snapshot must be canonical finite JSON"):
            canonical(state)

    def test_invalid_key_and_missing_file_never_birth_new_organism(self):
        other = self.root / "new.sqlite3"
        secret = self.root / "bad-key"
        secret.write_bytes(b"bad")
        if os.name == "posix":
            secret.chmod(0o600)
        with self.assertRaisesRegex(SnapshotError, "exactly 32"):
            Aquarium(other, SEED, GRAPH, exploration_mode="goal_free",
                     replay_snapshot_file=self.snapshot,
                     replay_snapshot_key_file=secret)
        self.assertFalse(other.exists())
        with self.assertRaisesRegex(SnapshotError, "missing/inaccessible"):
            Aquarium(other, SEED, GRAPH, exploration_mode="goal_free",
                     replay_snapshot_file=self.snapshot,
                     replay_snapshot_key_file=self.root / "missing-key")
        self.assertFalse(other.exists())


if __name__ == "__main__":
    unittest.main()
