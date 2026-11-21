"""Independent R0.14 v2 fault-injection and capacity QA (synthetic only).

All fault injection stays inside per-test temporary SQLite files; no real
Noosphere, physical device, remote host or private production records.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.cell_policy_v2 import (
    CellExplorer, LocalCellError, MAX_CELLS, SCHEMA as CELL_SCHEMA,
)
from mojum.developmental_policy import Proprioception, STEP_METERS
from mojum.noosphere import NoosphereError
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class LocalCellReplayFaultTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "synthetic-local-cell.sqlite3"

    def open(self, db=None, **kwargs):
        return Aquarium(
            db if db is not None else self.db,
            SEED, GRAPH, room=Room(.16, .16), start=(.16, .16),
            exploration_mode="goal_free", developmental_policy="local_cell_v2",
            **kwargs,
        )

    def test_write_failure_before_commit_preserves_ram_and_replay(self):
        with self.open() as a:
            before = a.checkpoint()
            count = a.store.count()
            with patch.object(a.store, "append", side_effect=OSError(
                    "fault injection before SQLite CAS")):
                with self.assertRaisesRegex(OSError, "before SQLite"):
                    a.step()
            self.assertEqual(a.checkpoint(), before)
            self.assertEqual(a.store.count(), count)
            self.assertEqual(a.tick, 0)
            self.assertEqual(a.explorer.snapshot(),
                             before["developmental_state"])
        with self.open() as reopened:
            self.assertEqual(reopened.checkpoint(), before)
            self.assertEqual(reopened.store.count(), count)
            next_frame = reopened.step()
            self.assertEqual(next_frame["developmental"]["schema"],
                             CELL_SCHEMA)

    def test_commit_then_ram_crash_recovers_exactly_once(self):
        with self.open() as a:
            before = a.checkpoint()
            existing_append = a.store.append

            def commit_then_fail(event, *, expected_head):
                result = existing_append(event, expected_head=expected_head)
                self.assertEqual(result, a.store.head())
                raise RuntimeError("fault injection after durable commit")

            with patch.object(a.store, "append", side_effect=commit_then_fail):
                with self.assertRaisesRegex(RuntimeError, "durable commit"):
                    a.step()
            self.assertEqual(a.tick, 0)
            self.assertEqual(a.world.revision, before["world_revision"])
            self.assertEqual(a.explorer.snapshot(),
                             before["developmental_state"])
            self.assertEqual(a.learner.snapshot(), before["neural_state"])
            self.assertEqual(a.store.count(), 2)
            durable_head = a.store.head()
            self.assertNotEqual(durable_head, before["journal_head"])
            with self.assertRaisesRegex(AquariumError,
                                        "stale or unadopted"):
                a.checkpoint()
        with self.open() as recovered:
            self.assertEqual(recovered.store.count(), 2)
            self.assertEqual(recovered.tick, 1)
            self.assertEqual(recovered.explorer.updates, 1)
            self.assertEqual(recovered.checkpoint()["journal_head"],
                             durable_head)
            recovered.step()
            after_resume = recovered.checkpoint()
            self.assertEqual(recovered.tick, 2)
        with self.open() as reopened:
            self.assertEqual(reopened.checkpoint(), after_resume)

    def test_two_writers_cas_stale_refuses_policy_ram_adoption(self):
        with self.open() as writer, self.open() as stale:
            saved = stale.checkpoint()
            writer.step()
            durable = writer.checkpoint()["journal_head"]
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.tick, 0)
            self.assertEqual(stale.world.revision, saved["world_revision"])
            self.assertEqual(stale.explorer.snapshot(),
                             saved["developmental_state"])
            self.assertEqual(stale.learner.snapshot(),
                             saved["neural_state"])
            self.assertEqual(stale.store.count(), 2)
            self.assertEqual(stale.store.head(), durable)
            with self.assertRaisesRegex(AquariumError,
                                        "stale or unadopted"):
                stale.checkpoint()
        with self.open() as recovery:
            self.assertEqual(recovery.tick, 1)
            self.assertEqual(recovery.checkpoint()["journal_head"], durable)
            recovery.step()

    def test_corrupt_event_rejected_and_external_anchor_checked(self):
        with self.open() as a:
            a.step()
            a.step()
            authenticated_only_by_chain = a.store.head()
            old_count = a.store.count()
        with self.assertRaisesRegex(NoosphereError,
                                    "independent external anchor"):
            with self.open(external_expected_head="0" * 64):
                pass
        with self.open(external_expected_head=authenticated_only_by_chain) as a:
            self.assertEqual(a.store.count(), old_count)

        # Corrupt exactly one synthetic fixture in a disposable DB; the
        # database is intentionally NOT a real user history. Disable the
        # append-only trigger in this test to imitate disk-level tampering.
        with sqlite3.connect(self.db) as db:
            wire = db.execute(
                "SELECT wire_json FROM events WHERE seq = 2"
            ).fetchone()[0]
            event = json.loads(wire)
            event["payload"]["developmental"]["learning"]["source"] = (
                "tampered-synthetic-data"
            )
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute(
                "UPDATE events SET wire_json = ? WHERE seq = 2",
                (json.dumps(event, ensure_ascii=False,
                            separators=(",", ":"), sort_keys=True),),
            )
        with self.assertRaisesRegex(NoosphereError,
                                    "hash chain mismatch"):
            with self.open():
                pass
        with sqlite3.connect(self.db) as db:
            actual = db.execute(
                "SELECT wire_json FROM events WHERE seq = 2"
            ).fetchone()[0]
            self.assertIn("tampered-synthetic-data", actual)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM events").fetchone()[0], old_count)

    def test_two_clean_sqlite_organisms_produce_equal_causal_frames(self):
        second = self.db.with_name("second-synthetic.sqlite3")
        with self.open() as a, self.open(second) as b:
            for _ in range(64):
                self.assertEqual(a.step(), b.step())
            self.assertEqual(a.explorer.snapshot(), b.explorer.snapshot())
            self.assertEqual(a.checkpoint(), b.checkpoint())
        with self.open() as a, self.open(second) as b:
            self.assertEqual(a.step(), b.step())
            self.assertEqual(a.checkpoint(), b.checkpoint())


class LocalCellQuotaTests(unittest.TestCase):
    def test_512_unique_cells_exhaustion_is_atomic_and_explicit(self):
        policy = CellExplorer()
        revision = "0" * 64
        for tick in range(MAX_CELLS):
            pos = (tick + 1) * STEP_METERS
            sense = Proprioception(tick, revision, pos, 0.0, False)
            intent = policy.decide(sense)
            learned = policy.observe(sense, intent, False)
            self.assertEqual(learned["total_updates"], tick + 1)
        snapshot = policy.snapshot()
        self.assertEqual(len(snapshot["records"]), MAX_CELLS)
        self.assertEqual(
            CellExplorer.from_snapshot(snapshot).snapshot(), snapshot
        )
        sense = Proprioception(
            MAX_CELLS, revision,
            (MAX_CELLS + 1) * STEP_METERS, 0.0, False,
        )
        intended = policy.decide(sense)
        with self.assertRaisesRegex(LocalCellError,
                                    "capacity exhausted"):
            policy.observe(sense, intended, False)
        self.assertEqual(policy.snapshot(), snapshot)
        self.assertEqual(policy.updates, MAX_CELLS)
        # Known LONG-RUN BLOCKER, never report v2 as always-on yet.

    def test_model_snapshot_is_defensive_copy(self):
        m = CellExplorer()
        revision = "0" * 64
        cue = Proprioception(0, revision, .16, .16, True)
        m.observe(cue, m.decide(cue), True)
        original = m.snapshot()
        alias = m.snapshot()
        for rows in alias["records"].values():
            for counters in rows.values():
                counters[0] = -1
        self.assertEqual(m.snapshot(), original)
        clone = CellExplorer.from_snapshot(original)
        clone.records.clear()
        self.assertEqual(m.snapshot(), original)


if __name__ == "__main__":
    unittest.main()
