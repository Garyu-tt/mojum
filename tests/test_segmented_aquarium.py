"""Synthetic SQLite coverage for the opt-in segmented Aquarium journal."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError, V4_MODE
from mojum.noosphere import NoosphereError
from mojum.segmented_noosphere import STORE_SCHEMA


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SegmentedAquariumTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.db = self.directory / "synthetic.sqlite3"

    def open(self, **overrides):
        options = {
            "motor_mode": V4_MODE,
            "neural_mode": "disabled",
            "journal_backend": "segmented",
            "journal_segment_size": 64,
        }
        options.update(overrides)
        return Aquarium(self.db, SEED, GRAPH, **options)

    def cli(self, module, ticks, *options):
        command = [
            sys.executable, "-m", module, "--db", str(self.db),
            "--ticks", str(ticks), "--motor-mode", V4_MODE,
            "--neural-mode", "disabled", "--sensory-mode", "none",
            "--temporal-mode", "none", "--journal-backend", "segmented",
            "--journal-segment-size", "64", *options,
        ]
        return subprocess.run(
            command, cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            text=True, capture_output=True, timeout=30,
        )

    def test_segments_and_anchored_cold_replay(self):
        with self.open(sensory_mode="toy_probe_v2") as aquarium:
            for _ in range(130):
                aquarium.step()
            committed = aquarium.checkpoint()
            self.assertGreater(committed["toy_contact_state"]["updates"], 0)
            head = committed["journal_head"]
            bootstrap = aquarium.store.event_at(1)["event"]["payload"]
            self.assertEqual(bootstrap["journal_backend"], "segmented")
            self.assertEqual(bootstrap["journal_schema"], STORE_SCHEMA)
            self.assertEqual(bootstrap["journal_segment_size"], 64)
            receipts = list(aquarium.store.iter_segment_receipts(
                external_expected_head=head,
            ))
            self.assertEqual(
                [(r["first_seq"], r["last_seq"]) for r in receipts],
                [(1, 64), (65, 128), (129, 131)],
            )
            self.assertEqual(receipts[-1]["segment_head"], head)

        before = self.db.read_bytes()
        with self.open(sensory_mode="toy_probe_v2", read_only=True,
                       external_expected_head=head) as reader:
            self.assertEqual(reader.checkpoint(), committed)
            with self.assertRaisesRegex(AquariumError, "read-only"):
                reader.step()
            with self.assertRaisesRegex(NoosphereError, "read-only"):
                reader.store.append(reader.store.first_event(), expected_head=head)
        self.assertTrue(self.db.read_bytes() == before, "read-only replay changed the database")

        with self.open(sensory_mode="toy_probe_v2",
                       external_expected_head=head) as resumed:
            self.assertEqual(resumed.checkpoint(), committed)
            resumed.step()
            self.assertEqual((resumed.tick, resumed.store.count()), (131, 132))
            self.assertNotEqual(resumed.checkpoint()["journal_head"], head)

    def test_read_only_replay_sees_uncheckpointed_wal(self):
        with self.open() as writer:
            writer.step()
            committed = writer.checkpoint()
            wal = self.db.with_name(self.db.name + "-wal")
            self.assertGreater(wal.stat().st_size, 32)
            with self.open(read_only=True,
                           external_expected_head=committed["journal_head"]) as reader:
                self.assertEqual((reader.tick, reader.store.count()), (1, 2))
                self.assertEqual(reader.checkpoint(), committed)

    def test_wrong_backend_size_and_anchor_leave_sources_untouched(self):
        with self.open() as aquarium:
            aquarium.step()
            committed = aquarium.checkpoint()
        before = self.db.read_bytes()
        with self.assertRaises((AquariumError, NoosphereError)):
            Aquarium(self.db, SEED, GRAPH, motor_mode=V4_MODE,
                     neural_mode="disabled")
        with self.assertRaises((AquariumError, NoosphereError)):
            self.open(journal_segment_size=128)
        with self.assertRaises((AquariumError, NoosphereError)):
            self.open(external_expected_head="0" * 64)
        with self.assertRaisesRegex(AquariumError, "mode mismatch"):
            self.open(sensory_mode="toy_probe_v2")
        self.assertTrue(self.db.read_bytes() == before, "rejected open changed the database")
        with self.open(read_only=True) as replay:
            self.assertEqual(replay.checkpoint(), committed)

        self.db = self.directory / "legacy.sqlite3"
        with Aquarium(self.db, SEED, GRAPH, motor_mode=V4_MODE,
                       neural_mode="disabled") as legacy:
            legacy.step()
            before_legacy = legacy.checkpoint()
            self.assertFalse({"journal_backend", "journal_schema", "journal_segment_size"}
                             & legacy.manifest.keys())
        before = self.db.read_bytes()
        with self.assertRaises((AquariumError, NoosphereError)):
            self.open()
        self.assertTrue(self.db.read_bytes() == before, "rejected open changed the legacy database")
        with Aquarium(self.db, SEED, GRAPH, motor_mode=V4_MODE,
                       neural_mode="disabled", read_only=True) as legacy:
            self.assertEqual(legacy.checkpoint(), before_legacy)

    def test_invalid_selectors_and_missing_anchored_journal_do_not_bootstrap(self):
        for options in (
            {"motor_mode": "scripted"},
            {"journal_backend": "unknown"},
            {"journal_segment_size": True},
            {"journal_segment_size": "64"},
            {"journal_segment_size": 63},
            {"journal_segment_size": 8193},
            {"external_expected_head": "0" * 64},
            {"read_only": True},
        ):
            with self.subTest(options=options):
                with self.assertRaises((AquariumError, NoosphereError)):
                    self.open(**options)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_stale_writer_keeps_memory_at_previous_segment(self):
        with self.open() as writer:
            for _ in range(63):
                writer.step()
            self.assertEqual(writer.store.count(), 64)
            with self.open() as stale:
                before = stale.checkpoint()
                writer.step()
                committed = writer.checkpoint()
                with self.assertRaisesRegex(NoosphereError, "CAS failed"):
                    stale.step()
                self.assertEqual(stale.tick, before["tick"])
                self.assertEqual(stale.motor.snapshot(), before["developmental_state"])
                with self.assertRaisesRegex(AquariumError, "stale aquarium checkpoint"):
                    stale.checkpoint()
        with self.open(read_only=True) as replay:
            self.assertEqual(replay.checkpoint(), committed)
            verified = replay.store.verify_chain()
            self.assertEqual((verified["count"], verified["segments"]), (65, 2))

    def test_aquarium_and_portable_cli_continue_same_journal(self):
        initial = self.cli("mojum.aquarium", 3)
        self.assertEqual(initial.returncode, 0, initial.stderr)
        first = json.loads(initial.stdout)
        self.assertEqual((first["tick"], first["stored_events"]), (3, 4))

        continued = self.cli("mojum.portable_host", 2, "--interval-ms", "20",
                             "--expected-head", first["journal_head"])
        self.assertEqual(continued.returncode, 0, continued.stderr)
        second = json.loads(continued.stdout)
        self.assertEqual((second["start_tick"], second["end_tick"],
                          second["journal_events"]), (3, 5, 6))
        self.assertNotIn("developmental_state", second)
        self.assertNotEqual(second["journal_head"], first["journal_head"])

        with self.open(read_only=True,
                       external_expected_head=second["journal_head"]) as replay:
            self.assertEqual((replay.tick, replay.store.count()), (5, 6))
