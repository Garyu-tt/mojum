"""Optional MuJoCo engine integration smoke for the standalone cloth spike.

This test is skipped by general stdlib CI without MuJoCo; a dedicated CI
workflow sets MOJUM_MUJOCO_RUNTIME_REQUIRED=1 and installs the real engine.
No remote assets, GUI, user devices or PRIVATE journals are accessed.
"""
import importlib.util
import math
import os
from pathlib import Path
import unittest


_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cloth_flex_spike.py"


def load_spike():
    spec = importlib.util.spec_from_file_location("mojum_cloth_spike_runtime_test", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cloth-spike source unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActualMuJoCoClothTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("mujoco") is None:
            if os.environ.get("MOJUM_MUJOCO_RUNTIME_REQUIRED") == "1":
                raise AssertionError("dedicated runtime job must install MuJoCo")
            raise unittest.SkipTest("MuJoCo optional in stdlib-only suite")
        import mujoco
        cls.mujoco = mujoco
        cls.spike = load_spike()

    def test_mjcf_compiles_with_real_flex_and_articulated_anchor(self):
        model = self.mujoco.MjModel.from_xml_string(self.spike.make_mjcf())
        self.assertEqual(model.nflex, 1)
        self.assertEqual(model.nmocap, 0)
        self.assertEqual(model.nu, 1)
        # flexcomp creates additional free joints for unpinned vertices.
        # Only require the intended articulated anchor to exist.
        slide_id = self.mujoco.mj_name2id(model, self.mujoco.mjtObj.mjOBJ_JOINT, "anchor_slide")
        self.assertGreaterEqual(slide_id, 0)
        self.assertEqual(model.jnt_type[slide_id], self.mujoco.mjtJoint.mjJNT_SLIDE)
        self.assertEqual(model.nflexvert, self.spike.GRID[0] * self.spike.GRID[1])
        self.assertAlmostEqual(model.opt.timestep, self.spike.DT)

    def test_short_headless_step_and_report_are_finite(self):
        for passive in (True, False):
            for selfcollide in ("auto", "none"):
                with self.subTest(passive=passive, selfcollide=selfcollide):
                    xml = self.spike.make_mjcf(
                        passive=passive, selfcollide=selfcollide
                    )
                    report = self.spike.benchmark(xml, steps=4, warmup=2)
                    self.assertEqual(report["engine"], "mujoco")
                    self.assertEqual(report["flexes"], 1)
                    self.assertEqual(report["vertices"],
                                     self.spike.GRID[0] * self.spike.GRID[1])
                    self.assertEqual(report["steps"], 4)
                    self.assertEqual(report["warmup_steps"], 2)
                    for metric in ("cpu_ms_per_step_p50", "cpu_ms_per_step_p95"):
                        self.assertTrue(math.isfinite(report[metric]))
                        self.assertGreaterEqual(report[metric], 0)
                    self.assertEqual(
                        report["penetration_metric"],
                        "vertex_center_plus_radius_only_not_mesh_surface_or_contact_proof",
                    )


if __name__ == "__main__":
    unittest.main()
