"""Offline integration acceptance for optional LeRobot numeric export bridge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.lerobot_trajectory import (
    MAX_FRAMES_BYTES, TrajectoryError, held_out_control, load_export,
)


def sample_rows():
    return [
        {"index": i, "episode_index": ep, "frame_index": tick,
         "timestamp": tick / 10, "task_index": 0, "observation.state": [float(tick)],
         "action": [1.0]}
        for i, (ep, tick) in enumerate([(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)])
    ]


class LeRobotExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.rows_file = self.folder / "frames.jsonl"
        self.manifest_file = self.folder / "manifest.json"
        self.rows = sample_rows()
        self.manifest = {
            "schema": "mojum.lerobot-export/1", "lerobot_format": "v3.0",
            "repo_id": "synthetic/held-out", "revision": "a" * 40,
            "license": "CC0-1.0", "fps": 10, "state_dim": 1,
            "action_dim": 1, "frames_sha256": "",
        }
        self.write()

    def write(self, *, raw_rows=None, raw_manifest=None):
        data = ("\n".join(json.dumps(row) for row in self.rows) + "\n").encode() if raw_rows is None else raw_rows
        self.rows_file.write_bytes(data)
        self.manifest["frames_sha256"] = hashlib.sha256(data).hexdigest()
        self.manifest_file.write_bytes(json.dumps(self.manifest).encode() if raw_manifest is None else raw_manifest)

    def load(self):
        return load_export(self.manifest_file, self.rows_file)

    def test_valid_replay_is_deterministic(self):
        first = self.load()
        second = self.load()
        self.assertEqual(first, second)
        self.assertEqual((len(first.frames), first.episode_count), (6, 2))
        self.assertEqual(first.summary(), second.summary())
        self.assertNotIn("observation.state", json.dumps(first.summary()))
        self.assertNotIn("synthetic/held-out", json.dumps(first.summary()))

    def test_held_out_train_is_separate_from_control(self):
        report = held_out_control(self.load())
        self.assertEqual(report["train_pairs"], 2)
        self.assertEqual(report["held_out_pairs"], 2)
        self.assertEqual(report["frozen_zero_mse"], 1)
        self.assertEqual(report["fitted_gain_mse"], 0)

    def test_checksum_tampering_fails_closed(self):
        self.rows_file.write_bytes(self.rows_file.read_bytes() + b"\n")
        with self.assertRaisesRegex(TrajectoryError, "checksum"):
            self.load()

    def test_duplicate_json_key_fails(self):
        blob = json.dumps(self.rows[0]).replace('"index": 0', '"index": 0, "index": 0')
        raw = (blob + "\n" + "\n".join(json.dumps(row) for row in self.rows[1:])).encode()
        self.write(raw_rows=raw)
        with self.assertRaises(TrajectoryError):
            self.load()

    def test_nonfinite_and_bool_vectors_fail(self):
        for bad in [float("nan"), True, float("inf"), 1e100]:
            with self.subTest(value=str(bad)):
                self.rows[0]["action"] = [bad]
                self.write()
                with self.assertRaises(TrajectoryError):
                    self.load()

    def test_frame_hole_and_out_of_order_episode_fail(self):
        self.rows[1]["frame_index"] = 99
        self.write()
        with self.assertRaises(TrajectoryError):
            self.load()
        self.rows = sample_rows()
        self.rows[-1]["episode_index"] = 0
        self.write()
        with self.assertRaises(TrajectoryError):
            self.load()

    def test_timestamp_regression_fails(self):
        self.rows[1]["timestamp"] = 0
        self.write()
        with self.assertRaises(TrajectoryError):
            self.load()

    def test_unpinned_revision_or_missing_license_rejected(self):
        for field, bad in [("revision", "main"), ("license", ""), ("repo_id", "../../../private")]:
            with self.subTest(field=field):
                self.manifest[field] = bad
                self.write()
                with self.assertRaises(TrajectoryError):
                    self.load()
                self.manifest[field] = {"revision": "a" * 40, "license": "CC0-1.0", "repo_id": "synthetic/held-out"}[field]

    def test_unknown_columns_rejected_even_if_checksum_valid(self):
        self.rows[0]["observation.images.front"] = "PRIVATE/BAD"
        self.write()
        with self.assertRaises(TrajectoryError):
            self.load()

    def test_manifest_version_and_unknown_fields_rejected(self):
        self.manifest["lerobot_format"] = "v2.1"
        self.write()
        with self.assertRaises(TrajectoryError):
            self.load()
        self.manifest["lerobot_format"] = "v3.0"
        self.manifest["private_token"] = "do-not-log"
        self.write()
        with self.assertRaises(TrajectoryError):
            self.load()

    def test_size_cap_and_zero_frames(self):
        self.rows_file.write_bytes(b"x" * (MAX_FRAMES_BYTES + 1))
        with self.assertRaises(TrajectoryError):
            self.load()
        self.write(raw_rows=b"")
        with self.assertRaises(TrajectoryError):
            self.load()

    def test_holdout_rejects_one_episode(self):
        self.rows = self.rows[:3]
        self.write()
        with self.assertRaises(TrajectoryError):
            held_out_control(self.load())

    def test_cli_redacts_bad_file_content(self):
        secret = "PRIVATE_DO_NOT_LEAK_01234"
        self.rows_file.write_text(secret, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "mojum.lerobot_trajectory", "--manifest", str(self.manifest_file),
             "--frames", str(self.rows_file)], capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn(secret, completed.stderr + completed.stdout)
        self.assertNotIn(str(self.rows_file), completed.stderr + completed.stdout)

    def test_cli_clean_summary_without_state(self):
        completed = subprocess.run(
            [sys.executable, "-m", "mojum.lerobot_trajectory", "--manifest", str(self.manifest_file),
             "--frames", str(self.rows_file), "--held-out-control"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        parsed = json.loads(completed.stdout)
        self.assertEqual(parsed["proxy_eval"]["fitted_gain_mse"], 0.)
        self.assertNotIn("repo_id", parsed)


if __name__ == "__main__":
    unittest.main()
