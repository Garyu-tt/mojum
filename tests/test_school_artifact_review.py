"""Regression tests for the offline school artifact review."""
from __future__ import annotations

import copy
import hashlib
import unittest

from mojum.school_activity import PHASES, run_activity
from mojum.school_artifact_review import (
    REVIEW_SCHEMA,
    SchoolArtifactReviewError,
    review_run,
)


CATALOG = {
    "schema": "mojum.school-catalog/1",
    "learning_loop": list(PHASES),
    "sections": [
        {
            "id": "visual_arts",
            "title": "Visual arts",
            "skills": ["drawing"],
            "artifact_types": ["SVG drawing"],
            "cross_subject_ids": ["music"],
            "starter_activity": {
                "id": "visual_arts.starter",
                "prompt": "Draw a simple shape",
                "observation": "Describe the shape",
            },
            "implementation_status": "PLANNED",
        },
        {
            "id": "music",
            "title": "Music",
            "skills": ["rhythm"],
            "artifact_types": ["rhythm grid"],
            "cross_subject_ids": ["visual_arts"],
            "starter_activity": {
                "id": "music.starter",
                "prompt": "Make a rhythm",
                "observation": "Describe the pulse",
            },
            "implementation_status": "PLANNED",
        },
    ],
}


def art_run(make, revise):
    script = {
        "observe": "I see a blank page",
        "experiment": "Try two line lengths",
        "make": {"strokes": make},
        "revise": {"strokes": revise},
        "reflect": "Describe what changed",
    }
    return run_activity(
        CATALOG, "visual_arts.starter", script,
        sim_tick=40, learner_ref="private-learner-marker",
    )


def rhythm_run(make, revise):
    script = {
        "observe": "Listen to an even pulse",
        "experiment": "Try a different spacing",
        "make": {"pulses": make},
        "revise": {"pulses": revise},
        "reflect": "Describe the rhythm",
    }
    return run_activity(
        CATALOG, "music.starter", script,
        sim_tick=70, learner_ref="private-learner-marker",
    )


def replace_artifact(run, index, content):
    item = run["artifacts"][index]
    item["content"] = content
    item["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    phase = item["phase"]
    for event in run["events"]:
        if event["phase"] == phase:
            event["artifact_sha256"] = item["sha256"]
    run["feedback"]["revision_changed"] = (
        run["artifacts"][0]["sha256"] != run["artifacts"][1]["sha256"]
    )


class SchoolArtifactReviewTests(unittest.TestCase):
    def test_svg_reports_bounded_structure_and_revision_delta(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 4, "y2": 0}],
            [
                {"x1": 0, "y1": 0, "x2": 4, "y2": 0},
                {"x1": 1, "y1": 1, "x2": 5, "y2": 3},
            ],
        )
        report = review_run(run)
        self.assertEqual(report["schema"], REVIEW_SCHEMA)
        self.assertEqual(report["activity_kind"], "svg")
        self.assertEqual(report["artifacts"][0]["metrics"]["stroke_count"], 1)
        self.assertEqual(report["artifacts"][0]["metrics"]["manhattan_length_units"], 4)
        self.assertEqual(report["artifacts"][1]["metrics"]["stroke_count"], 2)
        self.assertEqual(report["artifacts"][1]["metrics"]["manhattan_length_units"], 10)
        self.assertEqual(report["comparison"]["added_strokes"], 1)
        self.assertEqual(report["comparison"]["removed_strokes"], 0)
        self.assertEqual(report["comparison"]["manhattan_length_delta"], 6)

    def test_unchanged_svg_is_reported_without_quality_judgement(self):
        strokes = [{"x1": 2, "y1": 3, "x2": 7, "y2": 9}]
        report = review_run(art_run(strokes, strokes))
        self.assertFalse(report["comparison"]["changed"])
        self.assertIn("No quality", " ".join(report["limitations"]))

    def test_rhythm_reports_cyclic_gaps_and_pulse_delta(self):
        report = review_run(rhythm_run([0, 16, 32, 48], [0, 12, 24, 36, 48, 60]))
        self.assertEqual(report["activity_kind"], "rhythm")
        self.assertEqual(report["artifacts"][0]["metrics"]["cyclic_gap_steps"], [16, 16, 16, 16])
        self.assertEqual(report["artifacts"][1]["metrics"]["cyclic_gap_steps"], [12, 12, 12, 12, 12, 4])
        self.assertEqual(report["comparison"]["added_pulses"], 4)
        self.assertEqual(report["comparison"]["removed_pulses"], 2)
        self.assertEqual(report["comparison"]["symmetric_difference"], 6)
        self.assertEqual(report["comparison"]["pulse_count_delta"], 2)

    def test_single_pulse_has_one_full_cycle_gap(self):
        report = review_run(rhythm_run([8], [8]))
        self.assertEqual(report["artifacts"][0]["metrics"]["cyclic_gap_steps"], [64])
        self.assertFalse(report["comparison"]["changed"])

    def test_report_is_deterministic_and_does_not_mutate_run(self):
        run = rhythm_run([0, 16, 32, 48], [0, 12, 24, 36, 48, 60])
        before = copy.deepcopy(run)
        self.assertEqual(review_run(run), review_run(run))
        self.assertEqual(run, before)

    def test_report_does_not_disclose_learner_or_artifact_content(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        report_text = repr(review_run(run))
        self.assertNotIn("private-learner-marker", report_text)
        self.assertNotIn("<svg", report_text)
        self.assertNotIn("artifact submitted", report_text)

    def test_hostile_python_schema_value_fails_closed(self):
        class Hostile:
            def __ne__(self, _other):
                raise RuntimeError("comparison must never escape")

        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        run["schema"] = Hostile()
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_input_digest_tampering_is_rejected(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        run["artifacts"][0]["content"] += " "
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_invalid_private_event_chain_is_rejected(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        run["events"][2]["sim_tick"] += 1
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_non_private_run_is_rejected(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        run["visibility"] = "PUBLIC"
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_svg_doctype_and_entity_are_rejected(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        replace_artifact(run, 0, '<!DOCTYPE svg [<!ENTITY x "bad">]><svg/>')
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_svg_script_and_unknown_elements_are_rejected(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        replace_artifact(run, 0, (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
            'role="img" aria-label="Synthetic drawing"><script/></svg>'
        ))
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_svg_out_of_range_coordinate_is_rejected(self):
        run = art_run(
            [{"x1": 0, "y1": 0, "x2": 1, "y2": 1}],
            [{"x1": 0, "y1": 0, "x2": 2, "y2": 2}],
        )
        replace_artifact(run, 0, (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
            'role="img" aria-label="Synthetic drawing"><rect width="64" height="64" '
            'fill="white"/><g fill="none" stroke="black" stroke-width="2" '
            'stroke-linecap="round"><path d="M 0 0 L 64 0"/></g></svg>'
        ))
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_duplicate_rhythm_json_keys_are_rejected(self):
        run = rhythm_run([0, 16], [0, 12])
        replace_artifact(run, 0, (
            '{"schema":"mojum.rhythm-grid/1","grid_steps":64,'
            '"pulses":[0],"pulses":[16]}'
        ))
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_rhythm_nonfinite_values_are_rejected(self):
        run = rhythm_run([0, 16], [0, 12])
        replace_artifact(run, 0, (
            '{"schema":"mojum.rhythm-grid/1","grid_steps":64,"pulses":[NaN]}'
        ))
        with self.assertRaises(SchoolArtifactReviewError):
            review_run(run)

    def test_rhythm_unsorted_or_duplicate_pulses_are_rejected(self):
        for payload in ('[16,0]', '[0,0]'):
            with self.subTest(payload=payload):
                run = rhythm_run([0, 16], [0, 12])
                replace_artifact(run, 0, (
                    '{"schema":"mojum.rhythm-grid/1","grid_steps":64,"pulses":'
                    + payload + '}'
                ))
                with self.assertRaises(SchoolArtifactReviewError):
                    review_run(run)

    def test_rhythm_out_of_range_and_wrong_types_are_rejected(self):
        for pulses in ('[0,64]', '[0,true]', '[0,1.5]'):
            with self.subTest(pulses=pulses):
                run = rhythm_run([0, 16], [0, 12])
                replace_artifact(run, 0, (
                    '{"schema":"mojum.rhythm-grid/1","grid_steps":64,"pulses":'
                    + pulses + '}'
                ))
                with self.assertRaises(SchoolArtifactReviewError):
                    review_run(run)


if __name__ == "__main__":
    unittest.main()
