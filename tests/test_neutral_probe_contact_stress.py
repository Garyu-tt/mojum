"""Contact-exposure stress gate tests for the neutral-probe research arms."""
import json
import unittest

from mojum.neutral_probe_contact_stress import (
    ARMS, CASES, CLASSIFICATION, MIN_CONTACT_SCENES, evaluate, main,
)
from mojum.neutral_probe_eval import pulses_for
from mojum.world_room import Room


class ContactRichStressTests(unittest.TestCase):
    def test_declared_cases_are_bounded_and_intentionally_different(self):
        self.assertEqual(len(CASES), 12)
        self.assertEqual(len({c["id"] for c in CASES}), 12)
        self.assertEqual({c["pulse"] for c in CASES},
                         {"alternating", "seeded", "bursts", "off"})
        self.assertTrue(any(c["room"][2] for c in CASES))
        self.assertTrue(any(not c["room"][2] for c in CASES))
        self.assertTrue(any(c["start"][0] == 0 for c in CASES))
        self.assertTrue(any(c["start"][1] == 0 for c in CASES))
        for spec in CASES:
            with self.subTest(case=spec["id"]):
                w, h, obstacles = spec["room"]
                self.assertIsInstance(Room(
                    w, h, tuple(tuple(rect) for rect in obstacles)), Room)
                self.assertEqual(len(pulses_for(spec)), spec["ticks"])

    def test_contact_gate_is_measured_from_frozen_not_learner(self):
        report = evaluate()
        exposure = report["evidence_quality"]
        self.assertEqual(report["classification"], CLASSIFICATION)
        self.assertTrue(exposure["not_blind_or_held_out"])
        self.assertTrue(exposure["no_generalization_claim"])
        actual = [
            row["id"] for row in report["scenes"]
            if row["arms"]["frozen"]["blocked"] > 0
        ]
        self.assertEqual(exposure["contact_scene_ids"], actual)
        self.assertEqual(exposure["contact_scene_count"], len(actual))
        self.assertTrue(exposure["passes_contact_gate"])
        self.assertGreaterEqual(len(actual), MIN_CONTACT_SCENES)
        self.assertEqual(exposure["status"], "PURPOSEFUL_CONTACT_EXPOSURE")

    def test_each_arm_invariant_and_unflattering_findings_allowed(self):
        result = evaluate()
        self.assertEqual(result["scene_count"], len(CASES))
        self.assertEqual(result["total_ticks_each_arm"],
                         sum(row["ticks"] for row in CASES))
        for scene in result["scenes"]:
            for arm in ARMS:
                values = scene["arms"][arm]
                self.assertEqual(values["attempts"] + values["rest"],
                                 scene["ticks"])
                self.assertEqual(values["success"] + values["blocked"],
                                 values["attempts"])
                self.assertLessEqual(values["unique_positions"], scene["ticks"])
                self.assertTrue(values["blocked_per_attempt"] is None or
                                0 <= values["blocked_per_attempt"] <= 1)
                self.assertTrue(0 <= values["successful_movement_per_tick"] <= 1)
                self.assertEqual(len(values["trace_sha256"]), 64)
        for arm in ARMS:
            totals = result["totals"][arm]
            self.assertEqual(totals["rest"] + totals["attempts"],
                             result["total_ticks_each_arm"])
            self.assertEqual(totals["blocked"] + totals["success"],
                             totals["attempts"])
        comparisons = result["local_vs_frozen"]
        self.assertEqual(sum(comparisons[k] for k in (
            "fewer_blocks", "equal_blocks", "more_blocks"
        )), len(CASES))
        # Do NOT assert a winner: recording poor results is essential.

    def test_reproducible_without_private_state_or_external_calls(self):
        a, b = evaluate(), evaluate()
        self.assertEqual(a, b)
        self.assertEqual(json.loads(json.dumps(a)), a)
        self.assertEqual(len(a["suite_sha256"]), 64)
        output = main()
        self.assertNotIn("scenes", output)
        self.assertNotIn("PRIVATE", json.dumps(output))


if __name__ == "__main__":
    unittest.main()
