"""Long-run v4 + versioned synthetic contact replay QA.

The stress case is deliberately opt-in because it writes and replays 10,050
SQLite frames. It proves a bounded, versioned numeric contact journal can
cross the legacy 4,000-measurement boundary and the v4 epoch marker. It does
not prove a trained brain, object semantics, physical touch, consciousness,
authentication, or a complete digital organism.
"""

import json
import os
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.epoch_sense_v2 import EPOCH_TICKS
from mojum.sensory_associations_v2 import SCHEMA as MODEL_SCHEMA
from mojum.sensory_bridge_v2 import SCHEMA as BRIDGE_SCHEMA
from mojum.sensory_play import Toy, ToyScene


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
STRESS_ENABLED = os.environ.get("MOJUM_SENSORY_V2_STRESS") == "1"


@unittest.skipUnless(
    STRESS_ENABLED,
    "set MOJUM_SENSORY_V2_STRESS=1 for the 10,050-tick SQLite stress run",
)
class SensoryV2EpochSQLiteTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.db = self.root / "v4-sensory-private.sqlite3"
        # The v4 motor ABI pins the built-in 4m room and start position. These
        # bounded spectator-only toys cover the deterministic path through the
        # open left chamber; the motor never receives their IDs, geometry or
        # softness. The repeated numeric cue is still measured by the bridge
        # rather than injected into the motor policy.
        self.scene = ToyScene(
            width=4.0,
            height=4.0,
            toys=(
                tuple(
                    Toy(f"spectator.v4.horizontal.{index:02d}",
                        .3 + .14 * index, 2.0, .2,
                        (42, 90, 174), .4)
                    for index in range(6)
                )
                + tuple(
                    Toy(f"spectator.v4.vertical.{index:02d}",
                        .3, .5 + .12 * index, .2,
                        (42, 90, 174), .4)
                    for index in range(14)
                )
            ),
        )

    def open(self):
        return Aquarium(
            self.db,
            SEED,
            GRAPH,
            motor_mode=V4_MODE,
            neural_mode="disabled",
            sensory_mode="toy_probe_v2",
            sensory_scene=self.scene,
        )

    def test_real_10050_frames_cross_both_bounded_boundaries_and_cold_replay(self):
        with self.open() as aquarium:
            for _ in range(8_050):
                frame = aquarium.step()
            self.assertEqual(frame["toy_contact"]["schema"], BRIDGE_SCHEMA)
            crossing = aquarium.checkpoint()
            self.assertEqual(crossing["tick"], 8_050)
            self.assertEqual(crossing["toy_contact_state"]["schema"], MODEL_SCHEMA)
            self.assertGreater(crossing["toy_contact_state"]["updates"], 4_000)

            for _ in range(2_000):
                aquarium.step()
            final = aquarium.checkpoint()
            self.assertEqual(aquarium.tick, 10_050)
            self.assertEqual(aquarium.epoch_index, 1)
            self.assertGreater(final["toy_contact_state"]["updates"], 5_000)
            self.assertEqual(aquarium.store.count(), 10_052)
            marker = next(
                row["event"]
                for row in aquarium.store.iter_events(include_nonpublic=True)
                if row["seq"] == 10_001
            )
            self.assertEqual(marker["topic"], "aquarium.epoch")

        with self.open() as restored:
            self.assertEqual(restored.checkpoint(), final)
            wire = inspect_journal(
                self.db,
                external_expected_head=final["journal_head"],
            )
            self.assertEqual(wire["format"], "local_cell_v4+toy_probe_v2")
            self.assertEqual(wire["frames"], 10_050)
            self.assertEqual(wire["events"], 10_052)
            self.assertEqual(wire["epoch_ticks"], EPOCH_TICKS)
            self.assertEqual(wire["sensory_schema"], MODEL_SCHEMA)


if __name__ == "__main__":
    unittest.main()
