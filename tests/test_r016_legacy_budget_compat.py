"""Regression: original legacy budget gates coexist with segmented opt-in.

A new 262144-event *logical* SQLite capacity cannot silently lift the
100000-event legacy limit or defeat preflight before a batch. Conversely
tightening the legacy limit must not cap explicitly segmented journals.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import tempfile
import threading

from mojum.aquarium import Aquarium, AquariumError
from mojum.portable_host import PortableHostError, run_paced

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))


class LegacyBudgetPreservationTests(TestCase):
    def test_existing_legacy_db_and_writer_refuse_runtime_shrunk_limit(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "legacy.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled",
                          exploration_mode="goal_free", sensory_mode="none") as a:
                a.step()
                a.step()
                previous = a.checkpoint()
                self.assertEqual(a.store.count(), 3)
                with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
                    with self.assertRaisesRegex(AquariumError, "budget"):
                        a.step()
                    self.assertEqual(a.checkpoint(), previous)
                    self.assertEqual(a.store.count(), 3)
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 2):
                with self.assertRaisesRegex(AquariumError, "budget"):
                    Aquarium(db, SEED, GRAPH, neural_mode="disabled",
                             exploration_mode="goal_free", sensory_mode="none")

    def test_paced_host_preflight_observes_stricter_legacy_constant(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "legacy.sqlite3"
            with Aquarium(db, SEED, GRAPH, neural_mode="disabled",
                          exploration_mode="goal_free", sensory_mode="none") as a:
                previous = a.checkpoint()
                with patch("mojum.portable_host.MAX_REPLAY_EVENTS", 3):
                    with self.assertRaisesRegex(PortableHostError, "3-event"):
                        run_paced(a, ticks=3, interval_ms=20,
                                  stop_event=threading.Event())
                self.assertEqual(a.checkpoint(), previous)
                self.assertEqual(a.store.count(), 1)

    def test_segmented_opt_in_does_not_inherit_monkeypatched_legacy_limit(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "segmented.sqlite3"
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3), \
                 patch("mojum.portable_host.MAX_REPLAY_EVENTS", 3):
                with Aquarium(db, SEED, GRAPH, neural_mode="disabled",
                              exploration_mode="goal_free", sensory_mode="none",
                              developmental_policy="local_cell_v4",
                              journal_backend="segmented",
                              journal_segment_size=64) as a:
                    done = run_paced(a, ticks=4, interval_ms=20,
                                     stop_event=threading.Event())
                    self.assertEqual(done, 4)
                    self.assertEqual(a.checkpoint()["tick"], 4)
                    self.assertEqual(a.store.count(), 5)
                    self.assertGreater(a._effective_journal_limit(), 3)


if __name__ == "__main__":
    import unittest
    unittest.main()
