"""Structural tests for proposed spatial and logistics lessons, not learner QA."""
import json
import unittest
from pathlib import Path

from mojum.school import validate_catalog as validate_scripted_catalog
from mojum.school_activity import validate_catalog as validate_activity_catalog

CATALOG = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"
CORE = {"architecture", "sculpture", "logistics", "urban_planning",
        "structural_engineering", "design", "craft_making",
        "game_based_learning"}
LEVELS = {"foundation": 0, "intermediate": 1, "advanced": 2}


class SchoolExtendedCurriculumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.subjects = {section["id"]: section for section in cls.catalog["sections"]}

    def test_existing_consumers_accept_extension(self):
        self.assertEqual(self.catalog["schema"], "mojum.school-catalog/1")
        self.assertEqual(self.catalog["lesson_plan_status"], "PLANNED_NOT_EXECUTABLE")
        self.assertEqual(self.catalog["status"], "CATALOG_ONLY_NOT_EXECUTABLE")
        self.assertEqual(self.catalog["lesson_levels"], list(LEVELS))
        self.assertIs(validate_scripted_catalog(self.catalog), self.catalog)
        self.assertIs(validate_activity_catalog(self.catalog), self.catalog)

    def test_core_subjects_and_observable_lessons(self):
        self.assertTrue(CORE <= self.subjects.keys())
        self.assertGreaterEqual(len(self.subjects["logistics"]["lessons"]), 5)
        all_ids = set()
        for subject_id in sorted(CORE):
            section = self.subjects[subject_id]
            lessons = section["lessons"]
            self.assertGreaterEqual(len(lessons), 2)
            previous_level = -1
            for lesson in lessons:
                with self.subTest(lesson=lesson["id"]):
                    self.assertEqual(set(lesson), {"id", "title", "level", "goal",
                                                   "activity", "evidence", "transfer_to"})
                    self.assertTrue(lesson["id"].startswith(subject_id + "."))
                    self.assertNotIn(lesson["id"], all_ids)
                    all_ids.add(lesson["id"])
                    self.assertIn(lesson["level"], LEVELS)
                    current_level = LEVELS[lesson["level"]]
                    self.assertGreaterEqual(current_level, previous_level)
                    previous_level = current_level
                    for field in ("title", "goal", "activity", "evidence"):
                        self.assertIsInstance(lesson[field], str)
                        self.assertTrue(lesson[field].strip())
                    refs = lesson["transfer_to"]
                    self.assertTrue(refs)
                    self.assertEqual(len(refs), len(set(refs)))
                    self.assertNotIn(subject_id, refs)
                    self.assertTrue(set(refs) <= self.subjects.keys())

    def test_logistics_has_stock_routing_schedule_and_failure(self):
        ids = {lesson["id"] for lesson in self.subjects["logistics"]["lessons"]}
        self.assertEqual(ids, {
            "logistics.route_01", "logistics.inventory_02",
            "logistics.scheduling_03", "logistics.disruption_04",
            "logistics.resilience_05", "logistics.factory_06"})
        self.assertIn("logistics", self.subjects["architecture"]["cross_subject_ids"])
        self.assertIn("architecture", self.subjects["logistics"]["cross_subject_ids"])


    def test_game_reference_cases_are_optional_and_cross_subject(self):
        games = self.subjects["game_based_learning"]
        cases = games["reference_games"]
        names = {case["title"] for case in cases}
        self.assertIn("Factorio", names)
        self.assertIn("Minecraft", names)
        self.assertEqual(len(names), len(cases))
        self.assertEqual(len(games["lessons"]), 6)
        for case in cases:
            with self.subTest(game=case["title"]):
                self.assertEqual(case["access"], "OPTIONAL_LICENSED_GAME_OR_ORIGINAL_SIM")
                self.assertTrue(case["learning_focus"].strip())
                self.assertTrue(set(case["subject_ids"]) <= self.subjects.keys())
        for sid in ("logistics", "architecture", "design", "sculpture", "music"):
            self.assertIn("game_based_learning", self.subjects[sid]["cross_subject_ids"])
        self.assertIn("no_mandatory_proprietary_games_or_online_services",
                      self.catalog["principles"])
        self.assertIn("play_without_compulsory_assessment",
                      self.catalog["principles"])
        self.assertIn("no_coercive_retention_or_reward_pressure",
                      self.catalog["principles"])

    def test_factorio_lab_is_curriculum_only(self):
        logistics = self.subjects["logistics"]
        labs = [lesson for lesson in logistics["lessons"]
                if lesson["id"] == "logistics.factory_06"]
        self.assertEqual(len(labs), 1)
        self.assertIn("Factorio", labs[0]["title"])
        self.assertIn("виртуальную минуту", labs[0]["evidence"])
        games = self.subjects["game_based_learning"]
        self.assertTrue(any(l["id"] == "game_based_learning.factory_03"
                            for l in games["lessons"]))
        self.assertEqual(games["implementation_status"], "PLANNED")

    def test_core_does_not_claim_executable_lessons(self):
        for subject_id in CORE:
            with self.subTest(subject_id=subject_id):
                self.assertEqual(self.subjects[subject_id]["implementation_status"], "PLANNED")


if __name__ == "__main__":
    unittest.main()
