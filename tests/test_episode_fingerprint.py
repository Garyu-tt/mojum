import unittest
from unittest.mock import patch
from mojum.episode_fingerprint import FingerprintError, fingerprint_episode

def frame(tick=0, **changes):
    item = dict(tick=tick, observation={"x": 1}, action=[0], reward=0.0, terminated=False, truncated=False)
    item.update(changes)
    return item

class FingerprintTests(unittest.TestCase):
    def test_determinism_and_key_order(self):
        a = frame(observation={"a": 1, "b": 2})
        b = frame(observation={"b": 2, "a": 1})
        self.assertEqual(fingerprint_episode([a], episode_id="demo"), fingerprint_episode([b], episode_id="demo"))

    def test_changes_digest(self):
        a = fingerprint_episode([frame()], episode_id="demo")
        b = fingerprint_episode([frame(reward=1)], episode_id="demo")
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.assertNotEqual(a["sha256"], fingerprint_episode([frame()], episode_id="other")["sha256"])

    def test_rejects_bad_ticks(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(tick=1)], episode_id="demo")

    def test_rejects_early_terminal(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(terminated=True), frame(1)], episode_id="demo")

    def test_rejects_nan(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(reward=float("nan"))], episode_id="demo")

    def test_rejects_unbounded_input(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(observation="x" * 4097)], episode_id="demo")

    def test_rejects_unexpected_shape(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([{"tick": 0}], episode_id="demo")

    def test_rejects_unpaired_surrogate_episode_id(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame()], episode_id="bad-\ud800")

    def test_rejects_oversized_integer_reward_without_overflow(self):
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(reward=10 ** 1000)], episode_id="demo")

    def test_rejects_cycles_and_aliases(self):
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(observation=cyclic)], episode_id="demo")
        shared = [1, 2]
        with self.assertRaises(FingerprintError):
            fingerprint_episode([frame(observation=shared, action=shared)], episode_id="demo")

    def test_rejects_hostile_mapping_and_string_keys(self):
        from collections.abc import Mapping

        class HostileMapping(Mapping):
            def __getitem__(self, key):
                raise AssertionError("hostile mapping access must not execute")
            def __iter__(self):
                raise AssertionError("hostile mapping iteration must not execute")
            def __len__(self):
                return 6

        with self.assertRaises(FingerprintError):
            fingerprint_episode([HostileMapping()], episode_id="demo")

        class HostileKey(str):
            def __hash__(self):
                return hash(str(self))
            def __eq__(self, other):
                raise AssertionError("hostile key comparison must not execute")

        item = frame()
        item[HostileKey("tick")] = item.pop("tick")
        with self.assertRaises(FingerprintError):
            fingerprint_episode([item], episode_id="demo")

    def test_preflight_byte_budget_runs_before_serialization(self):
        large = [frame(observation={"text": "x" * 4096}) for _ in range(130)]
        with patch("mojum.episode_fingerprint.json.dumps",
                   side_effect=AssertionError("serializer ran before preflight")):
            with self.assertRaises(FingerprintError):
                fingerprint_episode(large, episode_id="demo")

if __name__ == "__main__":
    unittest.main()
