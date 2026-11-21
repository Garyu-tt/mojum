"""Out-of-fixture three-arm QA, intentionally without guaranteed winner."""
import json
import unittest

from mojum.neutral_probe_eval import (
    ARMS, SCENARIOS, SCHEMA, evaluate, pulses_for,
)
from mojum.world_room import Room


class NeutralProbeMultisceneTests(unittest.TestCase):
    def test_case_manifest_is_bounded_valid_and_varied(self):
        self.assertGreaterEqual(len(SCENARIOS), 8)
        self.assertLessEqual(len(SCENARIOS), 16)
        self.assertEqual(len({s["id"] for s in SCENARIOS}), len(SCENARIOS))
        self.assertEqual({s["pulse"] for s in SCENARIOS},
                         {"off", "seeded", "bursts", "alternating"})
        self.assertTrue(any(s["room"][2] for s in SCENARIOS))
        self.assertTrue(any(not s["room"][2] for s in SCENARIOS))
        self.assertGreaterEqual(len({s["room"][0] for s in SCENARIOS}), 4)
        for spec in SCENARIOS:
            with self.subTest(id=spec["id"]):
                room = Room(spec["room"][0], spec["room"][1],
                            tuple(tuple(a) for a in spec["room"][2]))
                self.assertIsInstance(room, Room)
                pulses = pulses_for(spec)
                self.assertEqual(len(pulses), spec["ticks"])
                self.assertTrue(all(type(bit) is bool for bit in pulses))
                self.assertLessEqual(len(pulses), 512)

    def test_deterministic_scenes_and_per_tick_conservation(self):
        first = evaluate()
        self.assertEqual(first, evaluate())
        self.assertEqual(first["schema"], SCHEMA)
        self.assertEqual(first["scene_count"], len(SCENARIOS))
        # Frozen after initial observed CI run, before policy tuning.
        self.assertEqual(first["suite_sha256"],
                         "b46a6a156cb250552b8069affc69703e8c48d1643eefe8eddbf5e6d80850283c")
        self.assertEqual(first["total_ticks_each_arm"],
                         sum(s["ticks"] for s in SCENARIOS))
        for scene in first["scenes"]:
            with self.subTest(scene=scene["id"]):
                self.assertEqual(set(scene["arms"]), set(ARMS))
                for arm in ARMS:
                    metrics = scene["arms"][arm]
                    self.assertEqual(
                        metrics["rest"] + metrics["attempts"],
                        scene["ticks"],
                    )
                    self.assertEqual(
                        metrics["blocked"] + metrics["successful_moves"],
                        metrics["attempts"],
                    )
                    self.assertGreaterEqual(metrics["positions"], 1)
                    self.assertLessEqual(metrics["longest_rest"], scene["ticks"])
                    self.assertEqual(len(metrics["trace_sha256"]), 64)
                self.assertEqual(len(scene["fixture_sha256"]), 64)
                self.assertEqual(len(scene["pulse_sha256"]), 64)
        self.assertEqual(json.loads(json.dumps(first)), first)

    def test_aggregate_is_actual_counts_not_safety_claim(self):
        report = evaluate()
        for arm in ARMS:
            totals = report["totals"][arm]
            for key in ("blocked", "rest", "attempts", "successful_moves"):
                self.assertEqual(
                    totals[key],
                    sum(scene["arms"][arm][key] for scene in report["scenes"]),
                )
            self.assertEqual(totals["rest"] + totals["attempts"],
                             report["total_ticks_each_arm"])
            self.assertEqual(totals["blocked"] + totals["successful_moves"],
                             totals["attempts"])
        wins = report["local_vs_frozen"]
        self.assertEqual(
            wins["fewer_blocked_scenes"] +
            wins["equal_blocked_scenes"] +
            wins["more_blocked_scenes"],
            len(report["scenes"]),
        )
        self.assertEqual(
            report["classification"],
            "PUBLIC_SYNTHETIC_PREDECLARED_CASES_NOT_BLINDED",
        )
        evidence = report["evidence_quality"]
        actual_contact_ids = [
            scene["id"] for scene in report["scenes"]
            if scene["arms"]["frozen"]["blocked"] > 0
        ]
        self.assertEqual(evidence["frozen_contact_scene_ids"],
                         actual_contact_ids)
        self.assertEqual(evidence["frozen_contact_scene_count"],
                         len(actual_contact_ids))
        self.assertEqual(evidence["no_contact_frozen_scene_count"],
                         len(report["scenes"]) - len(actual_contact_ids))
        self.assertEqual(evidence["behavior_divergence_scene_count"],
                         len(evidence["behavior_divergence_scene_ids"]))
        self.assertTrue(evidence["no_generalization_claim"])
        self.assertEqual(evidence["status"], "LOW_CONTACT_EXPOSURE")

    def test_pulse_generation_has_no_global_rng_or_wall_clock(self):
        seeded = next(s for s in SCENARIOS if s["pulse"] == "seeded")
        self.assertEqual(pulses_for(seeded), pulses_for(dict(seeded)))
        for item in SCENARIOS:
            self.assertEqual(pulses_for(item), pulses_for(item))
        with self.assertRaises(ValueError):
            pulses_for(dict(seeded, ticks=513))
        with self.assertRaises(ValueError):
            pulses_for(dict(seeded, pulse="hidden_oracle"))
        with self.assertRaises(ValueError):
            pulses_for(dict(seeded, seed=-1))


if __name__ == "__main__":
    unittest.main()
