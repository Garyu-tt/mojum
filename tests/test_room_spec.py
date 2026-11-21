"""Strict offline typed-room validation: exact tokens, bounds, start placement."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from mojum.room_spec import (
    MAX_INPUT_BYTES,
    MAX_OBSTACLES,
    REPORT_SCHEMA,
    RoomSpecError,
    main,
    validate_room_bytes,
    validate_room_document,
)

CANONICAL = {
    "width": 4,
    "height": 4,
    "obstacles": [[2, 1, 0.1, 2]],
    "start": [1.0, 2.0],
}


def encode(document):
    return json.dumps(document).encode("utf-8")


def errors_of(raw):
    return validate_room_bytes(raw).errors


class ValidRoomTests(unittest.TestCase):
    def test_canonical_default_room_is_valid(self):
        report = validate_room_bytes(encode(CANONICAL))
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.warnings, ())

    def test_summary_reports_geometry(self):
        report = validate_room_bytes(encode(CANONICAL))
        summary = report.summary
        self.assertEqual(summary["width"], "4")
        self.assertEqual(summary["height"], "4")
        self.assertEqual(summary["obstacle_count"], 1)
        self.assertEqual(summary["start"], ["1.0", "2.0"])
        self.assertEqual(summary["obstacles"], [["2", "1", "0.1", "2"]])
        self.assertEqual(summary["obstacles_total_area"], "0.2")
        self.assertEqual(summary["estimated_free_area"], "15.8")
        self.assertTrue(summary["number_tokens_preserved"])

    def test_exact_decimal_tokens_are_not_float_coerced(self):
        raw = (
            b'{"width":4,"height":4,"obstacles":[[2,1,0.10000000000000001,2]],'
            b'"start":[1.0,2.0]}'
        )
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["obstacles"][0][2], "0.10000000000000001")

    def test_exponent_and_integer_spellings_are_preserved(self):
        raw = (
            b'{"width":4e0,"height":4,"obstacles":[[2,1,1e-1,2]],'
            b'"start":[1.0,2.0]}'
        )
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["width"], "4e0")
        self.assertEqual(report.summary["obstacles"][0][2], "1e-1")

    def test_empty_obstacle_list_and_corner_start_is_valid(self):
        report = validate_room_bytes(encode(
            {"width": 4, "height": 4, "obstacles": [], "start": [0, 0]}
        ))
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["estimated_free_area"], "16")

    def test_touching_obstacles_warn_but_remain_valid(self):
        report = validate_room_bytes(encode({
            "width": 4, "height": 4,
            "obstacles": [[0, 0, 1, 1], [1, 0, 1, 1]],
            "start": [3, 3],
        }))
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(
            report.warnings, ("obstacles[0] overlaps obstacles[1]",)
        )

    def test_duplicate_obstacle_warns_but_remains_valid(self):
        report = validate_room_bytes(encode({
            "width": 4, "height": 4,
            "obstacles": [[0, 0, 1, 1], [0, 0, 1, 1]],
            "start": [3, 3],
        }))
        self.assertTrue(report.valid, report.errors)
        self.assertIn(
            "obstacles[1] duplicates obstacles[0]", report.warnings
        )

    def test_overlap_analysis_is_skipped_above_bound(self):
        obstacles = [[0, 0, 0.001, 0.001] for _ in range(257)]
        report = validate_room_bytes(encode({
            "width": 4, "height": 4, "obstacles": obstacles, "start": [3, 3],
        }))
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("overlap analysis skipped", report.warnings[0])

    def test_maximum_obstacle_count_is_accepted(self):
        obstacles = [[0, 0, 0.001, 0.001] for _ in range(MAX_OBSTACLES)]
        report = validate_room_bytes(encode({
            "width": 4, "height": 4, "obstacles": obstacles, "start": [3, 3],
        }))
        self.assertTrue(report.valid, report.errors)


class ShapeErrorTests(unittest.TestCase):
    def test_root_must_be_object(self):
        for document in ([], 1, "room", True, None):
            with self.subTest(document=document):
                report = validate_room_bytes(encode(document))
                self.assertFalse(report.valid)
                self.assertIn("root must be a JSON object", report.errors[0])

    def test_missing_keys_are_reported_together(self):
        report = validate_room_bytes(encode({"width": 4}))
        self.assertFalse(report.valid)
        self.assertEqual(len(report.errors), 1)
        self.assertIn("missing room key(s): height, obstacles, start",
                      report.errors[0])

    def test_unexpected_keys_are_rejected(self):
        raw = encode({**CANONICAL, "color": "red"})
        self.assertEqual(errors_of(raw), ("unexpected room key(s): color",))

    def test_duplicate_root_key_is_rejected(self):
        raw = b'{"width":4,"width":4,"height":4,"obstacles":[],"start":[1,1]}'
        self.assertEqual(errors_of(raw), ("duplicate JSON key: width",))

    def test_duplicate_nested_key_is_rejected(self):
        raw = b'{"width":4,"height":4,"obstacles":[],' \
              b'"start":[1,1],"start":[2,2]}'
        self.assertIn("duplicate JSON key: start", errors_of(raw)[0])

    def test_non_finite_json_constants_are_rejected(self):
        raw = b'{"width":NaN,"height":4,"obstacles":[],"start":[1,1]}'
        self.assertIn("non-finite JSON number", errors_of(raw)[0])

    def test_invalid_json_is_rejected(self):
        self.assertIn("not valid JSON", errors_of(b"{")[0])

    def test_invalid_utf8_is_rejected(self):
        self.assertIn("not valid UTF-8", errors_of(b'{"width":\xff}')[0])

    def test_byte_limit_is_enforced(self):
        raw = b" " * (MAX_INPUT_BYTES + 1)
        self.assertEqual(
            errors_of(raw), ("room document exceeds byte limit",)
        )

    def test_non_bytes_input_is_rejected_at_the_boundary(self):
        with self.assertRaises(RoomSpecError):
            from mojum.room_spec import _parse
            _parse("{}")

    def test_numbers_are_required_not_strings_or_bools(self):
        cases = [
            ("width", "4", "width must be a JSON number, not str"),
            ("width", True, "width must be a JSON number, not bool"),
            ("height", None, "height must be a JSON number, not NoneType"),
        ]
        for key, value, expected in cases:
            with self.subTest(key=key, value=value):
                raw = encode({**CANONICAL, key: value})
                self.assertIn(expected, errors_of(raw))

    def test_zero_and_negative_dimensions_are_rejected(self):
        for value in (0, -1, -0.5):
            with self.subTest(value=value):
                raw = encode({**CANONICAL, "width": value})
                self.assertIn("width must be greater than zero", errors_of(raw))

    def test_obstacles_must_be_a_list(self):
        raw = encode({**CANONICAL, "obstacles": [[0, 0, 1, 1]]}).replace(
            b"[[0, 0, 1, 1]]", b"{}"
        )
        self.assertEqual(errors_of(raw), ("obstacles must be a JSON list",))

    def test_obstacle_must_have_exactly_four_numbers(self):
        for rect in ([0, 0, 1], [0, 0, 1, 1, 1], {}, None):
            with self.subTest(rect=rect):
                raw = encode({**CANONICAL, "obstacles": [rect]})
                self.assertIn("obstacles[0] must be a list of exactly four",
                              errors_of(raw)[0])

    def test_obstacle_element_must_be_a_number(self):
        raw = encode({**CANONICAL, "obstacles": [[0, 0, 1, "1"]]})
        self.assertEqual(
            errors_of(raw), ("obstacles[0][3] must be a JSON number, not str",)
        )

    def test_obstacle_needs_positive_size(self):
        for rect in ([0, 0, 0, 1], [0, 0, 1, 0], [0, 0, -1, 1]):
            with self.subTest(rect=rect):
                raw = encode({**CANONICAL, "obstacles": [rect]})
                self.assertIn("positive width and height", errors_of(raw)[0])

    def test_obstacle_must_lie_inside_room(self):
        for rect in ([3, 0, 2, 1], [-1, 0, 1, 1], [0, 3, 1, 2], [0, -1, 1, 1]):
            with self.subTest(rect=rect):
                raw = encode({**CANONICAL, "obstacles": [rect]})
                self.assertIn("lies outside the room", errors_of(raw)[0])

    def test_obstacle_count_budget_is_enforced(self):
        obstacles = [[0, 0, 0.001, 0.001] for _ in range(MAX_OBSTACLES + 1)]
        raw = encode({**CANONICAL, "obstacles": obstacles})
        self.assertIn("rectangle budget", errors_of(raw)[0])

    def test_start_must_be_two_numbers(self):
        for start in ([1], [1, 2, 3], "12", {"x": 1, "y": 2}):
            with self.subTest(start=start):
                raw = encode({**CANONICAL, "start": start})
                self.assertIn("must be a list of exactly two numbers",
                              errors_of(raw)[0])

    def test_start_must_lie_inside_room(self):
        for start in ([-0.1, 2], [4.1, 2], [1, -1], [1, 4.5]):
            with self.subTest(start=start):
                raw = encode({**CANONICAL, "start": start})
                self.assertIn("start.", errors_of(raw)[0])

    def test_start_inside_obstacle_is_rejected(self):
        report = validate_room_bytes(encode({**CANONICAL, "start": [2.05, 2]}))
        self.assertFalse(report.valid)
        self.assertEqual(
            report.errors, ("start lies inside obstacles[0] (closed boundaries block)",)
        )

    def test_start_on_obstacle_boundary_is_rejected(self):
        for start in ([2, 2], [2.1, 2], [2, 3], [2.1, 3]):
            with self.subTest(start=start):
                raw = encode({**CANONICAL, "start": start})
                self.assertIn("start lies inside obstacles[0]", errors_of(raw)[0])

    def test_errors_accumulate_in_document_order(self):
        raw = encode({"width": 0, "height": -1, "obstacles": "x", "start": [1]})
        self.assertEqual(errors_of(raw), (
            "width must be greater than zero",
            "height must be greater than zero",
            "obstacles must be a JSON list",
            "start must be a list of exactly two numbers",
        ))


class ReportShapeTests(unittest.TestCase):
    def test_json_report_schema_and_validity(self):
        report = validate_room_bytes(encode(CANONICAL))
        payload = report.as_json()
        self.assertEqual(payload["schema"], REPORT_SCHEMA)
        self.assertIs(payload["valid"], True)
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["summary"]["start"], ["1.0", "2.0"])

    def test_invalid_document_has_no_summary(self):
        report = validate_room_bytes(encode({"width": 0}))
        self.assertIsNone(report.summary)
        self.assertIs(report.as_json()["valid"], False)

    def test_validation_does_not_mutate_the_input_document(self):
        document = json.loads(json.dumps(CANONICAL))
        before = json.loads(json.dumps(document))
        validate_room_document(document)
        self.assertEqual(document, before)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _write(self, raw, name="room.json"):
        path = self.root / name
        path.write_bytes(raw)
        return path

    def test_valid_text_report_exits_zero(self):
        path = self._write(encode(CANONICAL))
        code, out, err = self._run([str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(
            out, "VALID: width=4 height=4 obstacles=1 start=(1.0, 2.0)\n"
        )
        self.assertEqual(err, "")

    def test_invalid_text_report_exits_one(self):
        path = self._write(encode({**CANONICAL, "start": [2.05, 2]}))
        code, out, _ = self._run([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("INVALID: 1 error(s)", out)
        self.assertIn("error: start lies inside obstacles[0]", out)

    def test_json_format_is_machine_readable(self):
        path = self._write(encode(CANONICAL))
        code, out, _ = self._run([str(path), "--format", "json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema"], REPORT_SCHEMA)
        self.assertIs(payload["valid"], True)

    def test_warnings_are_reported_in_text_mode(self):
        path = self._write(encode({
            **CANONICAL,
            "obstacles": [[0, 0, 1, 1], [1, 0, 1, 1]],
        }))
        code, out, _ = self._run([str(path)])
        self.assertEqual(code, 0)
        self.assertIn("warning: obstacles[0] overlaps obstacles[1]", out)

    def test_missing_file_reports_an_error_without_a_traceback(self):
        code, out, err = self._run([str(self.root / "missing.json")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read room document", out)
        self.assertEqual(err, "")

    def test_oversized_file_reports_the_byte_limit(self):
        path = self._write(b"{" + b" " * MAX_INPUT_BYTES, name="big.json")
        code, out, _ = self._run([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("exceeds byte limit", out)

    def test_usage_error_exits_two(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main([])
        self.assertEqual(caught.exception.code, 2)


class RuntimeNumberRangeTests(unittest.TestCase):
    def test_huge_exponent_is_a_validation_error_not_a_decimal_overflow(self):
        raw = (b'{"width":1e1000000,"height":4,"obstacles":[],'
               b'"start":[0,0]}')
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIsNone(report.summary)
        self.assertIn("width is outside the finite runtime number range",
                      report.errors)

    def test_subnormal_underflow_does_not_certify_nonzero_geometry(self):
        raw = (b'{"width":1e-999999,"height":4,"obstacles":[],'
               b'"start":[0,0]}')
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIn("width is outside the finite runtime number range",
                      report.errors)

    def test_large_but_finite_runtime_numbers_preserve_exact_spelling(self):
        raw = (b'{"width":1e308,"height":1e308,"obstacles":[],'
               b'"start":[0,0]}')
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["width"], "1e308")
        self.assertEqual(report.summary["estimated_free_area"], "1E+616")


if __name__ == "__main__":
    unittest.main()
