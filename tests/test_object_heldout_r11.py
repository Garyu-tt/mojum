"""R0.10 held-out negative-control suite: count false merges and splits."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from examples.object_continuity_heldout import compact_report, run_suite, scenarios


ROOT = Path(__file__).resolve().parent.parent


class ObjectContinuityHeldOutQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_suite()
        cls.cases = cls.report["cases"]

    def test_static_same_color_stays_separated_in_distinct_locations(self):
        result = self.cases["static_same_color_separated"]
        c = result["counts"]
        self.assertEqual(result["evaluated"], 12)
        self.assertEqual(c["hypotheses"], 2)
        self.assertEqual(c["identity_switches"], 0)
        self.assertEqual(c["splits"], 0)
        self.assertEqual(c["matched_reencounters"], 10)
        self.assertEqual(c["measured_contacts"], 12)

    def test_reencounter_after_dropout_preserves_unverified_local_track(self):
        result = self.cases["temporary_sensor_dropout"]
        c = result["counts"]
        self.assertEqual(result["evaluated"], 9)
        self.assertEqual(c["no_signal"], 3)
        self.assertEqual(c["hypotheses"], 1)
        self.assertEqual(c["possible_reencounters"], 1)
        self.assertEqual(c["splits"], 0)
        self.assertTrue(all(
            row["candidate"] is None
            for row in result["timeline_for_qa_only"] if row["oracle_only"] is None
        ))

    def test_rgb_jitter_is_truthfully_reported_as_a_fragility(self):
        result = self.cases["small_rgb_jitter"]
        c = result["counts"]
        self.assertEqual(result["evaluated"], 12)
        self.assertGreater(c["splits"], 0)
        self.assertGreater(c["hypotheses"], 1)
        self.assertEqual(c["identity_switches"], 0)
        self.assertTrue(all(row["identity_verified"] is False
                            for row in result["timeline_for_qa_only"]))

    def test_opt_in_numeric_tolerance_handles_one_step_rgb_jitter(self):
        raw = self.cases["small_rgb_jitter"]["counts"]
        tolerant = self.cases["small_rgb_jitter_tolerant"]["counts"]
        self.assertEqual(raw["splits"], 2)
        self.assertEqual(raw["hypotheses"], 3)
        self.assertEqual(tolerant["splits"], 0)
        self.assertEqual(tolerant["hypotheses"], 1)
        self.assertEqual(tolerant["identity_switches"], 0)
        self.assertEqual(tolerant["matched_reencounters"], 11)
        self.assertEqual(tolerant["measured_contacts"], 12)

    def test_color_tolerance_does_not_disable_measured_contact_guard(self):
        tolerant = self.cases["crossing_identical_colors_guarded_tolerant"]["counts"]
        strict = self.cases["crossing_identical_colors_guarded"]["counts"]
        self.assertEqual(tolerant["contaminated_contacts"], 0)
        self.assertEqual(tolerant["identity_switches"], 0)
        self.assertEqual(tolerant["contact_conflict_abstentions"], 2)
        self.assertEqual(tolerant["hypotheses"], strict["hypotheses"])

    def test_moving_single_object_can_fragment_into_several_tracks(self):
        result = self.cases["moving_held_out_toy"]
        c = result["counts"]
        self.assertEqual(result["evaluated"], 17)
        self.assertGreater(c["splits"], 0)
        self.assertGreater(c["hypotheses"], 1)
        self.assertEqual(c["identity_switches"], 0)
        self.assertEqual(
            len({row["oracle_only"] for row in result["timeline_for_qa_only"]}),
            1,
        )

    def test_crossing_identical_colors_exposes_wrong_local_identity(self):
        result = self.cases["crossing_identical_colors"]
        c = result["counts"]
        self.assertEqual(result["evaluated"], 4)
        self.assertEqual(c["hypotheses"], 2)
        self.assertEqual(c["identity_switches"], 2)
        self.assertEqual(c["contaminated_contacts"], 2)
        self.assertEqual(c["splits"], 0)
        events = result["timeline_for_qa_only"]
        self.assertNotEqual(events[0]["oracle_only"], events[2]["oracle_only"])
        self.assertEqual(events[0]["candidate"], events[2]["candidate"])

    def test_guarded_crossing_abstains_instead_of_poisoning_contact_memory(self):
        raw = self.cases["crossing_identical_colors"]["counts"]
        guarded = self.cases["crossing_identical_colors_guarded"]["counts"]
        self.assertEqual(raw["identity_switches"], 2)
        self.assertEqual(raw["contaminated_contacts"], 2)
        self.assertEqual(guarded["identity_switches"], 0)
        self.assertEqual(guarded["contaminated_contacts"], 0)
        self.assertEqual(guarded["contact_conflict_abstentions"], 2)
        self.assertEqual(guarded["hypotheses"], 2)
        self.assertEqual(guarded["measured_contacts"], 4)
        self.assertEqual(
            self.cases["crossing_identical_colors_guarded"]["timeline_for_qa_only"][2]["status"],
            "contact_conflict_abstention",
        )
        # No inference of true identity: abstention is a safety trade-off.
        self.assertTrue(all(
            record["identity_verified"] is False
            for record in self.cases["crossing_identical_colors_guarded"]["timeline_for_qa_only"]
        ))

    def test_default_report_has_no_oracle_timeline_or_scene_geometry(self):
        report = compact_report(self.report)
        encoded = json.dumps(report, sort_keys=True)
        self.assertEqual(report["schema"], "mojum.qa.object-continuity.heldout/1")
        self.assertEqual(report["oracle_scope"], "external_qa_only_never_a_policy_input")
        self.assertEqual(set(report["cases"]),
                         set(scenarios()) | {
                             "crossing_identical_colors_guarded",
                             "crossing_identical_colors_guarded_tolerant",
                             "small_rgb_jitter_tolerant",
                         })
        self.assertNotIn('"oracle_mapping_for_qa_only"', encoded)
        self.assertNotIn('"timeline_for_qa_only"', encoded)
        self.assertNotIn('"final_memory_snapshot"', encoded)
        self.assertNotIn('"toys"', encoded)
        self.assertNotIn('"oracle.a"', encoded)

    def test_metric_reproducibility_and_no_privileged_agent_field(self):
        self.assertEqual(self.report, run_suite())
        for source_file in ("src/mojum/object_memory.py",
                            "src/mojum/sensory_play.py"):
            source = (ROOT / source_file).read_text(encoding="utf-8")
            self.assertNotIn("oracle_only", source)
            self.assertNotIn('["oracle"]', source)
        for result in self.cases.values():
            for record in result["timeline_for_qa_only"]:
                self.assertFalse(record["identity_verified"])

    def test_cli_compact_and_opt_in_detailed_json(self):
        env = [sys.executable, "-m", "examples.object_continuity_heldout"]
        compact = json.loads(subprocess.check_output(env, text=True))
        detail = json.loads(subprocess.check_output(
            env + ["--with-qa-traces"], text=True,
        ))
        self.assertEqual(compact, compact_report(self.report))
        self.assertEqual(detail, self.report)
        # Compact synthetic-only metrics are safe for public CI logs and
        # make later baseline claims independently reproducible.
        print("MOJUM_OBJECT_QA_COMPACT=" + json.dumps(compact, sort_keys=True))
        self.assertTrue(any(
            row["oracle_only"] is not None
            for row in detail["cases"]["static_same_color_separated"]["timeline_for_qa_only"]
        ))


if __name__ == "__main__":
    unittest.main()
