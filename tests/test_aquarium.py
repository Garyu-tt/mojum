"""Cross-component CPU-first tests; all data are synthetic fixtures."""

import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.noosphere import NoosphereError
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def gene_event(eid, tick, now_ns, delta=30):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": eid, "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm", "delta": delta,
        "valid_from_tick": tick, "observed_at_ns": 0,
        "recorded_at_ns": now_ns,
        "source": "synthetic-unit-test", "evidence": "not real learning",
    }


class AquariumIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "events.sqlite3"

    def aquarium(self, **kwargs):
        return Aquarium(self.db, SEED, GRAPH, **kwargs)

    def test_one_bootstrap_event_and_real_disk_file(self):
        with self.aquarium() as q:
            self.assertTrue(self.db.is_file())
            self.assertEqual(q.store.count(), 1)
            self.assertEqual(q.checkpoint()["tick"], 0)
        self.assertGreater(self.db.stat().st_size, 0)

    def test_single_step_synchronized_clock_and_world(self):
        with self.aquarium() as q:
            frame = q.step(dx=0.02)
            self.assertEqual(frame["tick"], 0)
            self.assertEqual(frame["time_ns"], 0)
            self.assertEqual(frame["world"]["tick"], 1)
            self.assertEqual(q.world.tick, 1)
            self.assertEqual(q.tick, 1)
            self.assertEqual(frame["interoception"]["observed_at_ns"], 0)
            self.assertEqual(q.store.count(), 2)

    def test_real_reopen_and_deterministic_replay(self):
        with self.aquarium() as q:
            for _ in range(42):
                q.step(dx=.025)
            expected = q.checkpoint()
        with self.aquarium() as new:
            self.assertEqual(new.checkpoint(), expected)
            self.assertEqual(new.tick, 42)
            self.assertLessEqual(new.world.x, 2.0)  # swept wall blocks the 41st crossing
            self.assertGreater(new.world.x, 1.9)
            frame = new.step(dx=.025)
            self.assertTrue(frame["world"]["blocked"])
            self.assertEqual(new.world.x, frame["world"]["position"][0])
            self.assertEqual(new.store.count(), 44)
            self.assertEqual(new.store.verify_chain()["count"], 44)

    def test_fresh_rerun_adds_events_not_duplicate_boot(self):
        with self.aquarium() as first:
            for _ in range(5):
                first.step()
        with self.aquarium() as second:
            for _ in range(5):
                second.step()
            self.assertEqual(second.tick, 10)
            self.assertEqual(second.store.count(), 11)

    def test_changes_in_heartbeat_without_external_input(self):
        with self.aquarium() as q:
            pulses = [q.step(dx=0)["interoception"]["heartbeat"]["pulse"] for _ in range(120)]
        self.assertIn(True, pulses)
        self.assertIn(False, pulses)

    def test_brain_is_explicitly_unimplemented(self):
        with self.aquarium() as q:
            frame = q.step()
            self.assertEqual(frame["brain"]["status"], "UNIMPLEMENTED")
            self.assertIsNone(frame["brain"]["action"])
            self.assertEqual(frame["physical_model"], "NONE_FIXTURE_ONLY")

    def test_world_contact_barrier_prevents_tunneling(self):
        with self.aquarium() as q:
            event = q.step(dx=2.0)
            self.assertTrue(event["world"]["blocked"])
            self.assertEqual(event["world"]["position"], [1.0, 2.0])
            self.assertFalse(event["world"]["physical_contact"])

    def test_sim_time_monotone_and_exact(self):
        with self.aquarium() as q:
            times = [q.step(dx=0)["time_ns"] for _ in range(20)]
            self.assertEqual(times, [i * 10_000_000 for i in range(20)])
            self.assertEqual(q.checkpoint()["sim_time_ns"], 200_000_000)

    def test_private_memory_not_in_default_dataset_export(self):
        with self.aquarium() as q:
            q.step()
            normal = q.store.dataset()
            full = q.store.dataset(include_nonpublic=True)
            self.assertEqual(len(normal["projection"]["items"]), 0)
            self.assertEqual(len(full["projection"]["items"]), 2)
            self.assertTrue(all(item["event"]["visibility"] == "PRIVATE" for item in full["projection"]["items"]))

    def test_corrupt_sqlite_event_payload_refused_on_reopen(self):
        with self.aquarium() as q:
            q.step()
        # sqlite3.Connection.__exit__ commits but does *not* close the FD.
        # Explicitly close after the deliberate corruption fixture commits.
        with closing(sqlite3.connect(self.db)) as db:
            with db:
                db.execute("DROP TRIGGER noosphere_no_update")
                db.execute("UPDATE events SET topic='tampered' WHERE seq=2")
        with self.assertRaises(NoosphereError):
            self.aquarium()

    def test_mismatched_seed_rejected_on_reopen(self):
        with self.aquarium() as q: q.step()
        changed = copy.deepcopy(SEED)
        changed["inherited"]["heartbeat_bpm"] = 70
        with self.assertRaises(AquariumError):
            Aquarium(self.db, changed, GRAPH)

    def test_mismatched_room_rejected_on_reopen(self):
        with self.aquarium() as q: q.step()
        with self.assertRaises(AquariumError):
            self.aquarium(room=Room(5, 5))

    def test_mismatched_dt_rejected_on_reopen(self):
        with self.aquarium() as q: q.step()
        with self.assertRaises(AquariumError):
            self.aquarium(dt_ns=20_000_000)

    def test_mismatched_graph_rejected_on_reopen(self):
        with self.aquarium() as q: q.step()
        graph = copy.deepcopy(GRAPH)
        graph["revision"] = "unreviewed"
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, graph)

    def test_valid_acquired_gene_modifies_following_frames_and_survives_reopen(self):
        with self.aquarium() as q:
            q.step(dx=0)
            q.step(dx=0)
            old = q.genome.revision
            updated = q.acquire(gene_event("gain", 2, 20_000_000))
            self.assertNotEqual(updated, old)
            frame = q.step(dx=0)
            self.assertEqual(frame["phenotype"]["parameters"]["heartbeat_bpm"], 90)
            state = q.checkpoint()
            self.assertEqual(q.store.count(), 5)
        with self.aquarium() as restored:
            self.assertEqual(restored.checkpoint(), state)
            self.assertEqual(restored.genome.phenotype(2)["parameters"]["heartbeat_bpm"], 90)
            self.assertEqual(restored.tick, 3)

    def test_later_acquisition_not_applied_to_current_tick(self):
        with self.aquarium() as q:
            q.acquire(gene_event("later", 3, 0, 15))
            early = q.step(dx=0)
            self.assertEqual(early["phenotype"]["parameters"]["heartbeat_bpm"], 60)
            q.step(dx=0)
            q.step(dx=0)
            later = q.step(dx=0)
            self.assertEqual(later["phenotype"]["parameters"]["heartbeat_bpm"], 75)

    def test_acquisition_cannot_retroactively_change_committed_history(self):
        with self.aquarium() as q:
            q.step()
            before = q.store.head()
            with self.assertRaises(AquariumError):
                q.acquire(gene_event("past", 0, 10_000_000))
            self.assertEqual(q.store.head(), before)

    def test_bad_acquisition_does_not_append(self):
        with self.aquarium() as q:
            previous = q.checkpoint()
            bad = gene_event("bad", 0, 0, delta=2000)
            with self.assertRaises(ValueError):
                q.acquire(bad)
            self.assertEqual(q.checkpoint(), previous)

    def test_bad_movement_does_not_advance_or_append(self):
        with self.aquarium() as q:
            before = q.checkpoint()
            for movement in (True, float("nan"), 10**5000):
                with self.assertRaises(AquariumError):
                    q.step(dx=movement)
            self.assertEqual(q.checkpoint(), before)

    def test_two_processes_stale_head_fails_closed(self):
        with self.aquarium() as q1, self.aquarium() as q2:
            previous = q2.checkpoint()
            q1.step()
            with self.assertRaises(NoosphereError):
                q2.step()
            self.assertEqual(q2.tick, previous["tick"])
            self.assertEqual(q2.world.revision, previous["world_revision"])
            with self.assertRaisesRegex(AquariumError, "stale or unadopted"):
                q2.checkpoint()
            self.assertEqual(q1.store.count(), 2)

    def test_chain_anchor_detects_divergent_database(self):
        with self.aquarium() as q:
            q.step()
            anchor = q.store.head()
        with self.aquarium(external_expected_head=anchor) as good:
            self.assertEqual(good.tick, 1)
            good.step()
        with self.assertRaises(NoosphereError):
            self.aquarium(external_expected_head=anchor)

    def test_unknown_record_type_rejected_on_reopen(self):
        with self.aquarium() as q:
            q._append_record("unrecognized.topic", "rogue", {}, 0, q.graph_sha256)
        with self.assertRaises(AquariumError):
            self.aquarium()

    def test_checkpoint_not_a_security_signature(self):
        with self.aquarium() as q:
            self.assertEqual(q.checkpoint()["trust"], "checksum_only_not_authenticated")

    def test_event_budget_is_fail_closed_before_appending(self):
        from unittest.mock import patch
        with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 2):
            with self.aquarium() as q:
                q.step()  # bootstrap + one frame = exactly two events
                prior = q.checkpoint()
                with self.assertRaises(AquariumError):
                    q.step()
                self.assertEqual(q.checkpoint(), prior)
                self.assertEqual(q.store.count(), 2)

    def test_cli_restarts_same_database(self):
        env = dict(os.environ)
        cmd = [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.db), "--ticks", "5", "--dx", "0.02",
        ]
        first = json.loads(subprocess.check_output(cmd, env=env, text=True))
        second = json.loads(subprocess.check_output(cmd, env=env, text=True))
        self.assertEqual(first["tick"], 5)
        self.assertEqual(second["tick"], 10)
        self.assertEqual(second["stored_events"], 11)


if __name__ == "__main__":
    unittest.main()
