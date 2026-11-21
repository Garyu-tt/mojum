"""Synthetic PRIVATE exporter pins policy wire ABI without exporting model state.

Both developmental policy versions and original scripted mode are exercised
using real replay-verified SQLite; this does not authenticate a local file.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace
from mojum.developmental_policy import SCHEMA as V1_SCHEMA
from mojum.developmental_policy_v2 import SCHEMA as V2_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class VersionedPrivateTraceMetadataTests(unittest.TestCase):
    def test_replay_verified_export_pins_only_schema_without_learned_state(self):
        with tempfile.TemporaryDirectory(prefix="mojum-policy-footer-") as temp:
            for label, mode, policy, schema in (
                ("scripted", "scripted", "coarse_v1", "scripted"),
                ("legacy-v1", "goal_free", "coarse_v1", V1_SCHEMA),
                ("local-v2a", "goal_free", "local_cell_v2", V2_SCHEMA),
            ):
                with self.subTest(policy=label):
                    db = Path(temp) / f"{label}.sqlite3"
                    trace = Path(temp) / f"{label}.private.jsonl"
                    with Aquarium(
                        db, SEED, GRAPH, neural_mode="disabled",
                        exploration_mode=mode, motor_policy=policy,
                    ) as a:
                        for _ in range(3):
                            if mode == "scripted":
                                a.step(dx=.02, dy=0.)
                            else:
                                a.step()
                        head = a.checkpoint()["journal_head"]
                        before = a.store.count()
                    result = export_private_trace(
                        db, trace, SEED, GRAPH, allow_private_export=True,
                        neural_mode="disabled", exploration_mode=mode,
                        motor_policy=policy, expected_journal_head=head,
                    )
                    parsed = [
                        json.loads(line)
                        for line in trace.read_text(encoding="utf-8").splitlines()
                    ]
                    self.assertEqual(len(parsed), 4)
                    self.assertEqual(result["journal_head"], head)
                    self.assertEqual(result["event_count"], before)
                    footer = parsed[-1]
                    self.assertEqual(footer["policy_schema"], schema)
                    self.assertEqual(footer["trace_integrity"]["frame_count"], 3)
                    self.assertEqual(footer["journal_head"], head)
                    for forbidden in (
                        "developmental_state", "neural_state", "records",
                        "genome_revision", "genotype", "phenotype",
                    ):
                        self.assertNotIn(forbidden, footer)
                    self.assertEqual(
                        {f.get("developmental", {}).get("schema", "scripted")
                         for f in parsed[:-1]},
                        {schema},
                    )


if __name__ == "__main__":
    unittest.main()
