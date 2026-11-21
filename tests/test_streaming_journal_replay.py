"""Bounded-memory SQLite streaming replay for portable synthetic Aquarium.

Fixtures use real SQLite journal rows; no unsigned snapshot fast-forward,
in-memory fake files, hidden oracle observations, GPU or third-party modules.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, MAX_REPLAY_EVENTS
from mojum.aquarium_trace_export import export_private_trace
from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class StreamingNoosphereReplayTests(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.db = self.root / "journal.sqlite3"
        self.output = self.root / "part.private.jsonl"

    def aquarium(self):
        return Aquarium(self.db, SEED, GRAPH,
                        exploration_mode="goal_free", sensory_mode="toy_probe")

    def test_cursor_default_public_privacy_explicit_opt_in_and_batch_limits(self):
        with LocalNoosphere(self.db, "stream-test-agent") as store:
            for i, visibility in enumerate(("PUBLIC", "PRIVATE", "RESTRICTED", "PUBLIC")):
                record = {
                    "schema": EVENT_SCHEMA,
                    "event_id": f"e-{i}",
                    "agent_id": "stream-test-agent",
                    "topic": "synthetic.record",
                    "observed_at_ns": i,
                    "recorded_at_ns": i,
                    "valid_from_ns": i,
                    "valid_to_ns": None,
                    "visibility": visibility,
                    "source": "synthetic-test",
                    "source_revision": "r01",
                    "payload": {"ordinal": i},
                }
                store.append(record, expected_head=store.head())
            original = store.dataset(include_nonpublic=True, limit=4)["projection"]["items"]
            expected = [original[0], original[3]]
            self.assertEqual(list(store.iter_events(batch_size=1)), expected)
            self.assertEqual(list(store.iter_events(batch_size=2)), expected)
            self.assertEqual(list(store.iter_events(
                include_nonpublic=True, batch_size=1)), original)
            self.assertEqual(list(store.iter_events(
                include_nonpublic=True, batch_size=3)), original)
            for value in (True, 0, -1, 1025, "64", 1.0):
                with self.subTest(batch_size=value):
                    with self.assertRaises(NoosphereError):
                        list(store.iter_events(batch_size=value))
            with self.assertRaises(NoosphereError):
                list(store.iter_events(include_nonpublic="yes"))
            self.assertEqual(store.verify_chain()["count"], 4)

    def test_canonical_reopen_and_export_never_materialize_dataset(self):
        with self.aquarium() as q:
            original = [q.step() for _ in range(17)]
            head = q.checkpoint()["journal_head"]
            count = q.store.count()
            self.assertEqual(count, 18)
            self.assertEqual(q._committed_event_count, count)
        with patch.object(LocalNoosphere, "dataset",
                          side_effect=AssertionError("unbounded dataset called")):
            with self.aquarium() as restored:
                self.assertEqual(restored.tick, 17)
                self.assertEqual(restored.store.count(), count)
                self.assertEqual(restored.checkpoint()["journal_head"], head)
                self.assertEqual(restored._committed_event_count, count)
            meta = export_private_trace(
                self.db, self.output, SEED, GRAPH,
                allow_private_export=True, exploration_mode="goal_free",
                sensory_mode="toy_probe", start_tick=9, frame_limit=5,
                expected_journal_head=head,
            )
        parsed = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual(parsed[:-1], original[9:14])
        self.assertEqual(parsed[-1]["trace_range"]["start_tick"], 9)
        self.assertEqual(parsed[-1]["trace_range"]["next_tick"], 14)
        self.assertEqual(parsed[-1]["stored_events"], count)
        self.assertEqual(parsed[-1]["journal_head"], head)
        self.assertEqual(meta["frames"], 5)
        self.assertEqual(meta["source_total_ticks"], 17)

    def test_10000_tick_start_is_valid_input_not_old_hard_limit(self):
        self.assertEqual(MAX_REPLAY_EVENTS, 100_000)
        with self.aquarium() as q:
            q.step()
        with self.assertRaisesRegex(ValueError, "no committed frames"):
            export_private_trace(
                self.db, self.output, SEED, GRAPH,
                allow_private_export=True, exploration_mode="goal_free",
                sensory_mode="toy_probe", start_tick=10_000, frame_limit=1,
            )
        self.assertFalse(self.output.exists())

    def test_per_step_append_uses_committed_count_cache_no_per_step_count(self):
        with self.aquarium() as q:
            before = q._committed_event_count
            with patch.object(q.store, "count",
                              side_effect=AssertionError("no per-step COUNT scan")):
                q.step()
                q.step()
                self.assertEqual(q._committed_event_count, before + 2)
                self.assertEqual(q.checkpoint()["tick"], 2)
            self.assertEqual(q.store.count(), before + 2)
            self.assertEqual(q.checkpoint()["journal_head"], q.store.head())

    def test_resource_budget_still_fails_closed_without_partial_adoption(self):
        with self.aquarium() as q:
            q.step()
            q.step()
            prior = q.checkpoint()
            self.assertEqual(q.store.count(), 3)
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
                with self.assertRaisesRegex(AquariumError, "budget"):
                    q.step()
            self.assertEqual(q.checkpoint(), prior)
            self.assertEqual(q.store.count(), 3)
        with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 2):
            with self.assertRaisesRegex(AquariumError, "budget"):
                self.aquarium()
        with self.aquarium() as q:
            self.assertEqual(q.tick, 2)

    def test_corrupt_indexed_row_remains_detected_by_streaming_replay(self):
        with self.aquarium() as q:
            q.step()
            q.step()
            before = q.checkpoint()
        with closing(sqlite3.connect(self.db)) as db:
            with db:
                db.execute("DROP TRIGGER noosphere_no_update")
                db.execute("UPDATE events SET topic='wrong' WHERE seq=2")
        with self.assertRaises(NoosphereError):
            self.aquarium()
        self.assertFalse(self.output.exists())
        self.assertEqual(before["tick"], 2)

    def test_stale_local_writer_count_not_silently_accepted(self):
        with self.aquarium() as first, self.aquarium() as stale:
            before = stale.checkpoint()
            first.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale._committed_event_count, 1)
            self.assertEqual(stale.tick, 0)
            with self.assertRaisesRegex(AquariumError, "stale"):
                stale.checkpoint()
            self.assertEqual(first.checkpoint()["tick"], 1)


if __name__ == "__main__":
    unittest.main()
