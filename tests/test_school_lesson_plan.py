"""Black-box QA for the opt-in, synthetic, read-only school planner."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.school_lesson_plan import (
    MAX_LESSONS, MAX_PHASE_TICKS, MAX_TICK, PLAN_SCHEMA,
    LessonPlanError, plan_voluntary_lessons,
)

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class VoluntaryLessonPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_two_subjects_have_ordered_contiguous_five_phase_timeline(self):
        original = copy.deepcopy(self.catalog)
        plan = plan_voluntary_lessons(
            self.catalog, ["music", "visual_arts"],
            consent=True, start_tick=10, phase_ticks=3,
            require_cross_subject_links=True,
        )
        self.assertEqual(plan["schema"], PLAN_SCHEMA)
        self.assertEqual([x["section_id"] for x in plan["lessons"]],
                         ["music", "visual_arts"])
        self.assertEqual([x["activity_id"] for x in plan["lessons"]],
                         ["music.starter", "visual_arts.starter"])
        self.assertEqual(plan["lessons"][0]["start_tick"], 10)
        self.assertEqual(plan["lessons"][0]["end_tick"], 24)
        self.assertEqual(plan["lessons"][1]["start_tick"], 25)
        self.assertEqual(plan["lessons"][1]["end_tick"], 39)
        self.assertEqual([p["phase"] for p in plan["lessons"][0]["phases"]],
                         ["observe", "experiment", "make", "revise", "reflect"])
        self.assertEqual(plan["lessons"][0]["phases"][1],
                         {"phase": "experiment", "start_tick": 13, "end_tick": 15})
        self.assertTrue(plan["consent"])
        self.assertFalse(plan["executed"])
        self.assertFalse(plan["physical_actuation"])
        self.assertEqual(self.catalog, original)

    def test_identical_calls_are_replayable_with_verifiable_digest(self):
        left = plan_voluntary_lessons(self.catalog, ["music"], consent=True)
        right = plan_voluntary_lessons(self.catalog, ["music"], consent=True)
        self.assertEqual(left, right)
        canonical = json.dumps({k: v for k, v in left.items() if k != "digest_sha256"},
                               sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.assertEqual(left["digest_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(len(left["digest_sha256"]), 64)
        self.assertEqual(len(left["catalog_sha256"]), 64)

    def test_explicit_consent_only(self):
        for consent in (False, None, 0, 1, "yes", [], {}):
            with self.subTest(consent=consent), self.assertRaises(LessonPlanError):
                plan_voluntary_lessons(self.catalog, ["music"], consent=consent)

    def test_reject_unknown_duplicate_and_non_plain_section_ids(self):
        for ids in ([], ["does_not_exist"], ["music", "music"],
                    ["music", 1], ["music", True], "music", ("music",),
                    [b"music"], ["music"] * (MAX_LESSONS + 1)):
            with self.subTest(ids=str(ids)[:80]), self.assertRaises(LessonPlanError):
                plan_voluntary_lessons(self.catalog, ids, consent=True)

    def test_directed_cross_subject_links_are_opt_in(self):
        self.assertTrue(plan_voluntary_lessons(
            self.catalog, ["music", "visual_arts"], consent=True,
            require_cross_subject_links=True)["require_cross_subject_links"])
        with self.assertRaises(LessonPlanError):
            plan_voluntary_lessons(
                self.catalog, ["music", "logistics"], consent=True,
                require_cross_subject_links=True)
        # Unlinked itinerary is still possible if the *caller* allows it.
        plan = plan_voluntary_lessons(
            self.catalog, ["music", "logistics"], consent=True,
            require_cross_subject_links=False)
        self.assertEqual(len(plan["lessons"]), 2)
        for bad in (None, 1, 0, "false"):
            with self.subTest(bad=bad), self.assertRaises(LessonPlanError):
                plan_voluntary_lessons(self.catalog, ["music"], consent=True,
                                       require_cross_subject_links=bad)

    def test_clock_boundaries_and_type_confusion(self):
        safe_start = MAX_TICK - 4
        plan = plan_voluntary_lessons(self.catalog, ["music"], consent=True,
                                      start_tick=safe_start)
        self.assertEqual(plan["lessons"][0]["end_tick"], MAX_TICK)
        for tick in (-1, MAX_TICK - 3, MAX_TICK + 1, True, False, 1.0, "0"):
            with self.subTest(tick=tick), self.assertRaises(LessonPlanError):
                plan_voluntary_lessons(self.catalog, ["music"],
                                       consent=True, start_tick=tick)

    def test_phase_duration_bounds_and_overflow(self):
        plan = plan_voluntary_lessons(
            self.catalog, ["music"], consent=True, phase_ticks=MAX_PHASE_TICKS,
        )
        self.assertEqual(plan["lessons"][0]["end_tick"], 5 * MAX_PHASE_TICKS - 1)
        for duration in (0, -1, MAX_PHASE_TICKS + 1, True, 1.5, "1"):
            with self.subTest(duration=duration), self.assertRaises(LessonPlanError):
                plan_voluntary_lessons(self.catalog, ["music"],
                                       consent=True, phase_ticks=duration)
        with self.assertRaises(LessonPlanError):
            plan_voluntary_lessons(self.catalog, ["music"], consent=True,
                                   start_tick=MAX_TICK, phase_ticks=MAX_PHASE_TICKS)

    def test_reject_malformed_catalog(self):
        for bad in (None, [], "catalog", {"schema": "invalid"},
                    {**self.catalog, "schema": "invalid"}):
            with self.subTest(kind=type(bad)), self.assertRaises(LessonPlanError):
                plan_voluntary_lessons(bad, ["music"], consent=True)
        corrupt = copy.deepcopy(self.catalog)
        corrupt["sections"][0]["starter_activity"]["id"] = "wrong.starter"
        with self.assertRaises(LessonPlanError):
            plan_voluntary_lessons(corrupt, ["music"], consent=True)

    def test_snapshot_is_independent_from_input_and_other_outputs(self):
        chosen = ["music"]
        first = plan_voluntary_lessons(self.catalog, chosen, consent=True)
        snapshot = copy.deepcopy(first)
        chosen[0] = "logistics"
        first["lessons"][0]["phases"][0]["start_tick"] = -999
        first["lessons"][0]["activity_id"] = "forged"
        second = plan_voluntary_lessons(self.catalog, ["music"], consent=True)
        self.assertEqual(second, snapshot)

    def test_plan_does_not_read_files_or_write_external_outputs(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected I/O")):
            plan = plan_voluntary_lessons(self.catalog, ["music"], consent=True)
        self.assertEqual(plan["provenance"], "OFFLINE_SYNTHETIC_CATALOG_PROPOSAL")
        encoded = json.dumps(plan, sort_keys=True)
        self.assertNotIn("Сравнить два ритма", encoded)
        self.assertNotIn("prompt", encoded)
        self.assertNotIn("observation", encoded)
        self.assertNotIn("learner_ref", encoded)


if __name__ == "__main__":
    unittest.main()
