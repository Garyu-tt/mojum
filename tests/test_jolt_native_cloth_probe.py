"""Strict offline validator and optional executable smoke for the native Jolt probe."""

from __future__ import annotations

import json
import math
import os
import subprocess
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any


MAX_REPORT_BYTES = 16_384

EXPECTED_KEYS = {
    "schema",
    "engine_version",
    "steps",
    "fixed_dt_seconds",
    "vertices",
    "faces",
    "pinned_vertices",
    "mean_bottom_drop_m",
    "max_pinned_drift_m",
    "finite_state",
    "pinned_row_stable",
    "deformed",
    "self_collision",
    "external_contact",
}


def _utf8_size_within(value: str, maximum: int) -> bool:
    """Count UTF-8 bytes without allocating an encoded copy of untrusted text."""
    size = 0
    for char in value:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            return False
        if code <= 0x7F:
            size += 1
        elif code <= 0x7FF:
            size += 2
        elif code <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > maximum:
            return False
    return True


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate report JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON constant")


def validate_report(raw: str) -> dict[str, Any]:
    if type(raw) is not str or not _utf8_size_within(raw, MAX_REPORT_BYTES):
        raise ValueError("report input type or byte budget invalid")
    try:
        report = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("report must be one strict JSON object") from exc
    except ValueError:
        # Preserve the specific duplicate-key / non-finite-value rejection.
        raise
    if type(report) is not dict or set(report) != EXPECTED_KEYS:
        raise ValueError("report keys do not match mojum.jolt-native-cloth/1")
    if report["schema"] != "mojum.jolt-native-cloth/1":
        raise ValueError("unsupported report schema")
    if report["engine_version"] != "5.7.0":
        raise ValueError("unexpected Jolt version")
    for key in ("steps", "vertices", "faces", "pinned_vertices"):
        if type(report[key]) is not int or report[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("fixed_dt_seconds", "mean_bottom_drop_m", "max_pinned_drift_m"):
        value = report[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite numeric data")
    if report["steps"] > 600 or report["vertices"] > 4096 or report["faces"] > 8192:
        raise ValueError("report exceeds the probe budget")
    if report["fixed_dt_seconds"] <= 0 or report["fixed_dt_seconds"] > 0.1:
        raise ValueError("fixed time step is outside the accepted range")
    if report["pinned_vertices"] > report["vertices"]:
        raise ValueError("pinned vertex count exceeds total vertices")
    for key in ("finite_state", "pinned_row_stable", "deformed", "self_collision", "external_contact"):
        if type(report[key]) is not bool:
            raise ValueError(f"{key} must be a JSON boolean")
    if not report["finite_state"] or not report["pinned_row_stable"] or not report["deformed"]:
        raise ValueError("native simulation did not satisfy its acceptance checks")
    if report["self_collision"] or report["external_contact"]:
        raise ValueError("this fixture must not claim untested contact capabilities")
    if report["mean_bottom_drop_m"] <= 0 or report["max_pinned_drift_m"] >= 1.0e-3:
        raise ValueError("cloth deformation or pinned-row bounds were not met")
    return report


class NativeJoltClothReportTests(unittest.TestCase):
    def test_accepts_well_formed_bounded_report(self) -> None:
        raw = json.dumps({
            "schema": "mojum.jolt-native-cloth/1",
            "engine_version": "5.7.0",
            "steps": 240,
            "fixed_dt_seconds": 1 / 60,
            "vertices": 96,
            "faces": 154,
            "pinned_vertices": 12,
            "mean_bottom_drop_m": 0.21,
            "max_pinned_drift_m": 0.0,
            "finite_state": True,
            "pinned_row_stable": True,
            "deformed": True,
            "self_collision": False,
            "external_contact": False,
        })
        self.assertEqual(validate_report(raw)["vertices"], 96)

    def test_rejects_duplicate_json_keys(self) -> None:
        duplicate = '{"schema":"first","schema":"second"}'
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_report(duplicate)

    def test_rejects_oversized_text_before_json_parser(self) -> None:
        with patch(
            "tests.test_jolt_native_cloth_probe.json.loads",
            side_effect=AssertionError("JSON parser must not see oversized input"),
        ):
            with self.assertRaisesRegex(ValueError, "byte budget"):
                validate_report(" " * (MAX_REPORT_BYTES + 1))

    def test_rejects_non_string_report_input(self) -> None:
        for raw in (b"{}", None, 123):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "input type"):
                validate_report(raw)

    def test_rejects_unknown_fields_and_missing_fields(self) -> None:
        valid = {
            "schema": "mojum.jolt-native-cloth/1",
            "engine_version": "5.7.0",
            "steps": 240,
            "fixed_dt_seconds": 1 / 60,
            "vertices": 96,
            "faces": 154,
            "pinned_vertices": 12,
            "mean_bottom_drop_m": 0.21,
            "max_pinned_drift_m": 0.0,
            "finite_state": True,
            "pinned_row_stable": True,
            "deformed": True,
            "self_collision": False,
            "external_contact": False,
        }
        for altered in ({**valid, "private_event": "must-not-appear"}, {k: v for k, v in valid.items() if k != "schema"}):
            with self.subTest(keys=sorted(altered)):
                with self.assertRaises(ValueError):
                    validate_report(json.dumps(altered))

    def test_rejects_bool_as_integer_and_nonfinite_values(self) -> None:
        valid = {
            "schema": "mojum.jolt-native-cloth/1",
            "engine_version": "5.7.0",
            "steps": True,
            "fixed_dt_seconds": 1 / 60,
            "vertices": 96,
            "faces": 154,
            "pinned_vertices": 12,
            "mean_bottom_drop_m": float("nan"),
            "max_pinned_drift_m": 0.0,
            "finite_state": True,
            "pinned_row_stable": True,
            "deformed": True,
            "self_collision": False,
            "external_contact": False,
        }
        with self.assertRaises(ValueError):
            validate_report(json.dumps(valid))

    def test_rejects_incomplete_or_overstated_physics(self) -> None:
        valid = {
            "schema": "mojum.jolt-native-cloth/1",
            "engine_version": "5.7.0",
            "steps": 240,
            "fixed_dt_seconds": 1 / 60,
            "vertices": 96,
            "faces": 154,
            "pinned_vertices": 12,
            "mean_bottom_drop_m": 0.21,
            "max_pinned_drift_m": 0.0,
            "finite_state": True,
            "pinned_row_stable": True,
            "deformed": True,
            "self_collision": False,
            "external_contact": True,
        }
        with self.assertRaises(ValueError):
            validate_report(json.dumps(valid))

    @unittest.skipUnless(os.environ.get("JOLT_NATIVE_CLOTH_PROBE"), "native Jolt binary is built by the dedicated CI workflow")
    def test_real_native_jolt_executable(self) -> None:
        binary = Path(os.environ["JOLT_NATIVE_CLOTH_PROBE"])
        self.assertTrue(binary.is_file(), f"native probe not found: {binary}")
        completed = subprocess.run(
            [str(binary)], check=True, capture_output=True, text=True, timeout=30
        )
        report = validate_report(completed.stdout)
        self.assertEqual(report["engine_version"], "5.7.0")


if __name__ == "__main__":
    unittest.main()
