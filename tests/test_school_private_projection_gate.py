"""Independent school R0.2 QA: PRIVATE projection must fail closed."""
import copy
import json
import unittest
from pathlib import Path

from mojum.school_activity import (
    SchoolError, compare_trials, load_catalog, redacted_summary, run_activity,
)

CATALOG = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


def fixture_script():
    return {
        "observe": "PERSONAL_TEXT_MUST_STAY_PRIVATE",
        "experiment": "test a diagonal",
        "make": {"strokes": [{"x1": 1, "y1": 1, "x2": 10, "y2": 10}]},
        "revise": {"strokes": [{"x1": 1, "y1": 1, "x2": 11, "y2": 10}]},
        "reflect": "another PERSONAL_TEXT_MUST_STAY_PRIVATE",
    }


class PrivateProjectionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG)

    def setUp(self):
        self.run = run_activity(
            self.catalog, "visual_arts.starter", fixture_script(),
            sim_tick=10, learner_ref="secret-fixture-learner",
        )

    def test_real_run_redacts_note_identity_and_artifacts(self):
        summary = redacted_summary(self.run)
        encoded = json.dumps(summary)
        self.assertEqual(summary["visibility"], "PUBLIC_REDACTED")
        self.assertEqual(summary["activity_id"], "visual_arts.starter")
        self.assertNotIn("PERSONAL_TEXT", encoded)
        self.assertNotIn("secret-fixture", encoded)
        self.assertNotIn("sha256", encoded)
        self.assertEqual(summary["phase_count"], 5)
        self.assertEqual(summary["artifact_count"], 2)

    def test_bad_activity_ids_cannot_escape_as_uncaught_type_errors(self):
        for activity in ([], {}, 3, None, True, "visual_arts.starter.EXFILTRATE"):
            with self.subTest(activity=activity), self.assertRaises(SchoolError):
                run_activity(self.catalog, activity, fixture_script(), sim_tick=0)

    def test_public_summary_refuses_tampered_and_broken_runs(self):
        def mutate_activity(x): x["activity_id"] = "secret-learner-identifier"
        def mutate_events(x): x["events"] = None
        def mutate_artifacts(x): x["artifacts"] = None
        def mutate_wrong_phase(x): x["events"][0]["phase"] = "reflect"
        def mutate_private_label(x): x["artifacts"][1]["visibility"] = "PUBLIC"
        def mutate_hash(x): x["artifacts"][1]["content"] += "secret"
        def mutate_learner(x): x["events"][1]["learner_ref"] = "other-person"
        def mutate_tick(x): x["events"][1]["sim_tick"] = 999
        def mutate_phase_hash(x): x["events"][2]["artifact_sha256"] = "wrong"
        def mutate_observation(x): x["events"][0]["observation"] = {"unexpected": "object"}
        def mutate_missing_feedback(x): x.pop("feedback")
        def mutate_feedback_visibility(x): x["feedback"]["visibility"] = "PUBLIC"
        def mutate_feedback_revision(x): x["feedback"]["revision_changed"] = not x["feedback"]["revision_changed"]
        def mutate_feedback_phases(x): x["feedback"]["reviewed_phases"].reverse()
        def mutate_unexpected_event_field(x): x["events"][0]["private_extra"] = "unvalidated"
        for mutation in (mutate_activity, mutate_events, mutate_artifacts,
                         mutate_wrong_phase, mutate_private_label, mutate_hash,
                         mutate_learner, mutate_tick, mutate_phase_hash,
                         mutate_observation, mutate_missing_feedback,
                         mutate_feedback_visibility, mutate_feedback_revision,
                         mutate_feedback_phases, mutate_unexpected_event_field):
            with self.subTest(mutation=mutation.__name__):
                broken = copy.deepcopy(self.run)
                mutation(broken)
                with self.assertRaises(SchoolError):
                    redacted_summary(broken)

    def test_maximum_valid_tick_preserves_final_phase_boundary(self):
        from mojum.school_activity import MAX_SIM_TICK
        run = run_activity(self.catalog, "visual_arts.starter", fixture_script(),
                           sim_tick=MAX_SIM_TICK)
        self.assertEqual(run["events"][-1]["sim_tick"], MAX_SIM_TICK + 4)
        self.assertEqual(redacted_summary(run)["phase_count"], 5)
        with self.assertRaises(SchoolError):
            run_activity(self.catalog, "visual_arts.starter", fixture_script(),
                         sim_tick=MAX_SIM_TICK + 1)

    def test_compare_trial_checks_both_source_shapes(self):
        later = run_activity(
            self.catalog, "visual_arts.starter", fixture_script(),
            sim_tick=20, learner_ref="secret-fixture-learner",
        )
        self.assertFalse(compare_trials(self.run, later)["transfer_measured"])
        broken = copy.deepcopy(later)
        broken["artifacts"][0]["sha256"] = "forged"
        with self.assertRaises(SchoolError):
            compare_trials(self.run, broken)
        with self.assertRaises(SchoolError):
            compare_trials({"schema": "mojum.school-run/1", "visibility": "PRIVATE"}, later)
        with self.assertRaises(SchoolError):
            compare_trials(self.run, {"schema": "mojum.school-run/1", "visibility": "PRIVATE", "artifacts": None})


if __name__ == "__main__":
    unittest.main()
