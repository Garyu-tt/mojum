"""Offline tests for #700(a); no LeRobot/pyarrow/network dependency."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from mojum.lerobot_trajectory import (
    COLUMNS, MAX_FRAMES, LeRobotImportError, heldout_zero_baseline,
    import_rows, load_parquet_shard, validate_info,
)

INFO = {
    "codebase_version": "v3.0", "fps": 20,
    "features": {
        "observation.state": {"dtype": "float32", "shape": [2]},
        "action": {"dtype": "float32", "shape": [2]},
        "observation.images.front": {"dtype": "video", "shape": [3, 64, 64]},
    },
}
SOURCE = {
    "dataset_id": "public/demo", "dataset_revision": "a" * 40,
    "dataset_license": "Apache-2.0",
    "metadata_sha256": "b" * 64, "shard_sha256": "c" * 64,
}


def frames(episodes=2):
    return [
        {"episode_index": ep, "frame_index": i, "timestamp": i / 20,
         "task_index": ep, "observation.state": [i * .1, float(ep)],
         "action": [i * .2, float(ep) / 2],
         "observation.images.front": "PRIVATE-VISUAL-MUST-NOT-LEAK"}
        for ep in range(episodes) for i in range(3)
    ]


class LeRobotTrajectoryTests(unittest.TestCase):
    def imported(self, rows=None, info=None, **source):
        return import_rows(frames() if rows is None else rows,
                           INFO if info is None else info, **(SOURCE | source))

    def test_deterministic_replay_and_provenance_without_image_leak(self):
        a = self.imported()
        b = self.imported()
        self.assertEqual(a, b)
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(a.episodes, (0, 1))
        self.assertEqual(len(a.frames), 6)
        self.assertNotIn("PRIVATE-VISUAL", repr(a))
        self.assertEqual(a.frames[0].action, (0.0, 0.0))
        self.assertEqual(a.dataset_revision, "a" * 40)
        self.assertEqual(a.shard_sha256, "c" * 64)
        self.assertIsNone(a.asset_sha256)
        self.assertIsNone(a.model_sha256)
        pinned = self.imported(asset_sha256="d" * 64, model_sha256="e" * 64)
        self.assertEqual(pinned.asset_sha256, "d" * 64)
        self.assertNotEqual(pinned.digest, a.digest)
        original = a.frames
        result = heldout_zero_baseline(a, seed=123)
        self.assertEqual(original, a.frames)
        self.assertEqual(result["train_frames"] + result["heldout_frames"], 6)
        self.assertTrue(set(result["train_episodes"]).isdisjoint(
            result["heldout_episodes"]))
        self.assertEqual(result, heldout_zero_baseline(b, seed=123))
        self.assertFalse(result["learned_motor_policy"])
        self.assertFalse(result["physical_contact"])
        with self.assertRaises((AttributeError, TypeError)):
            a.frames[0].action = (1.0, 2.0)

    def test_untrusted_frame_and_time_validation(self):
        invalid = [
            ("bool_time", {"timestamp": True}),
            ("nonfinite_time", {"timestamp": float("inf")}),
            ("negative_time", {"timestamp": -.2}),
            ("timestamp_drift", {"timestamp": .9}),
            ("bool_action", {"action": [False, 0.0]}),
            ("nan_action", {"action": [float("nan"), 1]}),
            ("infinite_state", {"observation.state": [0.0, float("-inf")]}),
            ("bad_dimensions", {"action": [1.0]}),
            ("negative_task", {"task_index": -1}),
            ("bool_index", {"frame_index": True}),
            ("missing", {"action": None}),
        ]
        for title, change in invalid:
            with self.subTest(title=title):
                rows = frames()
                rows[0].update(change)
                with self.assertRaises(LeRobotImportError):
                    self.imported(rows=rows)

    def test_sequence_and_episode_fail_closed(self):
        cases = (
            [frames()[1]],  # starts mid-episode
            frames()[1:],  # first frame index != 0
            frames()[:2] + frames()[3:],  # next episode fine, but incomplete? allowed
            frames()[:2] + [frames()[2]] * 2,
            frames()[3:] + frames()[:3],  # episode order inversion
            frames()[:3] + frames()[:3],  # repeated episode
        )
        for index, rows in enumerate(cases):
            if index == 2:
                continue  # last frame may be absent in a row set; no length field
            with self.subTest(index=index), self.assertRaises(LeRobotImportError):
                self.imported(rows=rows)

    def test_budget_metadata_and_provenance_validation(self):
        for info in (
            {**INFO, "codebase_version": "v2.1"},
            {**INFO, "fps": True},
            {**INFO, "fps": float("inf")},
            {**INFO, "fps": 0},
            {**INFO, "features": {"action": INFO["features"]["action"]}},
            {**INFO, "features": {
                **INFO["features"], "action": {"shape": [129], "dtype": "float32"}}},
        ):
            with self.subTest(info=str(info)[:50]), self.assertRaises(LeRobotImportError):
                validate_info(info)
        for source in (
            {"dataset_revision": "main"},
            {"dataset_id": "../private"},
            {"dataset_license": ""},
            {"metadata_sha256": "abc"},
            {"asset_sha256": "not-a-digest"},
            {"model_sha256": "f" * 63},
        ):
            with self.subTest(source=source), self.assertRaises(LeRobotImportError):
                self.imported(**source)
        with self.assertRaises(LeRobotImportError):
            self.imported(rows=(frames()[0] for _ in range(MAX_FRAMES + 1)))
        with self.assertRaises(LeRobotImportError):
            heldout_zero_baseline(self.imported(rows=frames(1)))

    def create_dataset(self, folder, info=None, raw=None):
        root = Path(folder)
        (root / "meta").mkdir()
        (root / "data" / "chunk-000").mkdir(parents=True)
        (root / "meta" / "info.json").write_bytes(
            raw if raw is not None else json.dumps(
                INFO if info is None else info).encode())
        (root / "data" / "chunk-000" / "file-000.parquet").write_bytes(
            b"fake-disposable-parquet-file")
        return root

    def load(self, root, **overrides):
        opts = {
            "shard": "data/chunk-000/file-000.parquet",
            "dataset_id": "public/demo",
            "dataset_revision": "a" * 40,
            "dataset_license": "Apache-2.0",
            "allow_public_dataset": True,
        }
        return load_parquet_shard(root, **(opts | overrides))

    def test_explicit_optin_and_path_guard_without_arrow_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_dataset(directory)
            with self.assertRaisesRegex(LeRobotImportError, "opt-in"):
                self.load(root, allow_public_dataset=False)
            with self.assertRaises(LeRobotImportError):
                self.load(root, shard="data/../../meta/info.json")
            with self.assertRaises(LeRobotImportError):
                self.load(root, shard="/tmp/file.parquet")
            symlink = root / "data" / "link.parquet"
            symlink.symlink_to(root / "data" / "chunk-000" / "file-000.parquet")
            with self.assertRaises(LeRobotImportError):
                self.load(root, shard="data/link.parquet")
            # Work identically even if a developer has pyarrow in their venv.
            with mock.patch.dict(sys.modules, {"pyarrow": None, "pyarrow.parquet": None}):
                with self.assertRaisesRegex(LeRobotImportError, "pyarrow"):
                    self.load(root)

    def test_duplicate_metadata_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_dataset(directory, raw=b'{"codebase_version":"v3.0","fps":20,"fps":50}')
            with self.assertRaisesRegex(LeRobotImportError, "duplicate"):
                self.load(root)

    def test_bounded_reader_maps_real_parquet_api_to_immutable_frames(self):
        recorded = []
        class Batch:
            def to_pylist(self):
                return frames()
        class Reader:
            metadata = types.SimpleNamespace(num_rows=6)
            schema_arrow = types.SimpleNamespace(names=list(COLUMNS) + ["observation.images.front"])
            def __init__(self, path):
                recorded.append(path)
            def iter_batches(self, *, batch_size, columns, use_threads):
                self_ref = (batch_size, columns, use_threads)
                recorded.append(self_ref)
                yield Batch()
        pyarrow = types.ModuleType("pyarrow")
        pyarrow.__path__ = []
        parquet = types.ModuleType("pyarrow.parquet")
        parquet.ParquetFile = Reader
        with tempfile.TemporaryDirectory() as directory:
            root = self.create_dataset(directory)
            with mock.patch.dict(sys.modules, {"pyarrow": pyarrow, "pyarrow.parquet": parquet}):
                a = self.load(root)
                b = self.load(root)
            self.assertEqual(a, b)
            self.assertEqual(recorded[1], (128, list(COLUMNS), False))
            self.assertEqual(len(a.frames), 6)
            self.assertNotIn("PRIVATE-VISUAL", repr(a))
            self.assertEqual(a.metadata_sha256, b.metadata_sha256)


if __name__ == "__main__":
    unittest.main()
