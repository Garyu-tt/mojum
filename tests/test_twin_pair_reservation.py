"""Failed pair creation must not delete a published journal reservation."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
import mojum.twin_lab as twin_lab


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class TwinPairReservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.left = self.directory / "left.sqlite3"
        self.right = self.directory / "right.sqlite3"
        self.foreign_bytes = b"another creator owns the right path"
        self.writer_seed = copy.deepcopy(SEED)
        self.writer_seed["genome_id"] = "concurrent-writer"

    def pair(self):
        return twin_lab.create_pair(
            self.left, self.right, "left-twin", "right-twin", SEED, GRAPH,
        )

    def collide_right(self, after_left):
        reserve = twin_lab._reserve_new_path

        def reserve_with_competing_writer(path):
            if path == self.right:
                after_left()
                self.right.write_bytes(self.foreign_bytes)
            return reserve(path)

        return patch.object(
            twin_lab, "_reserve_new_path", side_effect=reserve_with_competing_writer,
        )

    def test_committed_journal_from_another_process_survives(self):
        seed_path = self.directory / "writer-genome.json"
        seed_path.write_text(json.dumps(self.writer_seed), encoding="utf-8")
        committed = {}

        def commit_in_another_process():
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            result = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium", "--db", str(self.left),
                 "--genome", str(seed_path), "--ticks", "1"],
                cwd=self.directory, env=env, capture_output=True, text=True,
                timeout=30, check=True,
            )
            committed["checkpoint"] = json.loads(result.stdout)
            committed["bytes"] = self.left.read_bytes()
            self.assertEqual(committed["checkpoint"]["tick"], 1)
            self.assertEqual(committed["checkpoint"]["stored_events"], 2)
            self.assertGreater(len(committed["bytes"]), 0)

        with self.collide_right(commit_in_another_process):
            with self.assertRaisesRegex(AquariumError, "existing"):
                self.pair()

        self.assertTrue(self.left.is_file(), "the committed journal was deleted")
        self.assertEqual(self.left.read_bytes(), committed["bytes"])
        self.assertEqual(self.right.read_bytes(), self.foreign_bytes)
        with Aquarium(self.left, self.writer_seed, GRAPH,
                      agent_id=self.writer_seed["genome_id"], read_only=True) as reopened:
            checkpoint = dict(committed["checkpoint"])
            self.assertEqual(reopened.store.count(), checkpoint.pop("stored_events"))
            checkpoint.pop("status")
            self.assertEqual(reopened.checkpoint(), checkpoint)
        self.assertEqual(self.left.read_bytes(), committed["bytes"])
        with self.assertRaisesRegex(AquariumError, "existing"):
            self.pair()
        self.assertEqual(self.left.read_bytes(), committed["bytes"])

    def test_live_wal_writer_survives_failed_pair_creation(self):
        writer = None
        committed = {}

        def keep_writer_open():
            nonlocal writer
            writer = Aquarium(self.left, self.writer_seed, GRAPH,
                              agent_id=self.writer_seed["genome_id"])
            self.addCleanup(writer.close)
            writer.step()
            committed["checkpoint"] = writer.checkpoint()
            for suffix in ("", "-wal", "-shm"):
                path = Path(str(self.left) + suffix)
                self.assertTrue(path.is_file())
                committed[suffix] = path.read_bytes()

        with self.collide_right(keep_writer_open):
            with self.assertRaisesRegex(AquariumError, "existing"):
                self.pair()

        self.assertTrue(self.left.is_file(), "the live writer's journal was deleted")
        for suffix in ("", "-wal", "-shm"):
            self.assertEqual(Path(str(self.left) + suffix).read_bytes(), committed[suffix])
        self.assertEqual(writer.checkpoint(), committed["checkpoint"])
        writer.step()
        self.assertEqual(writer.checkpoint()["tick"], 2)
        writer.close()
        with Aquarium(self.left, self.writer_seed, GRAPH,
                      agent_id=self.writer_seed["genome_id"], read_only=True) as reopened:
            self.assertEqual(reopened.checkpoint()["tick"], 2)

    def test_empty_reservation_and_original_error_are_retained(self):
        reserve = twin_lab._reserve_new_path
        failure = AquariumError("second journal reservation unavailable")

        def unavailable_right(path):
            if path == self.right:
                raise failure
            return reserve(path)

        with patch.object(twin_lab, "_reserve_new_path", side_effect=unavailable_right):
            with self.assertRaises(AquariumError) as caught:
                self.pair()

        self.assertIs(caught.exception, failure)
        self.assertTrue(self.left.is_file())
        self.assertEqual(self.left.read_bytes(), b"")
        self.assertFalse(self.right.exists())
        for suffix in ("-wal", "-shm"):
            self.assertFalse(Path(str(self.left) + suffix).exists())
        with self.assertRaisesRegex(AquariumError, "existing"):
            self.pair()

    def test_interruption_preserves_published_reservation(self):
        reserve = twin_lab._reserve_new_path
        interruption = KeyboardInterrupt()

        def interrupted_right(path):
            if path == self.right:
                raise interruption
            return reserve(path)

        with patch.object(twin_lab, "_reserve_new_path", side_effect=interrupted_right):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.pair()

        self.assertIs(caught.exception, interruption)
        self.assertTrue(self.left.is_file())
        self.assertEqual(self.left.read_bytes(), b"")
        self.assertFalse(self.right.exists())

    def test_failed_first_reservation_never_touches_second_path(self):
        reserve = twin_lab._reserve_new_path

        def occupied_left(path):
            self.assertEqual(path, self.left)
            self.left.write_bytes(self.foreign_bytes)
            return reserve(path)

        with patch.object(twin_lab, "_reserve_new_path", side_effect=occupied_left):
            with self.assertRaisesRegex(AquariumError, "existing"):
                self.pair()

        self.assertEqual(self.left.read_bytes(), self.foreign_bytes)
        self.assertFalse(self.right.exists())

    def test_replaced_first_path_is_preserved(self):
        replacement = self.directory / "replacement.sqlite3"
        replacement.write_bytes(b"replacement owned by another creator")

        def replace_left():
            os.replace(replacement, self.left)

        with self.collide_right(replace_left):
            with self.assertRaisesRegex(AquariumError, "existing"):
                self.pair()

        self.assertEqual(self.left.read_bytes(), b"replacement owned by another creator")
        self.assertEqual(self.right.read_bytes(), self.foreign_bytes)


if __name__ == "__main__":
    unittest.main()
