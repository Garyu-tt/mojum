"""MuJoCo 3.17 exact-runtime regression for servo-pinned cloth.

This catches the 3.x compiler failure in original PR #492/#588: elastic
flex must not be attached directly to a mocap body. The fix comes from
stacked QA #572, retaining a physically articulated servo anchor.
No rendering, external assets, credentials or private journals.
"""
import importlib.util
import math
import os
from pathlib import Path
import unittest


_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cloth_flex_spike.py"


class MuJoCo317ServoClothTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("mujoco") is None:
            if os.environ.get("MOJUM_MUJOCO_317_REQUIRED") == "1":
                raise AssertionError("required MuJoCo 3.17 runtime unavailable")
            raise unittest.SkipTest("MuJoCo is optional in stdlib CI")
        import mujoco
        cls.mujoco = mujoco
        spec = importlib.util.spec_from_file_location("cloth_317_regression", _SCRIPT)
        if spec is None or spec.loader is None:
            raise AssertionError("cloth source unavailable")
        cls.spike = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.spike)

    def test_elastic_flex_compiles_without_mocap_attachment(self):
        m = self.mujoco.MjModel.from_xml_string(self.spike.make_mjcf())
        self.assertEqual(m.nflex, 1)
        self.assertEqual(m.nflexvert, self.spike.GRID[0] * self.spike.GRID[1])
        self.assertEqual(m.nmocap, 0)
        self.assertEqual(m.nu, 1)
        self.assertEqual(m.opt.timestep, self.spike.DT)
        self.assertGreaterEqual(
            self.mujoco.mj_name2id(m, self.mujoco.mjtObj.mjOBJ_JOINT, "anchor_slide_x"), 0
        )

    def test_physics_steps_and_metrics_in_four_modes(self):
        for passive in (True, False):
            for selfcollide in ("auto", "none"):
                with self.subTest(passive=passive, selfcollide=selfcollide):
                    xml = self.spike.make_mjcf(passive=passive, selfcollide=selfcollide)
                    m = self.mujoco.MjModel.from_xml_string(xml)
                    d = self.mujoco.MjData(m)
                    for i in range(12):
                        d.ctrl[0] = .045 * math.sin(2 * math.pi * i / 160)
                        self.mujoco.mj_step(m, d)
                        self.assertTrue(all(math.isfinite(float(x)) for x in d.qpos))
                        self.assertTrue(all(math.isfinite(float(x)) for x in d.qvel))
                    self.assertGreater(d.time, 0)
                    report = self.spike.benchmark(xml, steps=4, warmup=2)
                    self.assertEqual(report["engine"], "mujoco")
                    self.assertEqual(report["flexes"], 1)
                    self.assertEqual(report["vertices"], self.spike.GRID[0] * self.spike.GRID[1])
                    self.assertEqual(report["steps"], 4)
                    for key in ("cpu_ms_per_step_p50", "cpu_ms_per_step_p95",
                                "max_anchor_tracking_error_m"):
                        self.assertTrue(math.isfinite(report[key]))
                        self.assertGreaterEqual(report[key], 0)
                    self.assertEqual(
                        report["penetration_metric"],
                        "vertex_center_plus_radius_only_not_mesh_surface_or_contact_proof",
                    )


if __name__ == "__main__":
    unittest.main()
