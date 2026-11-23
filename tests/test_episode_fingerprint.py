import unittest
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

if __name__ == "__main__":
    unittest.main()
