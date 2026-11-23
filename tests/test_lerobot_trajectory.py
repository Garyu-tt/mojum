"""Synthetic offline regression/failure gates for the LeRobot decoded-row bridge."""
import json
import math
import unittest

from mojum.lerobot_trajectory import (
    BENCHMARK_SCHEMA, MAX_FRAMES, Provenance, TrajectoryError, Trajectory, Frame,
    benchmark, import_decoded_rows, import_lerobot_dataset, replay,
)


class LeRobotTrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.provenance = Provenance(
            dataset_id="synthetic/test_trajectory", dataset_revision="a" * 40,
            robot_asset_sha256="b" * 64, license_id="CC0-1.0",
            collector="teleoperator", consent_asserted=True,
        )

    def rows(self, episode=0, action=0.75):
        return [{"episode_index": episode, "frame_index": i,
                 "timestamp": float(i) / 30, "observation.state": [i % 2, 0.25],
                 "action": [action], "observation.images.front": b"PRIVATE MEDIA",
                 "secret_token": "SHOULD_NOT_SURVIVE"} for i in range(3)]

    def load(self, episode=0, action=0.75):
        return import_decoded_rows(self.rows(episode, action),
                                   provenance=self.provenance, episode_index=episode)

    def test_passive_replay_and_strict_allowlist(self):
        rows = self.rows()
        result = self.load()
        rows[0]["action"][0] = 100
        self.assertEqual(result.frames[0].action, (0.75,))
        self.assertEqual(replay(result)[0], (0.0, (0.0, 0.25), (0.75,)))
        self.assertNotIn("PRIVATE", str(result))
        self.assertNotIn("SHOULD_NOT_SURVIVE", str(result))
        self.assertEqual(self.load().digest, result.digest)
        self.assertEqual(len(result.digest), 64)

    def test_real_shape_like_optional_dataset_bridge_no_import(self):
        class Tensor:
            shape = (2,)
            def tolist(self):
                return [0.0, 1.0]
        rows = self.rows()
        for row in rows:
            row["observation.state"] = Tensor()
        got = import_lerobot_dataset(rows, provenance=self.provenance,
                                     episode_index=0, start=0, stop=3)
        self.assertEqual(got.frames[0].state, (0.0, 1.0))
        self.assertEqual(len(got.frames), 3)

    def test_timestamps_indices_and_episode_are_validated(self):
        for key, value in (("timestamp", float("nan")), ("timestamp", -1),
                           ("timestamp", True), ("frame_index", 1.5),
                           ("episode_index", 5), ("action", [float("inf")]),
                           ("observation.state", [])):
            with self.subTest(key=key, value=value):
                rows = self.rows()
                rows[1][key] = value
                with self.assertRaises(TrajectoryError):
                    import_decoded_rows(rows, provenance=self.provenance, episode_index=0)
        rows = self.rows()
        rows[2]["timestamp"] = rows[1]["timestamp"]
        with self.assertRaises(TrajectoryError):
            import_decoded_rows(rows, provenance=self.provenance, episode_index=0)
        rows = self.rows()
        rows[2]["frame_index"] += 1
        with self.assertRaises(TrajectoryError):
            import_decoded_rows(rows, provenance=self.provenance, episode_index=0)

    def test_no_unbounded_generator_and_slice(self):
        def unbounded():
            i = 0
            while True:
                yield {"episode_index": 0, "frame_index": i,
                       "timestamp": float(i), "observation.state": [0], "action": [0]}
                i += 1
        with self.assertRaisesRegex(TrajectoryError, "budget"):
            import_decoded_rows(unbounded(), provenance=self.provenance,
                                episode_index=0, max_frames=3)
        with self.assertRaises(TrajectoryError):
            import_lerobot_dataset([], provenance=self.provenance,
                                   episode_index=0, start=0, stop=MAX_FRAMES + 1)

    def test_fail_closed_provenance(self):
        data = vars(self.provenance).copy()
        for updates in ({"consent_asserted": False}, {"dataset_revision": "main"},
                        {"robot_asset_sha256": "missing"},
                        {"collector": "policy"}, {"license_id": "http://x"}):
            with self.subTest(updates=updates):
                with self.assertRaises(TrajectoryError):
                    Provenance(**{**data, **updates})
        policy = Provenance(**{**data, "collector": "policy",
                               "model_id": "local.policy", "model_revision": "c" * 40})
        self.assertEqual(policy.model_revision, "c" * 40)

    def test_exact_revision_width_and_provenance_types(self):
        data = vars(self.provenance).copy()
        # A 41..63-digit string is not an immutable SHA-1/SHA-256 revision.
        for width in (39, 41, 63, 65):
            with self.subTest(width=width), self.assertRaises(TrajectoryError):
                Provenance(**{**data, "dataset_revision": "a" * width})
        self.assertEqual(
            Provenance(**{**data, "dataset_revision": "a" * 64}).dataset_revision,
            "a" * 64,
        )

        class Spoof(str):
            def __eq__(self, other):
                return True

        with self.assertRaises(TrajectoryError):
            Provenance(**{**data, "collector": Spoof("invalid")})
        with self.assertRaises(TrajectoryError):
            Trajectory(self.provenance, 0, (Frame(0., 0, (0.,), (0.,)),),
                       schema=Spoof("invalid"))

    def test_frozen_comparator_fixed_seed_heldout_and_no_leakage(self):
        episodes = [self.load(i) for i in range(6)]
        first = benchmark(episodes, seed=42)
        self.assertEqual(first, benchmark(reversed(episodes), seed=42))
        self.assertEqual(first["schema"], BENCHMARK_SCHEMA)
        self.assertEqual(first["nearest_state_mse"], 0)
        self.assertAlmostEqual(first["frozen_zero_mse"], .75 ** 2)
        self.assertTrue(set(first["training_digests"]).isdisjoint(first["heldout_digests"]))
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        with self.assertRaisesRegex(TrajectoryError, "duplicate"):
            benchmark([episodes[0], episodes[0], episodes[1]])

    def test_counterexample_learned_baseline_not_always_better(self):
        episodes = [self.load(i, action=(0.0 if i == 0 else 1.0)) for i in range(6)]
        result = benchmark(episodes, seed=3)
        self.assertTrue(math.isfinite(result["nearest_state_mse"]))
        self.assertTrue(math.isfinite(result["frozen_zero_mse"]))
        # No promise that nearest-neighbor is superior on contradictory labels.
        self.assertGreaterEqual(result["nearest_state_mse"], 0)

    def test_public_trajectory_constructor_rejects_invalid_frames(self):
        with self.assertRaises(TrajectoryError):
            Trajectory(self.provenance, 0, (Frame(0., 0, (0.,), (0.,)),
                                           Frame(0., 1, (0.,), (0.,))))
        with self.assertRaises(TrajectoryError):
            Trajectory(self.provenance, 0, (Frame(0., 0, (0.,), (10**1000,)),))
        with self.assertRaises(TrajectoryError):
            import_decoded_rows([dict(self.rows()[0], action=[10**1000])],
                                provenance=self.provenance, episode_index=0)

    def test_benchmark_total_budget_is_bounded(self):
        episodes = []
        for ep in range(3):
            rows = [{"episode_index": ep, "frame_index": i,
                     "timestamp": float(i), "observation.state": [0.], "action": [0.]}
                    for i in range(180)]
            episodes.append(import_decoded_rows(rows, provenance=self.provenance, episode_index=ep))
        with self.assertRaisesRegex(TrajectoryError, "total frame budget"):
            benchmark(episodes)

    def test_dataset_rejects_wrong_shape_before_decode(self):
        class Huge:
            shape = (10000000,)
            def tolist(self):
                raise AssertionError("tolist must never be called")
        rows = self.rows()
        rows[0]["observation.state"] = Huge()
        with self.assertRaises(TrajectoryError):
            import_lerobot_dataset(rows, provenance=self.provenance,
                                   episode_index=0, start=0, stop=3)


if __name__ == "__main__":
    unittest.main()
