"""Structural QA for the proposed school catalog (NOT a learning-engine test)."""
import json
import unittest
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class SchoolCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def test_schema_and_honest_status(self):
        self.assertEqual(self.catalog["schema"], "mojum.school-catalog/1")
        self.assertEqual(self.catalog["status"], "CATALOG_ONLY_NOT_EXECUTABLE")
        self.assertEqual(self.catalog["learning_loop"], [
            "observe", "experiment", "make", "revise", "reflect"
        ])

    def test_sections_have_stable_distinct_ids(self):
        sections = self.catalog["sections"]
        ids = [s["id"] for s in sections]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 11)
        self.assertIn("music", ids)
        self.assertIn("visual_arts", ids)
        for section in sections:
            with self.subTest(section=section["id"]):
                self.assertTrue(section["id"].isascii())
                self.assertTrue(section["id"].replace("_", "").isalnum())
                self.assertTrue(section["title"].strip())
                self.assertTrue(section["skills"])
                self.assertTrue(section["artifact_types"])
                self.assertEqual(section["implementation_status"], "PLANNED")

    def test_cross_subject_references_resolve(self):
        ids = {s["id"] for s in self.catalog["sections"]}
        for section in self.catalog["sections"]:
            with self.subTest(section=section["id"]):
                refs = section["cross_subject_ids"]
                self.assertEqual(len(refs), len(set(refs)))
                self.assertTrue(refs)
                self.assertNotIn(section["id"], refs)
                self.assertTrue(set(refs).issubset(ids))

    def test_starters_have_observable_evidence(self):
        starters = set()
        for section in self.catalog["sections"]:
            with self.subTest(section=section["id"]):
                activity = section["starter_activity"]
                self.assertEqual(activity["id"], section["id"] + ".starter")
                self.assertNotIn(activity["id"], starters)
                starters.add(activity["id"])
                self.assertTrue(activity["prompt"].strip())
                self.assertTrue(activity["observation"].strip())


if __name__ == "__main__":
    unittest.main()
