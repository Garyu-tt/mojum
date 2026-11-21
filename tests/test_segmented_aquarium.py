"""R0.16 opt-in Aquarium integration for one-file logical segmented journal.

No actual Noosphere user archives, production servers, Android devices,
or physical actuators. All databases/key files are disposable and PRIVATE.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import LocalNoosphere, NoosphereError
from mojum.segmented_noosphere import (
    SegmentedNoosphere, STORE_SCHEMA as SEGMENTED_SCHEMA,
)
from mojum.verified_snapshot import create_key_file, load_snapshot
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
OWNER = "dante-fixture-001"


class ExplicitSegmentedAquariumQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "PRIVATE-synthetic-segmented.sqlite3"
        self.key = self.root / "synthetic-hmac.key"
        self.cache = self.root / "synthetic-private-replay.json"
        create_key_file(self.key)

    def open(self, *, backend="segmented", size=64, signed=True):
        kw = {}
        if signed:
            kw.update(
                replay_snapshot_file=self.cache,
                replay_snapshot_key_file=self.key,
            )
        return Aquarium(
            self.db, SEED, GRAPH, room=Room(.16, .16),
            start=(.16, .16), exploration_mode="goal_free",
            neural_mode="disabled", sensory_mode="none",
            developmental_policy="local_cell_v4",
            journal_backend=backend, journal_segment_size=size,
            **kw,
        )

    def test_330_real_frames_across_six_segments_signed_and_full_replay(self):
        with self.open() as original:
            self.assertEqual(original.manifest["journal_backend"], "segmented")
            self.assertEqual(original.manifest["journal_schema"],
                             SEGMENTED_SCHEMA)
            self.assertEqual(original.manifest["journal_segment_size"], 64)
            for _ in range(130):
                original.step()
            prefix = original.checkpoint()
            self.assertEqual(original.store.count(), 131)
            original.save_replay_snapshot()
            for _ in range(200):
                original.step()
            end = original.checkpoint()
            self.assertEqual(original.tick, 330)
            self.assertEqual(original.store.count(), 331)
            report = original.store.verify_chain(
                external_expected_head=end["journal_head"]
            )
            self.assertEqual(report["segments"], 6)
            receipts = list(original.store.iter_segment_receipts(
                external_expected_head=end["journal_head"]
            ))
            self.assertEqual(receipts[-1]["last_seq"], 331)
            self.assertEqual(receipts[-1]["segment_head"],
                             end["journal_head"])
            self.assertTrue(all("payload" not in x for x in receipts))
        with self.open() as signed:
            self.assertEqual(signed.checkpoint(), end)
            self.assertEqual(signed.store.count(), 331)
            self.assertEqual(load_snapshot(self.cache, self.key)["checkpoint"],
                             prefix)
            signed.step()
            final = signed.checkpoint()
        with self.open(signed=False) as from_source:
            self.assertEqual(from_source.checkpoint(), final)
            self.assertEqual(from_source.store.verify_chain()["segments"], 6)
            self.assertEqual(from_source.tick, 331)

    def test_old_journal_schema_and_wrong_size_refuse_without_rewrite(self):
        with self.open(signed=False) as q:
            for _ in range(9):
                q.step()
            checkpoint = q.checkpoint()
        for opts in ({"backend": "legacy"},
                     {"backend": "segmented", "size": 128}):
            with self.subTest(opts=opts):
                with self.assertRaises((NoosphereError, AquariumError)):
                    with self.open(signed=False, **opts):
                        pass
        with self.open(signed=False) as reopen:
            self.assertEqual(reopen.checkpoint(), checkpoint)
            self.assertEqual(reopen.store.count(), 10)

        other = self.root / "legacy-original.sqlite3"
        with LocalNoosphere(other, OWNER) as legacy:
            before = legacy.verify_chain()
        with self.assertRaises(NoosphereError):
            SegmentedNoosphere(other, OWNER, segment_size=64)
        with LocalNoosphere(other, OWNER) as legacy:
            self.assertEqual(legacy.verify_chain(), before)

    def test_stale_aquarium_writer_rejects_commit_and_ram_update(self):
        with self.open(signed=False) as first, self.open(signed=False) as stale:
            before = stale.checkpoint()
            first.step()
            after = first.checkpoint()
            self.assertNotEqual(after["journal_head"], before["journal_head"])
            with self.assertRaisesRegex(NoosphereError, "CAS failed"):
                stale.step()
            self.assertEqual(stale.tick, 0)
            self.assertEqual(stale.explorer.snapshot(),
                             before["developmental_state"])
            with self.assertRaisesRegex(
                AquariumError, "stale or unadopted",
            ):
                stale.checkpoint()
        with self.open(signed=False) as recovered:
            self.assertEqual(recovered.checkpoint(), after)

    def test_new_backend_rejects_legacy_and_bad_size_before_bootstrap(self):
        with self.assertRaisesRegex(
            AquariumError, "requires explicit local_cell_v4",
        ):
            Aquarium(
                self.db, SEED, GRAPH,
                exploration_mode="goal_free",
                developmental_policy="local_cell_v3",
                journal_backend="segmented",
            )
        self.assertFalse(self.db.exists())
        for value in (False, 0, 63, 8193, "64"):
            with self.subTest(value=value):
                with self.assertRaises(AquariumError):
                    Aquarium(
                        self.db, SEED, GRAPH,
                        exploration_mode="goal_free",
                        developmental_policy="local_cell_v4",
                        journal_backend="segmented",
                        journal_segment_size=value,
                    )
                self.assertFalse(self.db.exists())

    def test_actual_two_portable_subprocesses_same_journal_and_hmac(self):
        command = [
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(self.db),
            "--ticks", "3", "--interval-ms", "20",
            "--neural-mode", "disabled",
            "--developmental-policy", "local_cell_v4",
            "--sensory-mode", "none",
            "--journal-backend", "segmented",
            "--journal-segment-size", "64",
            "--snapshot-file", str(self.cache),
            "--snapshot-key-file", str(self.key),
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        for begin in (0, 3):
            outcome = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True,
                text=True, timeout=20,
            )
            self.assertEqual(outcome.returncode, 0, outcome.stderr)
            report = json.loads(outcome.stdout)
            self.assertEqual(report["start_tick"], begin)
            self.assertEqual(report["end_tick"], begin + 3)
            self.assertEqual(report["journal_events"], begin + 4)
            self.assertNotIn("neural_state", outcome.stdout)
        with self.open() as q:
            self.assertEqual(q.tick, 6)
            self.assertEqual(q.store.verify_chain()["count"], 7)


if __name__ == "__main__":
    unittest.main()
