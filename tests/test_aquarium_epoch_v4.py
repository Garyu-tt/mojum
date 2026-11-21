"""Current-main epoch continuation contract on disposable synthetic SQLite."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, V3_MODE, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.noosphere import NoosphereError
from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error, MAX_CELLS
from mojum.epoch_sense_v2 import (
    EPOCH_TICKS, EpochProprioception, EpochSenseError,
    MAX_ABSOLUTE_TICKS, SCHEMA as EPOCH_SCHEMA,
)


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class EpochSenseTests(unittest.TestCase):
    def test_absolute_and_epoch_ticks_are_explicit(self):
        for tick, epoch, offset in (
                (0, 0, 0), (EPOCH_TICKS - 1, 0, EPOCH_TICKS - 1),
                (EPOCH_TICKS, 1, 0), (EPOCH_TICKS + 1, 1, 1)):
            with self.subTest(tick=tick):
                cue = EpochProprioception(
                    tick, epoch, offset, "a" * 64, .16, .16, False,
                )
                self.assertEqual(cue.tick, tick)
                self.assertEqual(cue.public()["schema"], EPOCH_SCHEMA)
                self.assertEqual(cue.public()["epoch_tick"], offset)
        for values in (
                (EPOCH_TICKS, 0, 0),
                (EPOCH_TICKS, 1, 1),
                (-1, 0, 0),
                (MAX_ABSOLUTE_TICKS, 10, 9),
                (0, 0, 0, "a" * 64, 10 ** 1000, .16, False),
        ):
            with self.subTest(values=values), self.assertRaises(EpochSenseError):
                if len(values) == 3:
                    EpochProprioception(*values, "a" * 64, .16, .16, False)
                else:
                    EpochProprioception(*values)

    def test_v4_retention_is_bounded_and_snapshot_strict(self):
        model = CellExplorerV4()
        for tick in range(MAX_CELLS + 17):
            cue = EpochProprioception(
                tick, tick // EPOCH_TICKS, tick % EPOCH_TICKS,
                "0" * 64, (tick + 1) * .04, 0.0, False,
            )
            model.observe(cue, model.decide(cue), False)
        self.assertEqual(len(model.records), MAX_CELLS)
        self.assertEqual(model.evictions, 17)
        self.assertEqual(model.forgotten_observations, 17)
        snapshot = model.snapshot()
        self.assertEqual(CellExplorerV4.from_snapshot(snapshot).snapshot(), snapshot)
        invalid = deepcopy(snapshot)
        invalid["last_epoch_index"] = 99
        with self.assertRaises(CellV4Error):
            CellExplorerV4.from_snapshot(invalid)

    def test_blocked_rest_is_rejected_before_policy_mutation(self):
        model = CellExplorerV4()
        cue = EpochProprioception(0, 0, 0, "0" * 64, .16, .16, False)
        intent = {
            **model.decide(cue),
            "action": "rest", "dx": 0.0, "dy": 0.0,
        }
        before = model.snapshot()
        with self.assertRaisesRegex(CellV4Error, "blocked rest"):
            model.observe(cue, intent, True)
        self.assertEqual(model.snapshot(), before)


class EpochJournalTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.db = Path(scratch.name) / "synthetic-v4.sqlite3"

    def open(self):
        return Aquarium(
            self.db, SEED, GRAPH,
            neural_mode="disabled", motor_mode=V4_MODE,
        )

    def test_readonly_inspector_refuses_missing_marker_on_valid_hash_chain(self):
        """A coherent SQLite event hash chain is not an epoch-transition proof."""
        # Shrink the epoch solely in this disposable test fixture: runtime,
        # typed sense and read-only inspector must agree on its pinned wire.
        with (patch("mojum.aquarium.EPOCH_TICKS", 2),
              patch("mojum.aquarium_wire.EPOCH_TICKS", 2),
              patch("mojum.epoch_sense_v2.EPOCH_TICKS", 2),
              patch("mojum.cell_policy_v4.EPOCH_TICKS", 2)):
            with self.open() as aquarium:
                aquarium.step()
                aquarium.step()
                self.assertEqual(aquarium.tick, 2)
                # Forge an internally consistent next frame that improperly
                # skips its durable epoch event. No SQL or SHA corruption.
                aquarium.epoch_index = 1
                aquarium.step()
                self.assertEqual(aquarium.stored_events, 4)
            with self.assertRaisesRegex(NoosphereError, "missing durable epoch"):
                inspect_journal(self.db)

    def test_real_sqlite_boundary_marker_replays_after_interrupted_frame(self):
        with self.open() as aquarium:
            for _ in range(EPOCH_TICKS):
                aquarium.step()
            self.assertEqual((aquarium.tick, aquarium.epoch_index), (EPOCH_TICKS, 0))
            self.assertEqual(aquarium.required_events_for_steps(1), 2)
            before = aquarium.checkpoint()
            with patch.object(
                    aquarium, "_calculate_frame",
                    side_effect=RuntimeError("simulated interruption after marker")):
                with self.assertRaisesRegex(RuntimeError, "after marker"):
                    aquarium.step()
            self.assertEqual((aquarium.tick, aquarium.epoch_index), (EPOCH_TICKS, 1))
            self.assertEqual(aquarium.stored_events, EPOCH_TICKS + 2)
            marker = next(
                entry["event"]
                for entry in aquarium.store.iter_events(include_nonpublic=True)
                if entry["event"]["topic"] == "aquarium.epoch"
            )
            self.assertEqual(marker["topic"], "aquarium.epoch")
            self.assertEqual(marker["payload"]["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(marker["payload"]["epoch_index"], 1)
            after_marker = aquarium.checkpoint()

        with self.open() as resumed:
            self.assertEqual(resumed.checkpoint(), after_marker)
            for _ in range(51):
                resumed.step()
            final = resumed.checkpoint()
            self.assertEqual((resumed.tick, resumed.epoch_index), (10_050, 1))
            self.assertEqual(resumed.stored_events, 10_052)

        report = inspect_journal(self.db)
        self.assertEqual(report["format"], V4_MODE)
        self.assertEqual(report["frames"], 10_050)
        self.assertEqual(report["events"], 10_052)
        self.assertEqual(report["epoch_index"], 1)

        before = self.db.read_bytes()
        with self.assertRaisesRegex(AquariumError, "mode mismatch"):
            Aquarium(self.db, SEED, GRAPH, neural_mode="disabled", motor_mode=V3_MODE)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(final["tick"], 10_050)


if __name__ == "__main__":
    unittest.main()
