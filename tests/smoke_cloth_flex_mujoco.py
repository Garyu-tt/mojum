"""Pinned, opt-in MuJoCo physics smoke for PR #492.

This file deliberately does not match unittest's test*.py discovery pattern,
so Mojum's stdlib-only Aquarium/Motor/Mesh gates remain dependency-free.
Run only from the dedicated GitHub Actions job with mujoco installed.
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
from pathlib import Path
import contextlib
import unittest

import mujoco

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cloth_flex_spike.py"
_SPEC = importlib.util.spec_from_file_location("mojum_cloth_real_smoke", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
cloth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cloth)


class RealMuJoCoClothSmoke(unittest.TestCase):
    def test_compile_and_simulate_real_flex(self):
        mjcf = cloth.make_mjcf(selfcollide="auto", passive=True)
        model = mujoco.MjModel.from_xml_string(mjcf)
        data = mujoco.MjData(model)
        self.assertEqual(model.nflex, 1)
        self.assertEqual(model.nmocap, 1)
        self.assertEqual(model.nflexvert, cloth.GRID[0] * cloth.GRID[1])
        for i in range(25):
            data.mocap_pos[0, 0] = .045 * math.sin(2 * math.pi * i / 160)
            mujoco.mj_step(model, data)
            self.assertTrue(all(math.isfinite(float(v)) for v in data.qpos))
            self.assertTrue(all(math.isfinite(float(v)) for v in data.qvel))
            self.assertTrue(all(math.isfinite(float(v)) for row in data.flexvert_xpos for v in row))
        self.assertGreater(data.time, 0)

    def test_actual_benchmark_json_for_both_contact_modes(self):
        for passive, selfcollide in (("true", "auto"), ("false", "none")):
            with self.subTest(passive=passive, selfcollide=selfcollide):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = cloth.main([
                        "--steps", "20", "--warmup", "5",
                        "--passive", passive, "--selfcollide", selfcollide,
                    ])
                self.assertEqual(rc, 0)
                report = json.loads(out.getvalue())
                self.assertEqual(report["engine"], "mujoco")
                self.assertEqual(report["steps"], 20)
                self.assertEqual(report["warmup_steps"], 5)
                self.assertEqual(report["vertices"], cloth.GRID[0] * cloth.GRID[1])
                self.assertGreaterEqual(report["cpu_ms_per_step_p50"], 0)
                self.assertGreaterEqual(report["cpu_ms_per_step_p95"], 0)
                self.assertEqual(report["settings"]["passive"], passive == "true")
                self.assertEqual(report["settings"]["selfcollide"], selfcollide)


if __name__ == "__main__":
    unittest.main(verbosity=2)
