"""Current-main epoch continuation: typed boundary, retention, and SQLite replay."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.cell_policy_v3 import CellExplorerV3
from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error, SCHEMA as V4_SCHEMA
from mojum.epoch_sense_v2 import (
    EPOCH_TICKS, EpochProprioception, EpochSenseError,
    SCHEMA as EPOCH_SCHEMA,
)
from mojum.local_cell_sense import Proprioception
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class EpochSenseTests(unittest.TestCase):
    def test_boundary_is_absolute_and_epoch_local(self):
        for tick, epoch, offset in ((0, 0, 0), (9998, 0, 9998),
                                    (9999, 1, 0), (10000, 1, 1)):
            with self.subTest(tick=tick):
                sense = EpochProprioception(
                    tick, epoch, offset, "a" * 64, .16, .16, False,
                )
                self.assertEqual(sense.tick, tick)
                self.assertEqual(sense.public()["schema"], EPOCH_SCHEMA)
                self.assertEqual(sense.public()["epoch_tick"], offset)

        for values in ((9999, 0, 0), (9999, 1, 1), (9998, 1, 9998),
                       (0, True, 0), (0, 0, False), (-1, 0, 0)):
            with self.subTest(values=values), self.assertRaises(EpochSenseError):
                EpochProprioception(*values, "a" * 64, .16, .16, False)

    def test_v1_proprioception_remains_bounded(self):
        with self.assertRaises(ValueError):
            Proprioception(EPOCH_TICKS, "f" * 64, .16, .16, False)


class CellV4Tests(unittest.TestCase):
    def test_choices_match_v3_before_boundary_and_snapshot_is_distinct(self):
        old, new = CellExplorerV3(), CellExplorerV4()
        for tick in range(120):
            old_sense = Proprioception(tick, "0" * 64, .16, .16, tick % 2 == 1)
            new_sense = EpochProprioception(
                tick, 0, tick, "0" * 64, .16, .16, tick % 2 == 1,
            )
            old_intent, new_intent = old.decide(old_sense), new.decide(new_sense)
            self.assertEqual(old_intent["action"], new_intent["action"])
            blocked = tick % 3 == 0 and new_intent["action"] != "rest"
            old.observe(old_sense, old_intent, blocked)
            new.observe(new_sense, new_intent, blocked)
        self.assertEqual(old.records, new.records)
        self.assertEqual(new.snapshot()["schema"], V4_SCHEMA)
        self.assertEqual(CellExplorerV4.from_snapshot(new.snapshot()).snapshot(),
                         new.snapshot())

    def test_snapshot_rejects_v3_schema_without_mutating_input(self):
        model = CellExplorerV4()
        sense = EpochProprioception(0, 0, 0, "0" * 64, .16, .16, False)
        model.observe(sense, model.decide(sense), False)
        snapshot = model.snapshot()
        forged = deepcopy(snapshot)
        forged["schema"] = "mojum.developmental.local-cell/3"
        with self.assertRaises(CellV4Error):
            CellExplorerV4.from_snapshot(forged)
        self.assertEqual(model.snapshot(), snapshot)


class EpochSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "epoch-v4.sqlite3"

    def open(self):
        return Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled", motor_mode=V4_MODE,
        )

    def test_rejected_external_motor_input_cannot_append_epoch_marker(self):
        """Validation precedes durable epoch transition even at a boundary."""
        with self.open() as aquarium:
            old_head, old_count = aquarium.store.head(), aquarium.store.count()
            with patch.object(aquarium, "_epoch_event",
                              side_effect=AssertionError("invalid input reached epoch marker")):
                for request in ({"dx": 0.01}, {"dy": -0.01}):
                    with self.subTest(request=request):
                        with self.assertRaisesRegex(AquariumError, "rejects external"):
                            aquarium.step(**request)
            self.assertEqual(aquarium.store.head(), old_head)
            self.assertEqual(aquarium.store.count(), old_count)
            self.assertEqual(aquarium.tick, 0)

    def test_durable_boundary_recovery_and_10050_tick_cold_replay(self):
        with self.open() as aquarium:
            for _ in range(EPOCH_TICKS):
                aquarium.step()
            before = aquarium.checkpoint()
            with patch.object(
                aquarium, "_calculate_frame",
                side_effect=RuntimeError("simulated interruption before epoch frame"),
            ):
                with self.assertRaisesRegex(RuntimeError, "epoch frame"):
                    aquarium.step()

            self.assertEqual(aquarium.tick, EPOCH_TICKS)
            self.assertEqual(aquarium.epoch_index, 1)
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 2)
            marker_stream = aquarium.store.iter_events(
                include_nonpublic=True, topic="aquarium.epoch",
            )
            try:
                marker = next(marker_stream)["event"]
            finally:
                marker_stream.close()
            self.assertEqual(marker["payload"]["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(marker["event_id"], "epoch-00000001")
            self.assertNotEqual(aquarium.checkpoint()["journal_head"],
                                before["journal_head"])

            aquarium.step()
            for _ in range(50):
                aquarium.step()
            expected = aquarium.checkpoint()
            self.assertEqual(expected["tick"], 10050)
            self.assertEqual(expected["epoch_index"], 1)
            self.assertEqual(expected["epoch_tick"], 51)
            self.assertEqual(aquarium.store.count(), 10052)

        with self.open() as restored:
            self.assertEqual(restored.checkpoint(), expected)
            self.assertEqual(inspect_journal(self.db)["format"], V4_MODE)
            self.assertEqual(inspect_journal(self.db)["frames"], 10050)
            restored.step()
            self.assertEqual(restored.tick, 10051)

    def test_wire_inspector_rejects_missing_epoch_marker(self):
        # Exercise the boundary validator with a shorter synthetic threshold.
        # SQLite remains a genuine canonical 3-frame v4 event/hash chain;
        # only the read-only checker threshold is reduced for this test.
        with self.open() as aquarium:
            for _ in range(3):
                aquarium.step()
        with patch("mojum.aquarium_wire.EPOCH_TICKS", 2):
            with self.assertRaisesRegex(NoosphereError, "missing durable epoch marker"):
                inspect_journal(self.db)

    def test_v4_rejects_sensory_upgrade_before_creating_database(self):
        with self.assertRaisesRegex(AquariumError, "sensory_mode=none"):
            Aquarium(
                self.db, SEED, GRAPH, neural_mode="disabled", motor_mode=V4_MODE,
                sensory_mode="toy_probe",
            )
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
