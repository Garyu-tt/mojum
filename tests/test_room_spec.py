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


class RuntimeParityTests(unittest.TestCase):
    """An offline-VALID draft must also construct the runtime point-room.

    ``room_spec`` compares exact Decimal tokens; the runtime ``Room`` compares
    Python floats. A draft whose exact bounds are legal can still round past a
    float room edge and be rejected at runtime, so the validator must fail
    closed instead of declaring geometry usable that cannot be loaded.
    """

    @staticmethod
    def _runtime_accepts(raw):
        from mojum.world_room import Room, initial_state

        document = json.loads(raw)
        room = Room(
            width=document["width"],
            height=document["height"],
            obstacles=[tuple(rect) for rect in document["obstacles"]],
        )
        initial_state(room, *document["start"])

    def test_positive_float_rounding_outside_room_is_rejected(self):
        # 0.1 + 0.2 == 0.30000000000000004 as float, beyond width 0.3.
        raw = (b'{"width":0.3,"height":1,"obstacles":[[0.1,0,0.2,0.5]],'
               b'"start":[0,0.5]}')
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIn("runtime float", report.errors[0])
        with self.assertRaises(ValueError):
            self._runtime_accepts(raw)

    def test_start_rounding_onto_closed_obstacle_edge_is_rejected(self):
        # Exact start 0.30000000000000004 > exact right edge 0.3, but both
        # become the same float and the runtime blocks the closed boundary.
        raw = (b'{"width":1,"height":1,"obstacles":[[0.1,0,0.2,0.5]],'
               b'"start":[0.30000000000000004,0.25]}')
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIn("runtime float", report.errors[0])
        with self.assertRaises(ValueError):
            self._runtime_accepts(raw)

    def test_rounding_safe_geometry_stays_valid(self):
        raw = (b'{"width":1,"height":1,"obstacles":[[0.1,0,0.2,0.5]],'
               b'"start":[0.31,0.25]}')
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self._runtime_accepts(raw)

    def test_accepted_documents_always_construct_the_runtime_room(self):
        documents = [
            CANONICAL,
            {"width": 4, "height": 4, "obstacles": [], "start": [0, 0]},
            {"width": 4, "height": 4, "obstacles": [[0, 0, 1, 1]], "start": [4, 4]},
            {"width": 1.5, "height": 2.5, "obstacles": [[0.5, 0.5, 1, 1]], "start": [0, 0]},
            {"width": 0.7, "height": 0.7, "obstacles": [[0.2, 0.2, 0.3, 0.3]], "start": [0.6, 0.6]},
        ]
        for document in documents:
            with self.subTest(document=document):
                raw = encode(document)
                report = validate_room_bytes(raw)
                self.assertTrue(report.valid, report.errors)
                self._runtime_accepts(raw)


class DesignerDraftSchemaTests(unittest.TestCase):
    """The offline designer always writes its draft schema key."""

    DRAFT_SCHEMA = "mojum.world.point-room-draft/1"

    def test_designer_schema_key_is_accepted(self):
        report = validate_room_bytes(encode({**CANONICAL, "schema": self.DRAFT_SCHEMA}))
        self.assertTrue(report.valid, report.errors)

    def test_wrong_schema_value_is_rejected(self):
        report = validate_room_bytes(encode({**CANONICAL, "schema": "other/1"}))
        self.assertFalse(report.valid)
        self.assertIn("unsupported room schema", report.errors[0])

    def test_non_string_schema_value_is_rejected(self):
        report = validate_room_bytes(encode({**CANONICAL, "schema": 1}))
        self.assertFalse(report.valid)
        self.assertIn("unsupported room schema", report.errors[0])

    def test_other_unexpected_keys_still_rejected_with_schema(self):
        raw = encode({**CANONICAL, "schema": self.DRAFT_SCHEMA, "color": "red"})
        self.assertEqual(errors_of(raw), ("unexpected room key(s): color",))

    def test_repository_designer_example_validates(self):
        example = Path(__file__).resolve().parent.parent / "examples" / "room_simple_mixed.json"
        report = validate_room_bytes(example.read_bytes())
        self.assertTrue(report.valid, report.errors)


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



class NumericWireRegressionTests(unittest.TestCase):
    def test_start_tokens_preserved_as_written(self):
        raw = (
            b'{"width":4,"height":4,"obstacles":[],"start":[1e0,2.00]}'
        )
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["start"], ["1e0", "2.00"])

    def test_huge_finite_decimals_fail_closed_without_arithmetic_overflow(self):
        raw = b'{"width":1e999999,"height":1e999999,"obstacles":[],"start":[1,2]}'
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertTrue(any("finite room coordinate" in error for error in report.errors))
        self.assertIsNone(report.summary)

    def test_nonzero_underflow_cannot_disappear_as_zero_coordinate(self):
        raw = b'{"width":4,"height":4,"obstacles":[],"start":[1e-999,2]}'
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertTrue(any("finite room coordinate" in error for error in report.errors))

    def test_oversized_number_token_is_rejected(self):
        from mojum.room_spec import MAX_NUMBER_TOKEN_CHARS
        token = "0." + "0" * MAX_NUMBER_TOKEN_CHARS + "1"
        raw = (
            '{"width":4,"height":4,"obstacles":[],"start":[' + token + ',2]}'
        ).encode()
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertTrue(any("number-token length limit" in error for error in report.errors))

    def test_finite_scientific_spelling_remains_usable(self):
        raw = b'{"width":4e0,"height":4,"obstacles":[],"start":[1e-2,2.0]}'
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.summary["start"], ["1e-2", "2.0"])


class NativeIntegerRuntimeParityTests(unittest.TestCase):
    """Offline validator agrees with native JSON int/float room geometry."""

    def test_large_integer_obstacle_does_not_round_onto_valid_start(self):
        from mojum.world_room import Room, initial_state

        boundary = 2**53
        raw = (
            '{"width":%d,"height":1,"obstacles":[[%d,0,1,1]],'
            '"start":[%d,0.5]}' % (boundary + 4, boundary + 1, boundary)
        ).encode("ascii")
        initial_state(Room(boundary + 4, 1, ((boundary + 1, 0, 1, 1),)),
                      float(boundary), 0.5)
        report = validate_room_bytes(raw)
        self.assertTrue(report.valid, report.errors)

    def test_versioned_draft_keeps_all_float_runtime_contract(self):
        from mojum.room_draft_runtime import load_runtime_room
        from mojum.room_spec import RoomSpecError

        boundary = 2**53
        raw = (
            '{"schema":"mojum.world.point-room-draft/1","width":%d,'
            '"height":1,"obstacles":[[%d,0,1,1]],"start":[%d,0.5]}'
            % (boundary + 4, boundary + 1, boundary)
        ).encode("ascii")
        # Unlike a direct native-integer Room, the versioned draft loader
        # converts obstacle coordinates to float before bootstrap.
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIn("runtime float", report.errors[0])
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "draft.json"
            file.write_bytes(raw)
            with self.assertRaisesRegex(RoomSpecError, "invalid room draft"):
                load_runtime_room(file)

    def test_explicit_float_token_still_collides_after_rounding(self):
        from mojum.world_room import Room, initial_state

        boundary = 2**53
        raw = (
            '{"width":%d,"height":1,"obstacles":[[%d.0,0,1,1]],'
            '"start":[%d,0.5]}' % (boundary + 4, boundary + 1, boundary)
        ).encode("ascii")
        with self.assertRaisesRegex(ValueError, "invalid initial position"):
            initial_state(Room(boundary + 4, 1,
                               ((float(boundary + 1), 0, 1, 1),)),
                          float(boundary), 0.5)
        report = validate_room_bytes(raw)
        self.assertFalse(report.valid)
        self.assertIn("runtime float", report.errors[0])


if __name__ == "__main__":
    unittest.main()
