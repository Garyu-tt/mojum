"""QA: overlong caller-provided lesson IDs must fail before hashing or lookup."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from mojum.school_lesson_plan import LessonPlanError, plan_voluntary_lessons


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class LessonPlanSectionIdPreflightQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_oversized_id_is_rejected_before_any_set_hashing(self):
        # This still fits comfortably in a unit test but exceeds the catalog
        # identifier limit by orders of magnitude. The trap distinguishes
        # O(1) admission from "hash first, reject unknown ID later".
        oversized_id = "x" * 100_000
        with patch(
            "mojum.school_lesson_plan.set", create=True,
            side_effect=AssertionError("overlong ID reached set()"),
        ):
            with self.assertRaisesRegex(LessonPlanError, "identifier bound"):
                plan_voluntary_lessons(self.catalog, [oversized_id], consent=True)

    def test_normal_ids_and_unknown_bounded_ids_are_unchanged(self):
        plan = plan_voluntary_lessons(self.catalog, ["music"], consent=True)
        self.assertEqual(plan["lessons"][0]["section_id"], "music")
        with self.assertRaisesRegex(LessonPlanError, "unknown school section"):
            plan_voluntary_lessons(self.catalog, ["x" * 48], consent=True)


if __name__ == "__main__":
    unittest.main()
