"""Fail-closed school catalog parsing on interpreter-level JSON failures."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.school_activity import MAX_CATALOG_BYTES, SchoolError, load_catalog


class SchoolCatalogParserErrorsTests(unittest.TestCase):
    def test_integer_digit_limit_is_reported_as_school_error(self):
        if not hasattr(sys, "get_int_max_str_digits"):
            self.skipTest("interpreter has no integer conversion limit")
        limit = sys.get_int_max_str_digits()
        if not limit or limit + 100 > MAX_CATALOG_BYTES:
            self.skipTest("interpreter integer conversion cap is disabled or too large")
        # Python 3.11+ may throw ValueError, rather than JSONDecodeError,
        # while parsing an integer token with more than the configured digits.
        wire = ('{"schema":"mojum.school-catalog/1","extension":'
                + '9' * (limit + 1) + '}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(wire, encoding="utf-8")
            with self.assertRaisesRegex(SchoolError, "invalid catalog document"):
                load_catalog(path)

    def test_parser_recursion_error_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text('{"schema":"mojum.school-catalog/1"}', encoding="utf-8")
            with patch("mojum.school_activity.json.loads",
                       side_effect=RecursionError("synthetic parser depth")):
                with self.assertRaisesRegex(SchoolError, "invalid catalog document"):
                    load_catalog(path)

    def test_existing_duplicate_key_diagnostic_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text('{"schema":"mojum.school-catalog/1","schema":"duplicate"}',
                            encoding="utf-8")
            with self.assertRaisesRegex(SchoolError, "duplicate JSON key"):
                load_catalog(path)


if __name__ == "__main__":
    unittest.main()
