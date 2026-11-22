"""Regression: school catalog and scripted notes share a strict UTF-8 wire contract."""
import copy
import unittest
from pathlib import Path

from mojum.school_activity import (
    MAX_CATALOG_BYTES, SchoolError, load_catalog, redacted_summary,
    run_activity, validate_catalog,
)


CATALOG = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


def _script():
    return {
        "observe": "listen to the beat",
        "experiment": "try a pause",
        "make": {"pulses": [0, 4, 8]},
        "revise": {"pulses": [0, 4, 9]},
        "reflect": "the last pulse moved",
    }


class SchoolUtf8WirePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG)

    def test_notes_reject_unpaired_surrogates_before_returning_private_run(self):
        for phase in ("observe", "experiment", "reflect"):
            script = _script()
            script[phase] = "bad " + "\ud800"
            with self.subTest(phase=phase), self.assertRaises(SchoolError):
                run_activity(self.catalog, "music.starter", script)

    def test_extra_catalog_fields_must_round_trip_losslessly(self):
        invalid = (
            ("nonfinite", float("nan")),
            ("non-json object", object()),
            ("tuple silently becoming list", (1, 2)),
            ("nonstring mapping key", {1: "a"}),
            ("unpaired surrogate", "\udfff"),
        )
        for name, value in invalid:
            catalog = copy.deepcopy(self.catalog)
            catalog["experimental_metadata"] = value
            with self.subTest(name=name):
                with self.assertRaises(SchoolError):
                    validate_catalog(catalog)
                with self.assertRaises(SchoolError):
                    run_activity(catalog, "music.starter", _script())

    def test_cycles_and_oversized_in_memory_catalogs_fail_closed(self):
        recursive = copy.deepcopy(self.catalog)
        recursive["metadata"] = recursive
        oversized = copy.deepcopy(self.catalog)
        oversized["metadata"] = "x" * MAX_CATALOG_BYTES
        for catalog in (recursive, oversized):
            with self.assertRaises(SchoolError):
                validate_catalog(catalog)
            with self.assertRaises(SchoolError):
                run_activity(catalog, "music.starter", _script())

    def test_non_utf8_catalog_title_rejected_before_activity(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["sections"][0]["title"] = "school " + "\ud800"
        with self.assertRaises(SchoolError):
            validate_catalog(catalog)

    def test_valid_unicode_and_json_metadata_remain_supported(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["metadata"] = {"description": "музыкальная школа", "steps": [0, True, None]}
        self.assertIs(validate_catalog(catalog), catalog)
        script = _script()
        script["reflect"] = "ритм изменился"
        run = run_activity(catalog, "music.starter", script)
        self.assertEqual(redacted_summary(run)["artifact_count"], 2)


if __name__ == "__main__":
    unittest.main()
