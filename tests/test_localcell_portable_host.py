"""Portable CPython/Termux host + opt-in v3 integration and boundary QA.

Synthetic subprocesses only; no physical Android permission, socket,
production Noosphere journal or real agent.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from mojum.aquarium import Aquarium
from mojum.developmental_policy import MAX_EPISODE_TICKS
from mojum.portable_host import PortableHostError, run_paced
from mojum.verified_snapshot import create_key_file
from mojum.cell_policy_v3 import SCHEMA as V3_SCHEMA


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class PortableVersionedHostQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "synthetic.sqlite3"
        self.key = self.root / "owner.key"
        self.snapshot = self.root / "owner.snapshot.json"
        create_key_file(self.key)
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

    def invoke(self, *args):
        return subprocess.run([
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(self.db), "--ticks", "3", "--interval-ms", "20",
            *args,
        ], env=self.env, cwd=ROOT, text=True, capture_output=True,
            timeout=18)

    def test_two_real_portable_host_sessions_v3_with_signed_resume(self):
        args = [
            "--developmental-policy", "local_cell_v3",
            "--snapshot-file", str(self.snapshot),
            "--snapshot-key-file", str(self.key),
        ]
        first = self.invoke(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        a = json.loads(first.stdout)
        self.assertEqual(a["start_tick"], 0)
        self.assertEqual(a["end_tick"], 3)
        self.assertEqual(a["journal_events"], 4)
        self.assertEqual(a["snapshot_cache"],
                         "HMAC_LOCAL_PLAINTEXT_NOT_ENCRYPTED")
        self.assertNotIn("genome", first.stdout)
        self.assertNotIn("developmental_state", first.stdout)
        second = self.invoke(*args)
        self.assertEqual(second.returncode, 0, second.stderr)
        b = json.loads(second.stdout)
        self.assertEqual(b["start_tick"], 3)
        self.assertEqual(b["end_tick"], 6)
        self.assertEqual(b["journal_events"], 7)
        self.assertNotEqual(b["journal_head"], a["journal_head"])
        with Aquarium(
            self.db, SEED, GRAPH, exploration_mode="goal_free",
            sensory_mode="toy_probe", developmental_policy="local_cell_v3",
            replay_snapshot_file=self.snapshot,
            replay_snapshot_key_file=self.key,
        ) as q:
            self.assertEqual(q.tick, 6)
            self.assertEqual(q.explorer.snapshot()["schema"], V3_SCHEMA)
            self.assertEqual(q.store.count(), 7)
            self.assertEqual(q.store.head(), b["journal_head"])
        # Wrong policy must not silently reopen same real PRIVATE source.
        invalid = self.invoke(
            "--developmental-policy", "legacy",
            "--snapshot-file", str(self.snapshot),
            "--snapshot-key-file", str(self.key),
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stdout, "")
        with Aquarium(
            self.db, SEED, GRAPH, exploration_mode="goal_free",
            sensory_mode="toy_probe", developmental_policy="local_cell_v3",
            replay_snapshot_file=self.snapshot,
            replay_snapshot_key_file=self.key,
        ) as q:
            self.assertEqual(q.tick, 6)
            self.assertEqual(q.store.head(), b["journal_head"])

    def test_episode_preflight_rejects_whole_run_without_partial_step(self):
        class Counter:
            def count(self):
                return 1

        class Fake:
            exploration_mode = "goal_free"
            store = Counter()
            tick = MAX_EPISODE_TICKS - 2
            calls = 0

            def step(self):
                self.calls += 1
                raise AssertionError("preflight must reject before step")

        fake = Fake()
        with self.assertRaisesRegex(
            PortableHostError, "episode ticks",
        ):
            run_paced(
                fake, ticks=3, interval_ms=20,
                stop_event=threading.Event(),
            )
        self.assertEqual(fake.calls, 0)

    def test_invalid_policy_refused_without_new_sqlite_birth(self):
        bad = self.invoke("--developmental-policy", "unversioned")
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
