"""Regression coverage for school catalog/event UTF-8 and byte budgets."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from mojum.school_activity import (
    MAX_CATALOG_BYTES, SchoolError, load_catalog, redacted_summary,
    run_activity, validate_catalog,
)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


def script():
    return {
        "observe": "listen",
        "experiment": "try a beat",
        "make": {"pulses": [0, 4]},
        "revise": {"pulses": [0, 5]},
        "reflect": "compare",
    }


class SchoolCatalogWireGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG_PATH)

    def test_in_memory_unknown_metadata_cannot_bypass_json_contract(self):
        invalid = (object(), float("nan"), float("inf"), "\ud800",
                   {"nested": -float("inf")}, (1, 2), {1: "integer key"})
        for value in invalid:
            with self.subTest(value=repr(value)):
                catalog = copy.deepcopy(self.catalog)
                catalog["extension"] = value
                with self.assertRaises(SchoolError):
                    validate_catalog(catalog)
                with self.assertRaises(SchoolError):
                    run_activity(catalog, "music.starter", script())

        catalog = copy.deepcopy(self.catalog)
        catalog["extension"] = catalog
        with self.assertRaises(SchoolError):
            validate_catalog(catalog)

    def test_lossy_python_collections_cannot_pass_as_json_metadata(self):
        # json.JSONEncoder accepts tuples and numeric mapping keys, but
        # decoding those forms changes their meaning.  A byte limit alone
        # must not authorize a catalog that would change after persistence.
        for value in ((1, 2), {1: "numeric-key"},
                      {"nested": (1, 2)}, {True: "boolean-key"}):
            with self.subTest(value=repr(value)):
                catalog = copy.deepcopy(self.catalog)
                catalog["extension"] = value
                with self.assertRaisesRegex(SchoolError, "round-trip"):
                    validate_catalog(catalog)
                with self.assertRaisesRegex(SchoolError, "round-trip"):
                    run_activity(catalog, "music.starter", script())

    def test_exact_canonical_utf8_budget_and_one_byte_over(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["extension"] = ""

        def size():
            return len(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("utf-8"))

        slack = MAX_CATALOG_BYTES - size()
        self.assertGreater(slack, 0)
        catalog["extension"] = "x" * slack
        self.assertEqual(size(), MAX_CATALOG_BYTES)
        self.assertIs(validate_catalog(catalog), catalog)
        catalog["extension"] += "x"
        self.assertEqual(size(), MAX_CATALOG_BYTES + 1)
        with self.assertRaisesRegex(SchoolError, "byte budget"):
            validate_catalog(catalog)

    def test_huge_single_string_fails_before_encoder_allocation(self):
        # iterencode is incremental across tokens, not within a giant string.
        # A 100x-oversized value must be refused before constructing that token.
        import tracemalloc
        from unittest import mock

        catalog = copy.deepcopy(self.catalog)
        catalog["extension"] = "x" * (100 * MAX_CATALOG_BYTES)
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()
        try:
            baseline, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            with mock.patch.object(json.JSONEncoder, "iterencode",
                                   side_effect=AssertionError("oversized fragment encoded")):
                with self.assertRaisesRegex(SchoolError, "byte budget"):
                    validate_catalog(catalog)
            _, peak = tracemalloc.get_traced_memory()
            self.assertLess(peak - baseline, 2 * 1024 * 1024)
        finally:
            if not was_tracing:
                tracemalloc.stop()

    def test_huge_single_string_does_not_allocate_an_oversized_fragment(self):
        # iterencode() emits a string as one token; the old implementation
        # encoded a 100x-over-budget fragment before checking the limit.
        import gc
        import tracemalloc

        catalog = copy.deepcopy(self.catalog)
        catalog["extension"] = "x" * (100 * MAX_CATALOG_BYTES)
        gc.collect()
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(SchoolError, "byte budget"):
                validate_catalog(catalog)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # The original giant input was allocated before tracemalloc started.
        # Validation must not copy it into another giant encoded string.
        self.assertLess(peak, 16 * MAX_CATALOG_BYTES)

    def test_nested_string_values_are_preflight_bounded(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["metadata"] = {"shared": ["🎵" * MAX_CATALOG_BYTES]}
        with self.assertRaisesRegex(SchoolError, "byte budget"):
            validate_catalog(catalog)

    def test_budget_is_bytes_not_unicode_codepoints(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["extension"] = ""
        fixed_size = len(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8"))
        catalog["extension"] = "Ж" * ((MAX_CATALOG_BYTES - fixed_size) // 2 + 1)
        self.assertLess(len(catalog["extension"]), MAX_CATALOG_BYTES)
        with self.assertRaisesRegex(SchoolError, "byte budget"):
            validate_catalog(catalog)

    def test_file_catalog_rejects_finite_syntax_overflow_and_surrogate(self):
        encoded = json.dumps(self.catalog, ensure_ascii=True)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "school.json"
            for extension in ("1e999", r'"\ud800"'):
                file.write_text(encoded[:-1] + ',"extension":' + extension + "}",
                                encoding="utf-8")
                with self.subTest(extension=extension), self.assertRaises(SchoolError):
                    load_catalog(file)

    def test_script_notes_fail_closed_on_invalid_unicode_and_controls(self):
        for note in ("\ud800", "\udfff", "\x7f", "\x85", " " * 161):
            for field in ("observe", "experiment", "reflect"):
                with self.subTest(field=field, note=repr(note)):
                    bad = script()
                    bad[field] = note
                    with self.assertRaises(SchoolError):
                        run_activity(self.catalog, "music.starter", bad)

    def test_private_summary_cannot_accept_invalid_observation(self):
        run = run_activity(self.catalog, "music.starter", script())
        run["events"][0]["observation"] = "\ud800"
        with self.assertRaises(SchoolError):
            redacted_summary(run)

    def test_normal_multilingual_school_notes_preserved(self):
        notes = script()
        notes["observe"] = "Ритм и мелодия 🎵"
        result = run_activity(self.catalog, "music.starter", notes)
        self.assertEqual(result["events"][0]["observation"], notes["observe"])
        self.assertEqual(redacted_summary(result)["phase_count"], 5)


if __name__ == "__main__":
    unittest.main()
