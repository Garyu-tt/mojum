"""Focused R0.15 v4 epoch and retention regression tests.

These tests use only disposable synthetic SQLite journals.  They verify the
new opt-in wire without changing the legacy v1/v2/v3 policy contracts.
"""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, EPOCH_EVENT_SCHEMA, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.cell_policy_v3 import CellExplorerV3
from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error, MAX_CELLS
from mojum.epoch_sense_v2 import (
    EPOCH_TICKS, EpochProprioception, EpochSenseError,
    MAX_ABSOLUTE_TICKS, SCHEMA as EPOCH_SENSE_SCHEMA,
)
from mojum.local_cell_sense import Proprioception
from mojum.noosphere import EMPTY_HEAD, NoosphereError, _sha


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples" / "genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class EpochSenseAndPolicyTests(unittest.TestCase):
    def sense(self, tick):
        return EpochProprioception(
            tick, tick // EPOCH_TICKS, tick % EPOCH_TICKS,
            "a" * 64, .16, .16, bool(tick % 2),
        )

    def test_absolute_epoch_boundary_is_explicit_and_old_sense_stays_bounded(self):
        for tick, epoch, local in (
                (0, 0, 0), (EPOCH_TICKS - 1, 0, EPOCH_TICKS - 1),
                (EPOCH_TICKS, 1, 0), (EPOCH_TICKS + 1, 1, 1)):
            cue = self.sense(tick)
            self.assertEqual((cue.epoch_index, cue.epoch_tick), (epoch, local))
            self.assertEqual(cue.public()["schema"], EPOCH_SENSE_SCHEMA)
            self.assertEqual(cue.public()["absolute_tick"], tick)
        with self.assertRaises(EpochSenseError):
            EpochProprioception(EPOCH_TICKS, 0, 0, "a" * 64, .16, .16, False)
        with self.assertRaises(EpochSenseError):
            EpochProprioception(0, 0, 0, "a" * 64, 10 ** 1000, .16, False)
        with self.assertRaises(ValueError):
            Proprioception(EPOCH_TICKS, "a" * 64, .16, .16, False)

    def test_v4_retention_is_bounded_and_replayable(self):
        policy = CellExplorerV4()
        for tick in range(MAX_CELLS + 88):
            cue = EpochProprioception(
                tick, 0, tick, "a" * 64, (tick + 1) * .04, .16, False,
            )
            policy.observe(cue, policy.decide(cue), False)
        self.assertEqual(len(policy.records), MAX_CELLS)
        self.assertEqual(policy.evictions, 88)
        self.assertEqual(policy.forgotten_observations, 88)
        snapshot = policy.snapshot()
        self.assertEqual(CellExplorerV4.from_snapshot(snapshot).snapshot(), snapshot)
        invalid = deepcopy(snapshot)
        invalid["schema"] = "mojum.developmental.local-cell/3"
        with self.assertRaises(CellV4Error):
            CellExplorerV4.from_snapshot(invalid)

    def test_v4_matches_v3_actions_before_epoch_boundary(self):
        old, new = CellExplorerV3(), CellExplorerV4()
        for tick in range(170):
            old_sense = Proprioception(tick, "a" * 64, .16, .16, bool(tick % 2))
            new_sense = self.sense(tick)
            old_intent = old.decide(old_sense)
            new_intent = new.decide(new_sense)
            self.assertEqual(
                (old_intent["action"], old_intent["dx"], old_intent["dy"]),
                (new_intent["action"], new_intent["dx"], new_intent["dy"]),
            )
            old.observe(old_sense, old_intent, tick % 3 == 0)
            new.observe(new_sense, new_intent, tick % 3 == 0)
        self.assertEqual(old.records, new.records)


class AquariumEpochV4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mojum-epoch-v4-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "v4.sqlite3"

    def open(self):
        return Aquarium(
            self.db, SEED, GRAPH, motor_mode=V4_MODE,
            neural_mode="disabled",
        )

    def test_v4_is_opt_in_and_wrong_version_fails_before_new_frame(self):
        with self.open() as aquarium:
            self.assertEqual(aquarium.manifest["developmental_policy"], V4_MODE)
            self.assertEqual(aquarium.manifest["epoch_transition_schema"], EPOCH_EVENT_SCHEMA)
            self.assertEqual(aquarium.checkpoint()["schema"], "mojum.aquarium.checkpoint/2")
            aquarium.step()
            before = aquarium.store.count()
        with self.assertRaises(AquariumError):
            with Aquarium(self.db, SEED, GRAPH, motor_mode="local_cell_v3",
                           neural_mode="disabled"):
                pass
        with Aquarium(self.db, SEED, GRAPH, motor_mode=V4_MODE,
                      neural_mode="disabled") as reopened:
            self.assertEqual(reopened.store.count(), before)
            self.assertEqual(reopened.tick, 1)

    def test_real_sqlite_crosses_9999_with_one_durable_epoch_marker(self):
        # 9999 actual committed motor frames, followed by an injected
        # post-marker/pre-frame interruption.  This is deliberately a real
        # SQLite replay boundary, not an in-memory counter-only test.
        with self.open() as aquarium:
            for _ in range(EPOCH_TICKS):
                aquarium.step()
            self.assertEqual(aquarium.tick, EPOCH_TICKS)
            self.assertEqual(aquarium.epoch_index, 0)
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 1)
            self.assertEqual(aquarium.required_events_for_steps(1), 2)
            with patch.object(
                    aquarium, "_calculate_frame",
                    side_effect=RuntimeError("interrupted before epoch frame")):
                with self.assertRaisesRegex(RuntimeError, "epoch frame"):
                    aquarium.step()
            pending = aquarium.checkpoint()
            self.assertEqual(pending["tick"], EPOCH_TICKS)
            self.assertEqual(pending["epoch_index"], 1)
            self.assertEqual(pending["epoch_tick"], 0)
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 2)
            marker = next(
                item["event"] for item in aquarium.store.iter_events(include_nonpublic=True)
                if item["seq"] == EPOCH_TICKS + 2
            )
            self.assertEqual(marker["topic"], "aquarium.epoch")
            self.assertEqual(marker["event_id"], "epoch-00000001")
            self.assertEqual(marker["payload"]["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(marker["payload"]["epoch_index"], 1)
            info = inspect_journal(self.db)
            self.assertEqual(
                (info["format"], info["frames"], info["events"]),
                (V4_MODE, EPOCH_TICKS, EPOCH_TICKS + 2),
            )

        # Cold replay sees the committed marker and leaves exactly one pending
        # first frame; it does not duplicate the transition.
        with self.open() as resumed:
            self.assertEqual(resumed.checkpoint(), pending)
            self.assertEqual(resumed.epoch_index, 1)
            first = resumed.step()
            self.assertEqual(first["tick"], EPOCH_TICKS)
            self.assertEqual(first["developmental"]["observation"]["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(first["developmental"]["observation"]["epoch_tick"], 0)
            for _ in range(50):
                resumed.step()
            final = resumed.checkpoint()
            self.assertEqual(resumed.tick, EPOCH_TICKS + 51)
            self.assertEqual(resumed.epoch_index, 1)
            self.assertEqual(resumed.store.count(), EPOCH_TICKS + 53)
            self.assertEqual(final["epoch_tick"], 51)
            resumed_marker = next(
                item["event"] for item in resumed.store.iter_events(include_nonpublic=True)
                if item["seq"] == EPOCH_TICKS + 2
            )
            self.assertEqual(resumed_marker, marker)

        with self.open() as replayed:
            self.assertEqual(replayed.checkpoint(), final)
            self.assertEqual(replayed.store.count(), EPOCH_TICKS + 53)

        # A rehashed SQLite chain is not sufficient evidence of a valid v4
        # journal. Remove the required boundary marker from a disposable copy,
        # close the sequence gap and recompute every event hash. The inspector
        # must reject the otherwise hash-consistent first epoch-1 frame.
        missing_marker = Path(self.tmp.name) / "missing_marker.sqlite3"
        with closing(sqlite3.connect(self.db)) as source, closing(sqlite3.connect(missing_marker)) as target:
            source.backup(target)
        with closing(sqlite3.connect(missing_marker)) as db:
            db.execute("DROP TRIGGER noosphere_no_delete")
            db.execute("DROP TRIGGER noosphere_no_update")
            self.assertEqual(db.execute(
                "DELETE FROM events WHERE topic = 'aquarium.epoch'"
            ).rowcount, 1)
            previous = EMPTY_HEAD
            events = db.execute("SELECT seq, wire_json FROM events ORDER BY seq").fetchall()
            for new_seq, (old_seq, raw) in enumerate(events, 1):
                digest = _sha({"previous": previous, "event": json.loads(raw)})
                # Temporarily negative sequence values avoid primary-key
                # conflicts while renumbering this disposable journal copy.
                db.execute(
                    "UPDATE events SET seq=?, prev_hash=?, event_hash=? WHERE seq=?",
                    (-new_seq, previous, digest, old_seq),
                )
                previous = digest
            db.execute("UPDATE events SET seq=-seq WHERE seq<0")
            db.commit()
        with self.assertRaisesRegex(NoosphereError, "missing durable v4 epoch marker"):
            inspect_journal(missing_marker)

    def test_v4_absolute_and_journal_caps_fail_closed_without_partial_frame(self):
        with self.open() as aquarium:
            with self.assertRaises(AquariumError):
                aquarium.required_events_for_steps(MAX_ABSOLUTE_TICKS + 1)
            self.assertEqual(aquarium.store.count(), 1)

    def test_epoch_budget_preflight_preserves_boundary_and_pending_marker_budget(self):
        with self.open() as aquarium:
            # Model the state immediately before the first post-boundary
            # frame without spending another 9,999-frame test run here.
            aquarium.tick = EPOCH_TICKS
            aquarium.epoch_index = 0
            self.assertEqual(aquarium.required_events_for_steps(1), 2)
            before = aquarium.checkpoint()

            # One free event slot cannot admit both the durable marker and its
            # first frame. The preflight must reject before appending either.
            aquarium._committed_count = aquarium.journal_event_budget - 1
            with self.assertRaisesRegex(AquariumError, "event budget"):
                aquarium.step()
            self.assertEqual(aquarium.store.count(), 1)
            self.assertEqual(aquarium.checkpoint(), before)

            # Commit an actual marker into the disposable journal, then model
            # its verified count immediately below the cap. The pending first
            # frame needs only one event and must not be charged for a
            # duplicate marker.
            marker = aquarium._epoch_event(1)
            head = aquarium._append_record(
                marker["topic"], marker["event_id"], marker["payload"],
                marker["observed_at_ns"], marker["source_revision"],
            )
            aquarium.epoch_index = 1
            aquarium._committed_count = aquarium.journal_event_budget - 1
            aquarium._committed_head = head
            self.assertEqual(aquarium.store.count(), 2)
            stored_marker = next(
                item["event"] for item in aquarium.store.iter_events(include_nonpublic=True)
                if item["seq"] == 2
            )
            self.assertEqual(stored_marker["event_id"], "epoch-00000001")
            self.assertEqual(aquarium.required_events_for_steps(1), 1)


if __name__ == "__main__":
    unittest.main()
