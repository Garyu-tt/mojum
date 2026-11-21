"""Opt-in R0.16 export of real PRIVATE segmented v4 and v2 sensory histories.

CPU/stdlib only, disposable SQLite, no actual phones, private user records,
world-object oracle, neural mind, or physical actuation. A hash in JSONL
is not a signature. No old history is ever migrated or modified.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SegmentedVersionedTraceExportQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mojum-r016-export-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.room = Room(.32, .32)
        self.scene = ToyScene(
            width=.32, height=.32,
            toys=(Toy("inert.synthetic", .16, .16, .15, (42, 90, 174), .4),),
        )

    def opts(self, *, policy="local_cell_v4", backend="segmented",
             sense="none", segment_size=64):
        options = {
            "neural_mode": "disabled", "exploration_mode": "goal_free",
            "developmental_policy": policy, "journal_backend": backend,
            "journal_segment_size": segment_size, "sensory_mode": sense,
        }
        if sense == "toy_probe_v2":
            options.update({
                "room": self.room, "start": (.16, .16),
                "sensory_scene": self.scene,
            })
        return options

    def new_source(self, options, *, ticks=5):
        db = self.root / "source.sqlite3"
        with Aquarium(db, SEED, GRAPH, **options) as a:
            frames = [a.step() for _ in range(ticks)]
            checkpoint = a.checkpoint()
            count = a.store.count()
        return db, frames, checkpoint, count

    def export(self, db, filename, options, head, **extras):
        return export_private_trace(
            db, self.root / filename, SEED, GRAPH,
            allow_private_export=True, expected_journal_head=head,
            **options, **extras,
        )

    def rows(self, filename):
        return [
            json.loads(line)
            for line in (self.root / filename).read_text("utf-8").splitlines()
        ]

    def test_local_cell_v4_legacy_backend_footer_version_and_no_private_state(self):
        options = self.opts(backend="legacy")
        db, frames, checkpoint, count = self.new_source(options)
        before = db.read_bytes()
        self.export(db, "export.jsonl", options, checkpoint["journal_head"])
        self.assertEqual(db.read_bytes(), before)
        output = self.rows("export.jsonl")
        self.assertEqual(output[:-1], frames)
        footer = output[-1]
        self.assertEqual(footer["schema"], "mojum.aquarium.checkpoint/2")
        self.assertEqual(footer["policy_schema"], "mojum.developmental.local-cell/4")
        self.assertEqual(footer["epoch_index"], 0)
        self.assertEqual(footer["epoch_tick"], 5)
        self.assertEqual(footer["epoch_boundaries"], [])
        self.assertEqual(footer["stored_events"], count)
        for key in ("developmental_state", "toy_contact_state",
                    "neural_state", "genome_revision", "genotype", "phenotype"):
            self.assertNotIn(key, footer)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((self.root / "export.jsonl").stat().st_mode),
                             0o600)

    def test_segmented_v4_bounded_range_and_exact_head(self):
        options = self.opts()
        db, frames, checkpoint, count = self.new_source(options)
        before = db.read_bytes()
        result = self.export(
            db, "bounded.jsonl", options, checkpoint["journal_head"],
            start_tick=1, frame_limit=3,
        )
        footer = self.rows("bounded.jsonl")[-1]
        self.assertEqual(result["frames"], 3)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["event_count"], count)
        self.assertEqual(self.rows("bounded.jsonl")[:-1], frames[1:4])
        self.assertEqual(footer["trace_range"], {
            "start_tick": 1, "next_tick": 4,
            "source_total_ticks": 5, "has_more": True,
        })
        self.assertEqual(footer["journal_head"], checkpoint["journal_head"])
        self.assertEqual(footer["epoch_boundaries"], [])
        self.assertEqual(db.read_bytes(), before)

    def test_segmented_sensory_v2_does_not_leak_learned_model_in_footer(self):
        options = self.opts(sense="toy_probe_v2")
        db, frames, checkpoint, count = self.new_source(options, ticks=6)
        self.assertGreater(checkpoint["toy_contact_state"]["updates"], 0)
        original = db.read_bytes()
        self.export(db, "sensed.jsonl", options, checkpoint["journal_head"])
        self.assertEqual(db.read_bytes(), original)
        rows = self.rows("sensed.jsonl")
        self.assertEqual(rows[:-1], frames)
        self.assertEqual(rows[-1]["stored_events"], count)
        for forbidden in ("toy_contact_state", "sensory_scene", "world_revision",
                          "neural_state", "developmental_state"):
            self.assertNotIn(forbidden, rows[-1])
        self.assertTrue(any(
            f["toy_contact"]["schema"] == "mojum.aquarium.toy-contact/2"
            for f in frames
        ))
        self.assertTrue(any(
            f["toy_contact"]["learning"] is not None for f in frames
        ))
        self.assertEqual(rows[-1]["policy_schema"],
                         "mojum.developmental.local-cell/4")

    def test_wrong_backend_head_opt_in_or_destination_refuse_without_publish(self):
        options = self.opts()
        db, frames, checkpoint, count = self.new_source(options)
        original = db.read_bytes()
        head = checkpoint["journal_head"]
        cases = (
            ("wrong_head.jsonl", options, "0" * 64, {}),
            ("wrong_backend.jsonl", {**options, "journal_backend": "legacy"}, head, {}),
            ("wrong_size.jsonl", {**options, "journal_segment_size": 128}, head, {}),
            ("wrong_mode.jsonl", {**options, "developmental_policy": "local_cell_v3"}, head, {}),
        )
        for filename, bad_opts, anchor, kw in cases:
            with self.subTest(reason=filename):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.export(db, filename, bad_opts, anchor, **kw)
                self.assertFalse((self.root / filename).exists())
                self.assertEqual(db.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, "independent journal head"):
            export_private_trace(db, self.root / "unanchored.jsonl", SEED, GRAPH,
                                 allow_private_export=True, **options)
        with self.assertRaises(PermissionError):
            export_private_trace(db, self.root / "not_allowed.jsonl", SEED, GRAPH,
                                 expected_journal_head=head, **options)
        (self.root / "occupied.jsonl").write_text("NEVER OVERWRITE", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "new filename"):
            self.export(db, "occupied.jsonl", options, head)
        self.assertEqual((self.root / "occupied.jsonl").read_text(), "NEVER OVERWRITE")
        self.assertEqual(db.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
