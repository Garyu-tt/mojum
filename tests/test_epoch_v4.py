"""R0.15 v4 explicit epoch/absolute-time state and SQLite boundary QA.

Pure CPU synthetic point-room only. Real private user journals, physical
actuators, Android devices, production HMAC keys and server state untouched.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.cell_policy_v3 import CellExplorerV3
from mojum.cell_policy_v4 import (
    CellExplorerV4, CellV4Error, MAX_CELLS,
    SCHEMA as CELL_V4_SCHEMA, LEARNING_SCHEMA as LEARNING_V4_SCHEMA,
)
from mojum.developmental_policy import Proprioception
from mojum.epoch_sense_v2 import (
    EpochProprioception, EpochSenseError, EPOCH_TICKS,
    MAX_ABSOLUTE_TICKS, SCHEMA as SENSE_V2_SCHEMA,
)
from mojum.portable_host import PortableHostError, run_paced
from mojum.verified_snapshot import create_key_file, load_snapshot
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class V4TypedEpochSenseQA(unittest.TestCase):
    def sense(self, tick):
        return EpochProprioception(
            tick, tick // EPOCH_TICKS, tick % EPOCH_TICKS,
            "a" * 64, .16, .16, bool(tick % 2),
        )

    def test_exact_v4_boundary_and_invalid_epoch_claims(self):
        for tick, epoch, offset in (
            (0, 0, 0), (9998, 0, 9998), (9999, 1, 0),
            (10000, 1, 1), (19998, 2, 0),
        ):
            with self.subTest(tick=tick):
                cue = self.sense(tick)
                self.assertEqual((cue.epoch_index, cue.epoch_tick),
                                 (epoch, offset))
                self.assertEqual(cue.public()["absolute_tick"], tick)
                self.assertEqual(cue.public()["schema"], SENSE_V2_SCHEMA)
                self.assertEqual(cue.tick, tick)
        for values in (
            (9999, 0, 0), (9999, 1, 1), (9998, 1, 9998),
            (0, True, 0), (0, 0, False),
            (-1, 0, 0), (MAX_ABSOLUTE_TICKS, 10, 9),
        ):
            with self.subTest(values=values), self.assertRaises(EpochSenseError):
                EpochProprioception(*values, "a"*64, .16, .16, False)

    def test_v4_matches_v3_motor_choices_before_first_epoch(self):
        old, new = CellExplorerV3(), CellExplorerV4()
        for tick in range(170):
            s3 = Proprioception(tick, "0"*64, .16, .16, tick % 2 == 1)
            s4 = EpochProprioception(
                tick, 0, tick, "0"*64, .16, .16, tick % 2 == 1,
            )
            a, b = old.decide(s3), new.decide(s4)
            self.assertEqual((a["action"], a["dx"], a["dy"]),
                             (b["action"], b["dx"], b["dy"]))
            old.observe(s3, a, tick % 3 == 0)
            new.observe(s4, b, tick % 3 == 0)
        self.assertEqual(old.records, new.records)
        self.assertEqual(old.evictions, new.evictions)
        self.assertEqual(new.snapshot()["last_epoch_index"], 0)
        self.assertEqual(CellExplorerV4.from_snapshot(new.snapshot()).snapshot(),
                         new.snapshot())

    def test_v4_retention_bounded_with_more_than_2400_absolute_ticks(self):
        m = CellExplorerV4()
        for tick in range(2400):
            # Deliberately injected synthetic positions. This is a memory
            # unit protocol, not an actual possible 2400-step trajectory.
            cue = EpochProprioception(
                tick, 0, tick, "0"*64, (tick + 1)*.04, 0., False,
            )
            m.observe(cue, m.decide(cue), False)
            self.assertLessEqual(len(m.records), MAX_CELLS)
        self.assertEqual(m.evictions, 2400 - MAX_CELLS)
        self.assertEqual(m.forgotten_observations, 2400 - MAX_CELLS)
        snapshot = m.snapshot()
        self.assertEqual(CellExplorerV4.from_snapshot(snapshot).snapshot(),
                         snapshot)
        for key, val in (
            ("last_epoch_index", True),
            ("last_epoch_index", 1),
            ("schema", "mojum.developmental.local-cell/3"),
            ("updates", True),
        ):
            with self.subTest(key=key, val=val):
                invalid = deepcopy(snapshot)
                invalid[key] = val
                with self.assertRaises(CellV4Error):
                    CellExplorerV4.from_snapshot(invalid)

    def test_proprioception_v1_still_rejects_old_episode_boundary(self):
        with self.assertRaises(ValueError):
            Proprioception(EPOCH_TICKS, "f"*64, .16, .16, False)


class V4EpochJournalIntegrationQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "synthetic-v4.sqlite3"
        self.sidecar = self.root / "owner-only.replay.json"
        self.key = self.root / "hmac.key"
        create_key_file(self.key)

    def open(self, policy="local_cell_v4", signed=True):
        options = {}
        if signed:
            options.update(
                replay_snapshot_file=self.sidecar,
                replay_snapshot_key_file=self.key,
            )
        return Aquarium(
            self.db, SEED, GRAPH, room=Room(.16, .16), start=(.16, .16),
            exploration_mode="goal_free", neural_mode="disabled",
            sensory_mode="none", developmental_policy=policy,
            **options,
        )

    def test_only_v4_new_journal_and_no_toy_probe_upgrade(self):
        with self.assertRaisesRegex(AquariumError, "sensory_mode=none"):
            Aquarium(
                self.db, SEED, GRAPH,
                exploration_mode="goal_free", sensory_mode="toy_probe",
                developmental_policy="local_cell_v4",
            )
        self.assertFalse(self.db.exists())
        with self.open() as q:
            self.assertEqual(q.manifest["developmental_policy"],
                             "local_cell_v4")
            self.assertEqual(q.manifest["epoch_ticks"], EPOCH_TICKS)
            self.assertEqual(q.epoch_index, 0)
            q.step()
            checkpoint = q.checkpoint()
            self.assertEqual(checkpoint["schema"],
                             "mojum.aquarium.checkpoint/2")
            self.assertEqual(checkpoint["epoch_index"], 0)
            self.assertEqual(checkpoint["developmental_state"]["schema"],
                             CELL_V4_SCHEMA)
        for incompatible in ("legacy", "local_cell_v2", "local_cell_v3"):
            with self.subTest(incompatible=incompatible):
                with self.assertRaises((AquariumError, ValueError)):
                    with self.open(incompatible):
                        pass
        with self.open() as q:
            self.assertEqual(q.checkpoint(), checkpoint)

    def test_real_10050_tick_sqlite_epoch_and_signed_crash_tail_replay(self):
        # This is a REAL canonical Aquarius journal of >10k synthetic
        # motor frames, not only an injected in-memory model loop.
        with self.open() as aquarium:
            for i in range(EPOCH_TICKS):
                payload = aquarium.step()
                if i in (0, EPOCH_TICKS - 1):
                    self.assertEqual(payload["tick"], i)
                    self.assertEqual(payload["developmental"]["observation"]
                                     ["epoch_index"], 0)
            self.assertEqual(aquarium.tick, EPOCH_TICKS)
            self.assertEqual(aquarium.epoch_index, 0)
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 1)
            self.assertEqual(aquarium.required_events_for_steps(1), 2)
            before = aquarium.checkpoint()
            aquarium.save_replay_snapshot()
            # Simulated post-marker interruption: the marker is fully
            # committed but the first epoch-1 frame has not been written.
            with patch.object(aquarium, "_calculate",
                              side_effect=RuntimeError("before boundary frame")):
                with self.assertRaisesRegex(RuntimeError, "boundary frame"):
                    aquarium.step()
            self.assertEqual(aquarium.tick, EPOCH_TICKS)
            self.assertEqual(aquarium.epoch_index, 1)
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 2)
            marked = aquarium.store.event_at(EPOCH_TICKS + 2)["event"]
            self.assertEqual(marked["topic"], "aquarium.epoch")
            self.assertEqual(marked["event_id"], "epoch-00000001")
            self.assertEqual(marked["payload"]["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(marked["payload"]["epoch_index"], 1)
            self.assertEqual(marked["observed_at_ns"],
                             EPOCH_TICKS * aquarium.dt_ns)
            self.assertEqual(aquarium.required_events_for_steps(1), 1)
            after_marker = aquarium.checkpoint()
            self.assertNotEqual(before["journal_head"],
                                after_marker["journal_head"])
            self.assertEqual(after_marker["tick"], EPOCH_TICKS)
            self.assertEqual(after_marker["epoch_index"], 1)

        # Restart uses HMAC prefix at tick 9999 and full-verified tail
        # containing ONLY the one epoch marker, not an invented reset.
        with self.open() as restart:
            self.assertEqual(restart.checkpoint(), after_marker)
            self.assertEqual(restart.epoch_index, 1)
            # A signed cache created while a boundary is committed but its
            # first motor frame is still pending must restore that exact
            # half-step without assuming the frame was already replayed.
            restart.save_replay_snapshot()
        with self.open() as restart:
            self.assertEqual(restart.checkpoint(), after_marker)
            self.assertEqual(restart.epoch_index, 1)
            first = restart.step()
            self.assertEqual(first["tick"], EPOCH_TICKS)
            self.assertEqual(first["time_ns"],
                             EPOCH_TICKS * restart.dt_ns)
            self.assertEqual(first["interoception"]["observed_at_ns"],
                             first["time_ns"])
            self.assertEqual(first["developmental"]["observation"]
                             ["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(first["developmental"]["observation"]
                             ["epoch_tick"], 0)
            self.assertEqual(first["developmental"]["learning"]["schema"],
                             LEARNING_V4_SCHEMA)
            self.assertEqual(restart.tick, EPOCH_TICKS + 1)
            for _ in range(50):
                restart.step()
            expected = restart.checkpoint()
            self.assertEqual(restart.tick, EPOCH_TICKS + 51)
            self.assertEqual(restart.epoch_index, 1)
            self.assertEqual(restart.explorer.updates, EPOCH_TICKS + 51)
            self.assertEqual(expected["developmental_state"]["last_tick"],
                             EPOCH_TICKS + 50)
            self.assertEqual(restart.store.count(), EPOCH_TICKS + 53)
            self.assertEqual(expected["epoch_tick"], 51)
            restart.save_replay_snapshot()

        # Signed exact-head restore must preserve all derived state, not
        # merely the database row count.
        with self.open() as signed:
            self.assertEqual(signed.checkpoint(), expected)
            self.assertEqual(signed.store.count(), EPOCH_TICKS + 53)
            signed_state = load_snapshot(self.sidecar, self.key)
            self.assertEqual(signed_state["schema"],
                             "mojum.aquarium.replay-state/2")
            self.assertEqual(signed_state["epoch_index"], 1)
        # Full cold replay scans and re-executes all >10k stored events,
        # providing an independent oracle against the signed fast path.
        with self.open(signed=False) as fully_replayed:
            self.assertEqual(fully_replayed.checkpoint(), expected)
            self.assertEqual(fully_replayed.epoch_index, 1)
            self.assertEqual(fully_replayed.store.verify_chain()["head"],
                             expected["journal_head"])

    def test_preflight_epoch_marker_accounting_and_legacy_boundary(self):
        with self.open() as q:
            self.assertEqual(q.required_events_for_steps(9999), 9999)
            self.assertEqual(q.required_events_for_steps(10000), 10001)
            self.assertEqual(q.required_events_for_steps(10050), 10051)
            self.assertEqual(q.required_events_for_steps(0), 0)
            with patch("mojum.portable_host.MAX_REPLAY_EVENTS", 10):
                with self.assertRaisesRegex(PortableHostError, "budget"):
                    run_paced(
                        q, ticks=10, interval_ms=20,
                        stop_event=threading.Event(),
                    )
            self.assertEqual(q.store.count(), 1)
            self.assertEqual(q.tick, 0)

    def test_forged_signed_epoch_claim_rejected_without_source_rewrite(self):
        with self.open() as q:
            for _ in range(25):
                q.step()
            original = q.checkpoint()
            q.save_replay_snapshot()
        from mojum.verified_snapshot import save_snapshot
        state = load_snapshot(self.sidecar, self.key)
        state["epoch_index"] = 1  # correctly HMAC-signed, incorrect model time
        save_snapshot(self.sidecar, self.key, state)
        with self.assertRaises(ValueError):
            with self.open():
                pass
        with self.open(signed=False) as q:
            self.assertEqual(q.checkpoint(), original)
            self.assertEqual(q.store.count(), 26)


if __name__ == "__main__":
    unittest.main()
