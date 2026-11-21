"""Independent bounded CPU-only negative-control tests for Mojum probe."""
from contextlib import redirect_stdout
import io
import json
import unittest

from mojum.neutral_probe_trial import (
    COMPARISON_SCHEMA, MAX_TICKS, NeutralProbeError, compare, main, trial,
)
from mojum.world_room import Room


class NeutralProbeTrialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            "room": Room(0.16, 0.16),
            "start": (0.16, 0.16),
            "pulses": tuple(i % 2 == 0 for i in range(120)),
        }

    def test_pair_is_deterministic_and_has_identical_input_sequence(self):
        pair = compare(**self.fixture)
        self.assertEqual(pair, compare(**self.fixture))
        self.assertEqual(pair["schema"], COMPARISON_SCHEMA)
        adaptive, frozen = pair["adaptive"], pair["frozen"]
        self.assertEqual(adaptive["ticks"], 120)
        self.assertEqual(frozen["ticks"], 120)
        self.assertIsNone(frozen["learned_state"])
        self.assertEqual(adaptive["learned_state"]["updates"], 120)
        for index, (a, b) in enumerate(zip(adaptive["trace"], frozen["trace"])):
            self.assertEqual(a["tick"], index)
            self.assertEqual(b["tick"], index)
            self.assertEqual(a["heartbeat_pulse"], b["heartbeat_pulse"])
        self.assertEqual(adaptive["trace"][0]["action"],
                         frozen["trace"][0]["action"])

    def test_adaptive_changes_actions_from_actual_observed_contacts(self):
        pair = compare(**self.fixture)
        a = pair["adaptive"]
        f = pair["frozen"]
        self.assertGreater(f["blocked_count"], 0)
        self.assertGreater(a["learned_state"]["updates"], 0)
        self.assertTrue(any(x["blocked"] for x in a["trace"]))
        # The two policies start with identical innate logic. Only adaptive
        # policy is allowed to change after actual blocked outcomes.
        self.assertTrue(any(x["action"] != y["action"]
                            for x, y in zip(a["trace"], f["trace"])))

    def test_no_explicit_affect_or_privileged_motor_fields(self):
        for arm in ("adaptive", "frozen"):
            data = trial(**self.fixture, mode=arm)
            serialized = json.dumps(data, sort_keys=True, allow_nan=False)
            self.assertEqual(json.loads(serialized), data)
            for field in ("pain", "suffering", "reward", "valence",
                          "distress", "motor_capability", "actuator"):
                self.assertNotIn('"' + field + '"', serialized)
            self.assertEqual(data["classification"],
                             "SYNTHETIC_POINT_ROOM_RESEARCH_ONLY")
            self.assertLessEqual(data["blocked_count"], data["ticks"])

    def test_rejects_unbounded_invalid_and_ambiguous_inputs(self):
        valid = self.fixture
        invalid = (
            {"pulses": ()},
            {"pulses": (True,) * (MAX_TICKS + 1)},
            {"pulses": (True, 1, False)},
            {"pulses": "True"},
            {"pulses": [True, None]},
            {"start": (False, 0.1)},
            {"start": (float("nan"), 0.1)},
            {"start": (10**1000, 0.1)},
            {"mode": "goal_seek"},
            {"mode": True},
            {"room": None},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                kwargs = dict(valid, mode="adaptive")
                kwargs.update(changes)
                with self.assertRaises(NeutralProbeError):
                    trial(**kwargs)

    def test_cli_is_small_metadata_only_and_offline(self):
        with io.StringIO() as stream, redirect_stdout(stream):
            result = main(["--ticks", "40", "--mode", "compare"])
            printed = stream.getvalue()
        self.assertEqual(json.loads(printed), result)
        self.assertEqual(result["ticks"], 40)
        self.assertLess(len(printed), 512)
        self.assertNotIn("position_xy", printed)
        self.assertNotIn("learned_state", printed)

    def test_frozen_arm_does_not_update_hidden_policy(self):
        for length in (1, 5, 64):
            pulses = (False,) * length
            one = trial(room=Room(.16, .16), start=(.16, .16),
                        pulses=pulses, mode="frozen")
            self.assertIsNone(one["learned_state"])
            expected = ("rest", "x+", "y+", "x-", "y-")
            self.assertEqual([t["action"] for t in one["trace"]],
                             [expected[i % len(expected)] for i in range(length)])


if __name__ == "__main__":
    unittest.main()
