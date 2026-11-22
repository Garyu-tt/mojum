"""Real MuJoCo 3.17 compatibility regression for current-main articulated cloth.

Forward-ports only the engine-specific acceptance intent from stacked #595.
Do not copy its old #572 source: current main uses the anchor_slide joint
and has a different benchmark schema. No rendering, secrets or hardware.
"""
from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path
import unittest


_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cloth_flex_spike.py"


class MuJoCo317CurrentMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("mujoco") is None:
            if os.environ.get("MOJUM_MUJOCO_317_REQUIRED") == "1":
                raise AssertionError("required MuJoCo 3.17 runtime unavailable")
            raise unittest.SkipTest("MuJoCo 3.17 is optional outside dedicated CI")
        import mujoco
        if mujoco.__version__ != "3.17.0":
            if os.environ.get("MOJUM_MUJOCO_317_REQUIRED") == "1":
                raise AssertionError("required exact MuJoCo version 3.17.0")
            raise unittest.SkipTest("this regression targets MuJoCo 3.17.0 only")
        cls.mujoco = mujoco
        spec = importlib.util.spec_from_file_location("cloth_317_main", _SCRIPT)
        if spec is None or spec.loader is None:
            raise AssertionError("current-main cloth source unavailable")
        cls.spike = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.spike)

    def test_main_articulated_servo_compiles_without_mocap(self):
        m = self.mujoco.MjModel.from_xml_string(self.spike.make_mjcf())
        self.assertEqual(m.nmocap, 0)
        self.assertEqual(m.nu, 1)
        self.assertEqual(m.nflex, 1)
        self.assertEqual(m.nflexvert, self.spike.GRID[0] * self.spike.GRID[1])
        self.assertEqual(m.opt.timestep, self.spike.DT)
        self.assertGreaterEqual(
            self.mujoco.mj_name2id(
                m, self.mujoco.mjtObj.mjOBJ_JOINT, "anchor_slide"
            ), 0,
        )

    def test_main_real_steps_and_metrics_in_four_modes(self):
        for passive in (True, False):
            for selfcollide in ("auto", "none"):
                with self.subTest(passive=passive, selfcollide=selfcollide):
                    xml = self.spike.make_mjcf(
                        passive=passive, selfcollide=selfcollide
                    )
                    m = self.mujoco.MjModel.from_xml_string(xml)
                    d = self.mujoco.MjData(m)
                    for tick in range(12):
                        d.ctrl[0] = 0.045 * math.sin(2 * math.pi * tick / 160)
                        self.mujoco.mj_step(m, d)
                        self.assertTrue(all(math.isfinite(float(v)) for v in d.qpos))
                        self.assertTrue(all(math.isfinite(float(v)) for v in d.qvel))
                        self.assertTrue(
                            all(math.isfinite(float(v)) for xyz in d.flexvert_xpos for v in xyz)
                        )
                    self.assertGreater(d.time, 0)
                    report = self.spike.benchmark(xml, steps=4, warmup=2)
                    self.assertEqual(report["engine"], "mujoco")
                    self.assertEqual(report["engine_version"], "3.17.0")
                    self.assertEqual(report["flexes"], 1)
                    self.assertEqual(
                        report["vertices"], self.spike.GRID[0] * self.spike.GRID[1]
                    )
                    self.assertEqual(report["steps"], 4)
                    for key in (
                        "cpu_ms_per_step_p50", "cpu_ms_per_step_p95",
                        "max_floor_vertex_penetration_m_approx",
                        "max_sphere_vertex_penetration_m_approx",
                    ):
                        self.assertIsNotNone(report[key])
                        self.assertTrue(math.isfinite(report[key]))
                        self.assertGreaterEqual(report[key], 0)
                    self.assertEqual(
                        report["penetration_metric"],
                        "vertex_center_plus_radius_only_not_mesh_surface_or_contact_proof",
                    )


if __name__ == "__main__":
    unittest.main()
