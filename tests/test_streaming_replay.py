"""Bounded streaming journal iteration and replay beyond the legacy 10k cap.

All fixtures are synthetic. These tests never claim authenticated history or
unbounded capacity: the replay ceiling remains an explicit finite constant.
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, MAX_REPLAY_EVENTS
from mojum.noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def journal_event(eid, *, topic="synthetic.topic", visibility="PRIVATE",
                  source="synthetic-unit-test", n=0):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": eid,
        "agent_id": SEED["genome_id"],
        "topic": topic,
        "observed_at_ns": 0,
        "recorded_at_ns": 0,
        "valid_from_ns": 0,
        "valid_to_ns": None,
        "visibility": visibility,
        "source": source,
        "source_revision": "synthetic-r1",
        "payload": {"n": n},
    }


class IterEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.sqlite3"

    def store(self):
        return LocalNoosphere(self.path, SEED["genome_id"])

    def seed_journal(self):
        with self.store() as db:
            head = db.head()
            specs = (
                ("public-1", "public.topic", "PUBLIC", "source-a"),
                ("private-1", "private.topic", "PRIVATE", "source-a"),
                ("public-2", "public.topic", "PUBLIC", "source-b"),
                ("private-2", "public.topic", "PRIVATE", "source-b"),
            )
            for index, (eid, topic, visibility, source) in enumerate(specs):
                head = db.append(
                    journal_event(eid, topic=topic, visibility=visibility,
                                  source=source, n=index),
                    expected_head=head,
                )
            return head

    def test_iteration_matches_dataset_projection(self):
        self.seed_journal()
        with self.store() as db:
            expected = db.dataset(include_nonpublic=True)["projection"]["items"]
            streamed = list(db.iter_events(include_nonpublic=True))
            self.assertEqual(streamed, expected)
            self.assertEqual([item["seq"] for item in streamed], [1, 2, 3, 4])

    def test_batch_size_does_not_change_order_or_content(self):
        self.seed_journal()
        with self.store() as db:
            whole = list(db.iter_events(include_nonpublic=True, batch_size=128))
            single = list(db.iter_events(include_nonpublic=True, batch_size=1))
            pairs = list(db.iter_events(include_nonpublic=True, batch_size=3))
            self.assertEqual(whole, single)
            self.assertEqual(whole, pairs)

    def test_public_only_default_matches_dataset(self):
        self.seed_journal()
        with self.store() as db:
            self.assertEqual(
                list(db.iter_events()),
                db.dataset()["projection"]["items"],
            )
            self.assertEqual(
                [item["event"]["event_id"] for item in db.iter_events()],
                ["public-1", "public-2"],
            )

    def test_topic_and_source_filters_match_dataset(self):
        self.seed_journal()
        with self.store() as db:
            for filters in (
                {"topic": "public.topic"},
                {"source": "source-a"},
                {"topic": "public.topic", "source": "source-b"},
                {"include_nonpublic": True, "source": "source-b"},
            ):
                with self.subTest(filters=filters):
                    streamed = list(db.iter_events(**filters))
                    expected = db.dataset(**filters)["projection"]["items"]
                    self.assertEqual(streamed, expected)

    def test_invalid_arguments_fail_closed(self):
        self.seed_journal()
        with self.store() as db:
            for kwargs in (
                {"batch_size": 0},
                {"batch_size": -1},
                {"batch_size": 10_001},
                {"batch_size": "128"},
                {"batch_size": True},
                {"include_nonpublic": 1},
                {"include_nonpublic": "yes"},
                {"topic": ""},
                {"topic": 7},
                {"source": ""},
                {"source": None.__class__},
            ):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(NoosphereError):
                        list(db.iter_events(**kwargs))

    def test_iterator_does_not_block_later_appends_when_closed_early(self):
        head = self.seed_journal()
        with self.store() as db:
            stream = db.iter_events(include_nonpublic=True, batch_size=1)
            self.assertIsNotNone(next(stream, None))
            stream.close()
            self.assertEqual(db.count(), 4)
            db.append(journal_event("after-early-close"), expected_head=head)
            self.assertEqual(db.count(), 5)

    def test_streaming_does_not_materialize_full_projection(self):
        self.seed_journal()
        with self.store() as db:
            with patch.object(LocalNoosphere, "dataset",
                              side_effect=AssertionError("full dataset load")):
                self.assertEqual(len(list(db.iter_events(include_nonpublic=True))), 4)


class StreamingAquariumReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "events.sqlite3"

    def aquarium(self, **kwargs):
        return Aquarium(self.db, SEED, GRAPH, **kwargs)

    def test_replay_streams_instead_of_loading_dataset(self):
        with self.aquarium() as q:
            q.step(dx=0.02)
            q.step(dx=0.02)
            head = q.checkpoint()["journal_head"]
        with patch.object(LocalNoosphere, "dataset",
                          side_effect=AssertionError("full dataset load")):
            with self.aquarium() as q:
                self.assertEqual(q.tick, 2)
                self.assertEqual(q.checkpoint()["journal_head"], head)

    def test_budget_gate_uses_cached_count_not_per_append_scan(self):
        with self.aquarium() as q:
            q.step(dx=0.02)
            prior = q.checkpoint()
            with patch.object(q.store, "count",
                              side_effect=AssertionError("per-append count scan")):
                q.step(dx=0.02)
                checkpoint = q.checkpoint()
            self.assertEqual(q.stored_events, 3)
            self.assertEqual(checkpoint["tick"], 2)
            self.assertNotEqual(checkpoint["journal_head"], prior["journal_head"])

    def test_cached_budget_still_fails_closed_before_append(self):
        with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
            with self.aquarium() as q:
                q.step(dx=0.02)
                q.step(dx=0.02)
                prior = copy.deepcopy(q.checkpoint())
                with self.assertRaises(AquariumError):
                    q.step(dx=0.02)
                self.assertEqual(q.checkpoint(), prior)
                self.assertEqual(q.stored_events, 3)
                self.assertEqual(q.store.count(), 3)

    def test_cached_count_tracks_reopened_journal(self):
        with self.aquarium() as q:
            q.step(dx=0.02)
            q.step(dx=0.02)
        with self.aquarium() as q:
            self.assertEqual(q.stored_events, q.store.count())
            self.assertEqual(q.stored_events, 3)

    def test_stale_writer_still_fails_cas_after_count_cache(self):
        first = self.aquarium()
        second = self.aquarium()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.step(dx=0.02)
        with self.assertRaises(NoosphereError):
            second.step(dx=0.02)
        self.assertEqual(second.tick, 0)
        self.assertEqual(second.stored_events, 1)
        self.assertEqual(second.store.count(), 2)

    def test_ceiling_is_an_explicit_finite_constant(self):
        self.assertEqual(MAX_REPLAY_EVENTS, 100_000)
        self.assertNotEqual(MAX_REPLAY_EVENTS, float("inf"))


if __name__ == "__main__":
    unittest.main()
