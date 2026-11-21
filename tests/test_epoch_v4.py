"""R0.15 v4 epoch continuation on the current motor-mode ABI.

These tests exercise only disposable CPU/synthetic journals. They do not
claim a neural brain, physical actuation, Android liveness or indefinite run.
"""

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, V3_MODE, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.cell_policy_v3 import CellExplorerV3
from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error, MAX_CELLS
from mojum.epoch_sense_v2 import (
    EPOCH_TICKS, EpochProprioception, EpochSenseError,
    MAX_ABSOLUTE_TICKS, SCHEMA as EPOCH_SENSE_SCHEMA,
)
from mojum.local_cell_sense import Proprioception
from mojum.portable_host import run_paced


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class EpochSenseTests(unittest.TestCase):
    def sense(self, tick):
        return EpochProprioception(
            tick, tick // EPOCH_TICKS, tick % EPOCH_TICKS,
            "a" * 64, .16, .16, bool(tick % 2),
        )

    def test_boundary_is_absolute_and_v1_remains_bounded(self):
        for tick, epoch, offset in (
            (0, 0, 0), (EPOCH_TICKS - 1, 0, EPOCH_TICKS - 1),
            (EPOCH_TICKS, 1, 0), (EPOCH_TICKS + 1, 1, 1),
        ):
            with self.subTest(tick=tick):
                cue = self.sense(tick)
                self.assertEqual((cue.epoch_index, cue.epoch_tick), (epoch, offset))
                self.assertEqual(cue.tick, tick)
                self.assertEqual(cue.public()["schema"], EPOCH_SENSE_SCHEMA)
        for values in (
            (EPOCH_TICKS, 0, 0), (EPOCH_TICKS - 1, 1, EPOCH_TICKS - 1),
            (0, True, 0), (0, 0, False), (-1, 0, 0),
            (MAX_ABSOLUTE_TICKS, 10, 9),
        ):
            with self.subTest(values=values), self.assertRaises(EpochSenseError):
                EpochProprioception(*values, "a" * 64, .16, .16, False)

    def test_gigantic_coordinates_are_typed_rejections(self):
        with self.assertRaises(EpochSenseError):
            self.sense(0).__class__(0, 0, 0, "a" * 64, 10**1000, .16, False)
        with self.assertRaises(EpochSenseError):
            self.sense(0).__class__(0, 0, 0, "a" * 64, .16, 10**1000, False)

    def test_v4_preserves_v3_choices_before_first_epoch(self):
        old, new = CellExplorerV3(), CellExplorerV4()
        for tick in range(170):
            old_sense = Proprioception(tick, "0" * 64, .16, .16, tick % 2 == 1)
            new_sense = self.sense(tick)
            old_intent, new_intent = old.decide(old_sense), new.decide(new_sense)
            self.assertEqual(
                (old_intent["action"], old_intent["dx"], old_intent["dy"]),
                (new_intent["action"], new_intent["dx"], new_intent["dy"]),
            )
            old.observe(old_sense, old_intent, False)
            new.observe(new_sense, new_intent, False)
        self.assertEqual(old.records, new.records)
        self.assertEqual(new.snapshot()["last_epoch_index"], 0)

    def test_v4_retention_is_bounded_and_snapshot_strict(self):
        model = CellExplorerV4()
        for tick in range(MAX_CELLS + 80):
            sense = EpochProprioception(
                tick, 0, tick, "0" * 64, (tick + 1) * .04, 0.0, False,
            )
            model.observe(sense, model.decide(sense), False)
        snapshot = model.snapshot()
        self.assertEqual(len(model.records), MAX_CELLS)
        self.assertEqual(model.evictions, 80)
        self.assertEqual(model.forgotten_observations, 80)
        self.assertEqual(CellExplorerV4.from_snapshot(snapshot).snapshot(), snapshot)
        forged = deepcopy(snapshot)
        forged["schema"] = "mojum.developmental.local-cell/3"
        with self.assertRaises(CellV4Error):
            CellExplorerV4.from_snapshot(forged)


    def test_v4_rejects_impossible_snapshot_block_counts(self):
        model = CellExplorerV4()
        for tick in range(40):
            sense = self.sense(tick)
            model.observe(sense, model.decide(sense), False)
        snapshot = model.snapshot()
        self.assertEqual(CellExplorerV4.from_snapshot(snapshot).snapshot(), snapshot)
        zone = next(iter(snapshot["records"]))
        actions = snapshot["records"][zone]
        move = next(
            action for action, counts in actions.items()
            if action != "rest" and counts[0] >= 2
        )
        self.assertIn("rest", actions)

        forged = deepcopy(snapshot)
        forged["records"][zone][move][1] = 2
        with self.assertRaisesRegex(CellV4Error, "measured counters"):
            CellExplorerV4.from_snapshot(forged)

        forged = deepcopy(snapshot)
        forged["records"][zone]["rest"][1] = 1
        with self.assertRaisesRegex(CellV4Error, "measured counters"):
            CellExplorerV4.from_snapshot(forged)


class EpochJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "v4.sqlite3"

    def open(self):
        return Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled", motor_mode=V4_MODE,
        )

    def test_v4_exact_100k_event_frame_ceiling_preflight_is_nonmutating(self):
        # No 100k writes required: verify the precise bootstrap + boundary math.
        from mojum.aquarium import MAX_V4_FRAMES, MAX_REPLAY_EVENTS
        self.assertEqual(MAX_V4_FRAMES, 99_990)
        with self.open() as q:
            admitted = q.required_events_for_steps(MAX_V4_FRAMES)
            self.assertEqual(1 + admitted, MAX_REPLAY_EVENTS)
            self.assertEqual(admitted, MAX_V4_FRAMES + 9)
            with self.assertRaisesRegex(AquariumError, "source event budget"):
                q.required_events_for_steps(MAX_V4_FRAMES + 1)
            self.assertEqual(q.stored_events, 1)
            self.assertEqual(q.store.count(), 1)

    def test_external_motor_args_cannot_commit_epoch_marker(self):
        """Invalid API input must fail before a pending epoch marker can write."""
        with self.open() as aquarium:
            previous_head = aquarium.store.head()
            previous_count = aquarium.store.count()
            with patch.object(
                aquarium, "_commit_pending_epoch",
                side_effect=AssertionError("invalid input reached epoch append"),
            ):
                for kwargs in ({"dx": 0.01}, {"dy": -0.01},
                               {"dx": 0.01, "dy": 0.0}):
                    with self.subTest(kwargs=kwargs):
                        with self.assertRaisesRegex(AquariumError, "rejects external"):
                            aquarium.step(**kwargs)
            self.assertEqual(aquarium.store.head(), previous_head)
            self.assertEqual(aquarium.store.count(), previous_count)
            self.assertEqual(aquarium.tick, 0)

    def test_v4_is_explicit_and_rejects_toy_probe_before_birth(self):
        with self.assertRaisesRegex(AquariumError, "sensory_mode=none"):
            Aquarium(
                self.db, SEED, GRAPH, motor_mode=V4_MODE,
                sensory_mode="toy_probe",
            )
        self.assertFalse(self.db.exists())
        with self.open() as q:
            self.assertEqual(q.manifest["developmental_policy"], V4_MODE)
            self.assertEqual(q.manifest["epoch_ticks"], EPOCH_TICKS)
            q.step()
            checkpoint = q.checkpoint()
            self.assertEqual(checkpoint["schema"], "mojum.aquarium.checkpoint/2")
            self.assertEqual(checkpoint["epoch_index"], 0)
        before = self.db.read_bytes()
        with self.assertRaises((AquariumError, ValueError)):
            with Aquarium(self.db, SEED, GRAPH, neural_mode="disabled", motor_mode=V3_MODE):
                pass
        self.assertEqual(self.db.read_bytes(), before)

    def test_wire_inspector_reports_the_versioned_v4_contract(self):
        with self.open() as q:
            q.step()
            report = inspect_journal(self.db)
        self.assertEqual(report["format"], V4_MODE)
        self.assertEqual(report["state_schema"], "mojum.developmental.local-cell/4")
        self.assertEqual(report["frames"], 1)
        self.assertEqual(report["epoch_markers"], 0)

    def test_wire_inspector_rejects_frame_after_epoch_without_marker(self):
        """Even a rehashed wire stream must not omit the durable boundary."""
        from mojum.noosphere import NoosphereError

        with self.open() as q:
            owner, manifest = q.agent_id, deepcopy(q.manifest)

        def fake_verify(_db, _owner, *, inspect_event, **_kwargs):
            envelope = {
                "source": "mojum.aquarium.fixture",
                "visibility": "PRIVATE",
                "agent_id": owner,
            }
            inspect_event(1, dict(
                envelope, topic="aquarium.bootstrap",
                event_id="aquarium-bootstrap", payload=manifest,
            ))
            for tick in range(EPOCH_TICKS + 1):
                inspect_event(tick + 2, dict(
                    envelope, topic="aquarium.frame",
                    event_id=f"frame-{tick:012d}", payload={"tick": tick},
                ))
            return {"count": EPOCH_TICKS + 2, "head": "0" * 64}

        # Isolate the marker state machine; actual frame payload and SHA-chain
        # integrity are tested by the separate real-SQLite cold-replay case.
        with patch("mojum.aquarium_wire._verify_chain", side_effect=fake_verify), \
             patch("mojum.aquarium_wire._check_policy_frame"):
            with self.assertRaisesRegex(NoosphereError, "missing durable epoch"):
                inspect_journal(self.db)

    def test_paced_host_accepts_v4_without_toy_sensor(self):
        now = [0.0]

        def wait(delay):
            now[0] += delay

        with self.open() as q:
            completed = run_paced(
                q, ticks=2, interval_ms=20,
                stop_event=threading.Event(), monotonic=lambda: now[0], wait=wait,
            )
            self.assertEqual(completed, 2)
            self.assertEqual(q.tick, 2)

    def test_real_sqlite_boundary_marker_and_cold_replay_past_9999(self):
        with self.open() as q:
            for _ in range(EPOCH_TICKS):
                q.step()
            self.assertEqual((q.tick, q.epoch_index, q.store.count()),
                             (EPOCH_TICKS, 0, EPOCH_TICKS + 1))
            self.assertEqual(q.required_events_for_steps(1), 2)
            with patch.object(q, "_calculate_frame",
                              side_effect=RuntimeError("before boundary frame")):
                with self.assertRaisesRegex(RuntimeError, "before boundary frame"):
                    q.step()
            self.assertEqual((q.tick, q.epoch_index, q.store.count()),
                             (EPOCH_TICKS, 1, EPOCH_TICKS + 2))
            with closing(sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro",
                                         uri=True)) as connection:
                wire = connection.execute(
                    "SELECT wire_json FROM events WHERE seq = ?",
                    (EPOCH_TICKS + 2,),
                ).fetchone()[0]
            marker = json.loads(wire)
            self.assertEqual(marker["topic"], "aquarium.epoch")
            self.assertEqual(marker["payload"]["absolute_tick"], EPOCH_TICKS)
            after_marker = q.checkpoint()

        with self.open() as q:
            self.assertEqual(q.checkpoint(), after_marker)
            self.assertEqual(q.step()["developmental"]["observation"]["absolute_tick"],
                             EPOCH_TICKS)
            for _ in range(50):
                q.step()
            expected = q.checkpoint()
            self.assertEqual((q.tick, q.epoch_index), (EPOCH_TICKS + 51, 1))
            self.assertEqual(q.store.count(), EPOCH_TICKS + 53)

        with self.open() as q:
            self.assertEqual(q.checkpoint(), expected)
            self.assertEqual(q.store.verify_chain()["head"], expected["journal_head"])


if __name__ == "__main__":
    unittest.main()
