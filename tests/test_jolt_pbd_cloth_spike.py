"""Deterministic, offline, stdlib-only acceptance checks for Jolt/PBD cloth slice #702."""
import contextlib
import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import unittest

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "jolt_pbd_cloth_spike.py"
_SPEC = importlib.util.spec_from_file_location("mojum_jolt_pbd_fixture", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("missing standalone cloth benchmark")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class JoltPbdClothTests(unittest.TestCase):
    def test_public_exact_geometry_and_pinned_edge(self):
        spec = mod.fixture()
        self.assertEqual(spec["schema"], mod.SCHEMA)
        self.assertEqual(spec["target_engine"], "JoltPhysics")
        self.assertEqual(spec["target_version"], "5.7.0")
        self.assertEqual(spec["source"], "synthetic_public_fixture_only")
        self.assertIs(spec["hardware_contact"], False)
        self.assertEqual(spec["grid"], [12, 6])
        self.assertEqual(len(spec["vertices"]), 72)
        self.assertEqual(len(spec["distance_constraints"]), 126)
        self.assertEqual(len(spec["triangles"]), 110)
        self.assertEqual(
            [i for i, v in enumerate(spec["vertices"]) if v["inverse_mass"] == 0.0],
            list(range(6)))
        self.assertEqual(len(mod.fixture_hash()), 64)
        self.assertEqual(mod.fixture_hash(spec), mod.fixture_hash())

    def test_reference_replay_same_positions_not_timing(self):
        first = mod.run_reference(steps=7, warmup=3, iterations=4)
        second = mod.run_reference(steps=7, warmup=3, iterations=4)
        self.assertEqual(first["final_positions_sha256"],
                         second["final_positions_sha256"])
        self.assertEqual(first["fixture_sha256"], mod.fixture_hash())
        self.assertEqual(first["vertices"], 72)
        self.assertEqual(first["engine"], "pbd_python_reference_not_jolt")
        self.assertIs(first["contact_proof"], False)
        self.assertEqual(first["max_anchor_drift_m"], 0)
        for field in ("cpu_ms_per_step_p50", "cpu_ms_per_step_p95",
                      "max_floor_vertex_penetration_m_approx",
                      "max_sphere_vertex_penetration_m_approx"):
            self.assertTrue(math.isfinite(first[field]))
            self.assertGreaterEqual(first[field], 0)
        self.assertGreaterEqual(first["cpu_ms_per_step_p95"],
                                first["cpu_ms_per_step_p50"])

    def test_run_budget_rejects_overlarge_and_bool(self):
        for params in (
            {"steps": 0}, {"steps": True}, {"steps": 2001},
            {"warmup": -1}, {"warmup": False}, {"warmup": 501},
            {"iterations": 0}, {"iterations": True}, {"iterations": 33},
            {"steps": 2000, "warmup": 500, "iterations": 32},
        ):
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    mod.run_reference(**params)

    def test_nonfinite_contact_data_fail_closed(self):
        for bad in (float("nan"), float("inf"), -float("inf"), 1e100):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(RuntimeError, "nonfinite"):
                    mod._penetration([[bad, 0.0, 0.0]])
                with self.assertRaisesRegex(RuntimeError, "nonfinite"):
                    mod._project_contacts([bad, 0.0, 0.0])

    def test_exact_sphere_center_has_stable_separating_axis(self):
        point = list(mod.BALL_CENTER)
        mod._project_contacts(point)
        self.assertGreaterEqual(
            math.dist(point, mod.BALL_CENTER),
            mod.BALL_RADIUS + mod.RADIUS - 1e-12)
        self.assertGreaterEqual(point[2], mod.RADIUS)

    def _native_report(self):
        return {
            "schema": mod.NATIVE_REPORT_SCHEMA,
            "engine": "JoltPhysics",
            "engine_version": mod.JOLT_VERSION,
            "fixture_sha256": mod.fixture_hash(),
            "steps": 6, "warmup_steps": 2, "vertices": 72,
            "dt_seconds": mod.DT,
            "cpu_ms_per_step_p50": 1.25,
            "cpu_ms_per_step_p95": 2.5,
            "max_rss_kib_process": 20480,
            "max_anchor_drift_m": 0.001,
            "max_floor_vertex_penetration_m_approx": 0.002,
            "max_sphere_vertex_penetration_m_approx": 0.004,
            "hardware_contact": False,
        }

    def test_external_jolt_report_strict_identity_and_privacy(self):
        valid = self._native_report()
        self.assertEqual(mod.validate_native_report(
            valid, expected_steps=6, expected_warmup=2), valid)
        for key, bad in (
            ("schema", "private-state/1"),
            ("engine", "mujoco"),
            ("engine_version", "5.8.0"),
            ("fixture_sha256", "0" * 64),
            ("steps", True),
            ("steps", 7),
            ("warmup_steps", 3),
            ("vertices", 73),
            ("dt_seconds", 0.01),
            ("hardware_contact", True),
            ("cpu_ms_per_step_p95", -1),
            ("cpu_ms_per_step_p50", float("nan")),
            ("max_rss_kib_process", True),
        ):
            with self.subTest(key=key, bad=bad):
                value = copy.deepcopy(valid)
                value[key] = bad
                with self.assertRaises(ValueError):
                    mod.validate_native_report(
                        value, expected_steps=6, expected_warmup=2)
        value = copy.deepcopy(valid)
        value["private_trace"] = "do-not-leak"
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            mod.validate_native_report(
                value, expected_steps=6, expected_warmup=2)
        value = copy.deepcopy(valid)
        value["cpu_ms_per_step_p95"] = 0.5
        with self.assertRaises(ValueError):
            mod.validate_native_report(
                value, expected_steps=6, expected_warmup=2)

    def test_cli_fixture_and_reference(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(mod.main(["--fixture"]), 0)
        self.assertEqual(json.loads(buf.getvalue()), mod.fixture())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(mod.main(["--steps", "2", "--warmup", "0",
                                       "--iterations", "2"]), 0)
        report = json.loads(buf.getvalue())
        self.assertEqual(report["schema"], mod.REPORT_SCHEMA)


if __name__ == "__main__":
    unittest.main()
