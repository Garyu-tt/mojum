"""Issue #689 strict stdlib tests, optional exact-version real engine tests."""
import copy
import contextlib
import io
from types import SimpleNamespace
import importlib.util
import math
import unittest
import xml.etree.ElementTree as ET

from mojum.mujoco_body import (
    DT, HeadlessBody, PINNED_MUJOCO, RigError, compile_mjcf,
    fixture_rig, main, rig_revision, validate_rig,
)


class RigContractTests(unittest.TestCase):
    def setUp(self):
        self.rig = fixture_rig()

    def test_fixture_is_stable_and_is_not_an_actuator(self):
        original = rig_revision(self.rig)
        self.assertEqual(len(original), 64)
        self.assertEqual(self.rig, validate_rig(self.rig))
        xml = ET.fromstring(compile_mjcf(self.rig))
        self.assertEqual(xml.tag, "mujoco")
        self.assertEqual(xml.find("compiler").get("angle"), "radian")
        self.assertIsNone(xml.find("actuator"))
        self.assertIsNone(xml.find(".//freejoint"))
        joints = xml.findall(".//joint")
        self.assertEqual([j.get("name") for j in joints], ["shoulder_y", "elbow_y"])
        self.assertEqual([j.get("range") for j in joints], ["-1.2 1.2", "-1.3999999999999999 1.3999999999999999"])
        self.assertEqual(xml.find("option").get("timestep"), "0.002")
        self.assertEqual(original, rig_revision(self.rig))
        self.rig["joints"][0]["length_m"] = .7
        self.assertNotEqual(original, rig_revision(self.rig))

    def test_validation_never_mutates_input(self):
        copy_rig = validate_rig(self.rig)
        copy_rig["joints"][0]["axis"][0] = 1
        self.assertNotEqual(self.rig, copy_rig)

    def test_rejects_bad_contracts(self):
        cases = []
        for field, value in [("schema", "mojum.body.rig/2"), ("dt_seconds", 2),
                             ("dt_seconds", .001), ("root_height_m", float("nan"))]:
            x = copy.deepcopy(self.rig); x[field] = value; cases.append(x)
        for k, value in [("type", "ball"), ("name", "bad<xml>"),
                         ("axis", [0, 0, 0]), ("axis", [0, 0, True]),
                         ("limits_rad", [1.5, 0.0]),
                         ("limits_rad", [float("inf"), 2]),
                         ("radius_m", 0.3), ("length_m", 0.0),
                         ("mass_kg", False)]:
            x = copy.deepcopy(self.rig); x["joints"][0][k] = value; cases.append(x)
        x = copy.deepcopy(self.rig); x["joints"][1]["name"] = "shoulder_y"; cases.append(x)
        x = copy.deepcopy(self.rig); x["joints"] *= 33; cases.append(x)
        x = copy.deepcopy(self.rig); x["unexpected"] = 1; cases.append(x)
        for x in cases:
            with self.subTest(case=str(x)[:100]), self.assertRaises(RigError):
                compile_mjcf(x)

    def test_cli_prints_mjcf_offline(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--print-mjcf"]), 0)
        self.assertEqual(ET.fromstring(output.getvalue()).tag, "mujoco")

    def test_fake_engine_nonfinite_state_latches(self):
        class FakeEngine:
            __version__ = PINNED_MUJOCO
            class MjModel:
                @staticmethod
                def from_xml_string(xml):
                    return SimpleNamespace(nu=0, nq=2, nv=2, nmocap=0,
                                           opt=SimpleNamespace(timestep=DT))
            class MjData:
                def __init__(self, model):
                    self.qpos = [0., 0.]
                    self.qvel = [0., 0.]
                    self.ncon = 0
                    self.contact = []
                    self.time = 0.
            @staticmethod
            def mj_step(model, data):
                data.qpos[0] = float("nan")
                data.time += DT
        body = HeadlessBody(self.rig, mujoco_module=FakeEngine)
        for bad in (True, 0, 10001, 2.0):
            with self.assertRaises(RigError):
                body.step(bad)
        with self.assertRaisesRegex(RigError, "fault latched"):
            body.step()
        self.assertTrue(body.faulted)
        with self.assertRaisesRegex(RigError, "fault latched"):
            body.observe()

    def test_bounded_engine_error_and_step_input(self):
        with self.assertRaisesRegex(RuntimeError, "exact MuJoCo"):
            HeadlessBody(self.rig, mujoco_module=type("Stub", (), {"__version__": "0.0"})())
        # No implicit package loading during stdlib source import or validation.
        self.assertEqual(DT, .002)
        if importlib.util.find_spec("mujoco") is None:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--steps", "1"]), 2)


@unittest.skipUnless(importlib.util.find_spec("mujoco") is not None,
                     "MuJoCo is an opt-in dependency")
class NativeMuJoCoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mujoco
        if mujoco.__version__ != PINNED_MUJOCO:
            raise unittest.SkipTest(f"engine must be MuJoCo {PINNED_MUJOCO}")

    def test_compiles_and_runs_without_controlled_actuators(self):
        body = HeadlessBody(fixture_rig())
        self.assertEqual(body.model.nu, 0)
        # Check the *compiled* engine limits, not just XML source attributes.
        # Default MJCF angle=degree would shrink every limit by pi/180.
        for i, joint in enumerate(fixture_rig()["joints"]):
            for side in (0, 1):
                self.assertAlmostEqual(
                    float(body.model.jnt_range[i, side]),
                    joint["limits_rad"][side], places=8)
        initial = body.observe()
        seen_contacts = []
        for _ in range(50):
            final = body.step()
            seen_contacts.extend(final["contacts"])
        self.assertTrue(any("floor" in (c["geom_a"], c["geom_b"])
                            for c in seen_contacts), "expected measured virtual floor contact")
        self.assertEqual(final["tick"], 50)
        self.assertAlmostEqual(final["sim_time_s"], 50 * DT, places=10)
        self.assertEqual(len(final["joints"]), 2)
        self.assertEqual(initial["rig_revision"], final["rig_revision"])
        self.assertFalse(final["hardware_contact"])
        self.assertEqual(final["actuation"], "disabled")
        self.assertTrue(all(math.isfinite(j["position_rad"]) for j in final["joints"]))

    def test_two_fresh_runs_have_same_trajectory(self):
        a = HeadlessBody(fixture_rig())
        b = HeadlessBody(fixture_rig())
        for _ in range(32):
            aa, bb = a.step(), b.step()
            self.assertEqual(aa["rig_revision"], bb["rig_revision"])
            self.assertEqual(len(aa["contacts"]), len(bb["contacts"]))
            for p, q in zip(aa["joints"], bb["joints"]):
                self.assertAlmostEqual(p["position_rad"], q["position_rad"], delta=1e-9)
            for p, q in zip(aa["contacts"], bb["contacts"]):
                self.assertEqual((p["geom_a"], p["geom_b"]), (q["geom_a"], q["geom_b"]))
                self.assertAlmostEqual(p["signed_distance_m"], q["signed_distance_m"], delta=1e-9)


if __name__ == "__main__":
    unittest.main()
