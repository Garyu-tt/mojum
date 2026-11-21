"""Behavioral QA: PRIVATE art/music fixture, strict inputs, no I/O."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.school_activity import (SchoolError, compare_trials, load_catalog,
                                   redacted_summary, run_activity, validate_catalog)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


def art_script():
    return {"observe": "simple shape", "experiment": "try diagonals",
            "make": {"strokes": [{"x1": 1, "y1": 1, "x2": 10, "y2": 10}]},
            "revise": {"strokes": [{"x1": 1, "y1": 1, "x2": 11, "y2": 10}]},
            "reflect": "second version is different"}


def rhythm_script():
    return {"observe": "four steady beats", "experiment": "shift the last beat",
            "make": {"pulses": [0, 4, 8, 12]}, "revise": {"pulses": [0, 4, 8, 13]},
            "reflect": "one beat was moved"}


class SchoolActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_art_repeatable_svg_five_private_events(self):
        a = run_activity(self.catalog, "visual_arts.starter", art_script(), sim_tick=10)
        b = run_activity(self.catalog, "visual_arts.starter", art_script(), sim_tick=10)
        self.assertEqual(a, b)
        self.assertEqual([e["phase"] for e in a["events"]],
                         ["observe", "experiment", "make", "revise", "reflect"])
        self.assertEqual([e["sim_tick"] for e in a["events"]], [10, 11, 12, 13, 14])
        self.assertTrue(all(e["visibility"] == "PRIVATE" for e in a["events"]))
        self.assertTrue(a["feedback"]["revision_changed"])
        self.assertTrue(a["artifacts"][0]["content"].startswith("<svg "))
        for item in a["artifacts"]:
            self.assertEqual(hashlib.sha256(item["content"].encode()).hexdigest(), item["sha256"])

    def test_rhythm_json_and_independent_repeat(self):
        baseline = run_activity(self.catalog, "music.starter", rhythm_script(), sim_tick=0)
        follow = run_activity(self.catalog, "music.starter", rhythm_script(), sim_tick=10)
        parsed = json.loads(baseline["artifacts"][1]["content"])
        self.assertEqual(parsed["pulses"], [0, 4, 8, 13])
        result = compare_trials(baseline, follow)
        self.assertFalse(result["transfer_measured"])
        self.assertFalse(result["difference_from_baseline"])
        modified = rhythm_script()
        modified["revise"]["pulses"] = [0, 5, 8, 13]
        changed = run_activity(self.catalog, "music.starter", modified, sim_tick=20)
        self.assertTrue(compare_trials(baseline, changed)["difference_from_baseline"])
        with self.assertRaises(SchoolError):
            compare_trials(baseline, baseline)

    def test_public_projection_is_redacted(self):
        script = art_script()
        script["observe"] = "DO_NOT_EXPORT_PRIVATE_TEXT"
        run = run_activity(self.catalog, "visual_arts.starter", script,
                           learner_ref="private-person-001")
        projection = json.dumps(redacted_summary(run))
        self.assertNotIn("DO_NOT_EXPORT", projection)
        self.assertNotIn("private-person", projection)
        self.assertNotIn("sha256", projection)
        self.assertEqual(json.loads(projection)["visibility"], "PUBLIC_REDACTED")

    def test_bad_schema_references_and_duplicate_keys(self):
        bad = copy.deepcopy(self.catalog)
        bad["schema"] = "mojum.school-catalog/2"
        with self.assertRaises(SchoolError):
            validate_catalog(bad)
        bad = copy.deepcopy(self.catalog)
        bad["sections"][0]["cross_subject_ids"] = ["absent"]
        with self.assertRaises(SchoolError):
            validate_catalog(bad)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "catalog.json"
            f.write_text('{"schema":"mojum.school-catalog/1","schema":"mojum.school-catalog/1"}')
            with self.assertRaises(SchoolError):
                load_catalog(f)
            f.write_bytes(b"{" + b" " * 131_072 + b"}")
            with self.assertRaises(SchoolError):
                load_catalog(f)

    def test_malformed_budgets_and_atomic_rejection(self):
        for activity, script in (("visual_arts.starter", art_script()),
                                 ("music.starter", rhythm_script())):
            for tick in (True, -1, 2**31):
                with self.subTest(activity=activity, tick=tick), self.assertRaises(SchoolError):
                    run_activity(self.catalog, activity, script, sim_tick=tick)
        for malformed in (None, {}, {"observe": "only"}):
            with self.assertRaises(SchoolError):
                run_activity(self.catalog, "visual_arts.starter", malformed)
        s = art_script()
        s["make"]["strokes"] *= 33
        with self.assertRaises(SchoolError):
            run_activity(self.catalog, "visual_arts.starter", s)
        s = art_script()
        s["make"]["strokes"][0]["x1"] = True
        with self.assertRaises(SchoolError):
            run_activity(self.catalog, "visual_arts.starter", s)
        s = rhythm_script()
        s["make"]["pulses"] = [0, 0, 1]
        with self.assertRaises(SchoolError):
            run_activity(self.catalog, "music.starter", s)
        s = rhythm_script()
        s["make"]["pulses"] = list(range(33))
        with self.assertRaises(SchoolError):
            run_activity(self.catalog, "music.starter", s)
        with self.assertRaises(SchoolError):
            run_activity(self.catalog, "sculpture.starter", art_script())

    def test_no_files_network_audio_or_actuation(self):
        # Runner must remain pure; no attempt to create files, communicate, or
        # operate devices. load_catalog is the only opt-in filesystem read.
        with patch("builtins.open", side_effect=AssertionError("I/O forbidden")), \
             patch("pathlib.Path.open", side_effect=AssertionError("I/O forbidden")), \
             patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(len(run_activity(self.catalog, "visual_arts.starter", art_script())["events"]), 5)
            self.assertEqual(len(run_activity(self.catalog, "music.starter", rhythm_script())["events"]), 5)


if __name__ == "__main__":
    unittest.main()
