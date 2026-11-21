"""Independent synthetic twin journals and fail-closed mentor commands."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mojum.twin_lab as twin_lab

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError
from mojum.twin_lab import create_pair, mentor_steps

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class TwinLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.left = Path(self.temp.name) / "left.sqlite3"
        self.right = Path(self.temp.name) / "right.sqlite3"

    def pair(self):
        return create_pair(self.left, self.right, "twin-a", "twin-b", SEED, GRAPH)

    def test_fresh_twins_share_heritable_traits_not_identity_or_journal(self):
        report = self.pair()
        self.assertEqual([t["tick"] for t in report["twins"]], [0, 0])
        self.assertEqual([t["stored_events"] for t in report["twins"]], [1, 1])
        for db, actor in ((self.left, "twin-a"), (self.right, "twin-b")):
            seed = copy.deepcopy(SEED)
            seed["genome_id"] = actor
            with Aquarium(db, seed, GRAPH, agent_id=actor) as twin:
                self.assertEqual(twin.genome.phenotype(0)["parameters"], SEED["inherited"])
                self.assertEqual(twin.store.count(), 1)
                self.assertEqual(twin.tick, 0)

    def test_mentors_diverge_by_input_without_cross_journal_read(self):
        self.pair()
        first = mentor_steps(self.left, "twin-a", SEED, GRAPH,
                             ticks=8, dx=.05, dy=0)
        second = mentor_steps(self.right, "twin-b", SEED, GRAPH,
                              ticks=4, dx=-.05, dy=0)
        self.assertEqual(first["shared_heritable_sha256"],
                         second["shared_heritable_sha256"])
        self.assertEqual(first["twin"]["tick"], 8)
        self.assertEqual(second["twin"]["tick"], 4)
        self.assertNotEqual(first["twin"]["position_m"],
                            second["twin"]["position_m"])
        for db, actor, tick in ((self.left, "twin-a", 8),
                                (self.right, "twin-b", 4)):
            seed = copy.deepcopy(SEED)
            seed["genome_id"] = actor
            with Aquarium(db, seed, GRAPH, agent_id=actor) as reopened:
                self.assertEqual(reopened.tick, tick)
                self.assertEqual(reopened.store.count(), tick + 1)
        with self.assertRaises(NoosphereError):
            mentor_steps(self.left, "twin-b", SEED, GRAPH,
                         ticks=1, dx=0, dy=0)
        self.assertEqual(mentor_steps(self.left, "twin-a", SEED, GRAPH,
                                      ticks=0, dx=0, dy=0)["twin"]["tick"], 8)

    def test_no_overwrite_aliased_paths_or_duplicate_identity(self):
        with self.assertRaises(AquariumError):
            create_pair(self.left, self.right, "same", "same", SEED, GRAPH)
        self.assertFalse(self.left.exists())
        with self.assertRaises(AquariumError):
            create_pair(self.left, self.left, "a", "b", SEED, GRAPH)
        self.assertFalse(self.left.exists())
        self.pair()
        first = self.left.read_bytes()
        with self.assertRaises(AquariumError):
            self.pair()
        self.assertEqual(self.left.read_bytes(), first)

    def test_racing_second_journal_is_never_opened_or_modified(self):
        # Simulate another creator arriving after _new_path preflight but
        # immediately before the exclusive reservation of the right journal.
        reserve = twin_lab._reserve_new_path
        foreign_bytes = b"foreign journal; must not be read or overwritten"

        def raced_reservation(path):
            if path == self.right:
                self.right.write_bytes(foreign_bytes)
            return reserve(path)

        with patch.object(twin_lab, "_reserve_new_path", side_effect=raced_reservation):
            with self.assertRaisesRegex(AquariumError, "existing"):
                self.pair()
        self.assertEqual(self.right.read_bytes(), foreign_bytes)
        self.assertFalse(self.left.exists())
        self.assertFalse(Path(str(self.right) + "-wal").exists())

    def test_mentor_validation_prevents_partial_append(self):
        self.pair()
        before = mentor_steps(self.left, "twin-a", SEED, GRAPH,
                              ticks=0, dx=0, dy=0)
        for kwargs in ({"ticks": True, "dx": 0, "dy": 0},
                       {"ticks": 1001, "dx": 0, "dy": 0},
                       {"ticks": 1, "dx": float("nan"), "dy": 0},
                       {"ticks": 1, "dx": 900, "dy": 900}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AquariumError):
                mentor_steps(self.left, "twin-a", SEED, GRAPH, **kwargs)
        self.assertEqual(mentor_steps(self.left, "twin-a", SEED, GRAPH,
                                      ticks=0, dx=0, dy=0), before)

    def test_source_dante_identity_is_not_reused(self):
        with self.assertRaises(AquariumError):
            create_pair(self.left, self.right, SEED["genome_id"],
                        "new-twin-b", SEED, GRAPH)
        self.assertFalse(self.left.exists())
        self.assertFalse(self.right.exists())
        with self.assertRaises(AquariumError):
            mentor_steps(self.left, SEED["genome_id"], SEED, GRAPH,
                         ticks=0, dx=0, dy=0)
        self.assertFalse(self.left.exists())

    def test_missing_mentor_db_is_not_created(self):
        with self.assertRaises(AquariumError):
            mentor_steps(self.left, "twin-a", SEED, GRAPH,
                         ticks=1, dx=.02, dy=0)
        self.assertFalse(self.left.exists())


if __name__ == "__main__":
    unittest.main()
