"""CPU-only structural tests; MuJoCo physical behavior is an opt-in check."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

# SQLite's 3.13 leak audit invokes the suite with PYTHONPATH=src and
# sys.path[0]=tests, so repository-root 'tools' is not importable there.
# Load this standalone opt-in script directly without global sys.path edits.
_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cloth_flex_spike.py"
_SPEC = importlib.util.spec_from_file_location("mojum_cloth_flex_spike_fixture", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load cloth flex spike fixture")
_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_module)
GRID = _module.GRID
_percentile_ms = _module._percentile_ms
_vertex_penetrations = _module._vertex_penetrations
main = _module.main
make_mjcf = _module.make_mjcf


class ClothFlexSpikeTests(unittest.TestCase):
    def test_pinned_edge_uses_jointed_rig_anchor(self):
        root = ET.fromstring(make_mjcf())
        anchor = root.find("./worldbody/body[@name='cloth_anchor']")
        self.assertIsNotNone(anchor)
        self.assertNotEqual(anchor.get("mocap"), "true")
        joint = anchor.find("joint")
        self.assertIsNotNone(joint)
        self.assertEqual(joint.get("name"), "anchor_slide")
        self.assertEqual(joint.get("type"), "slide")
        self.assertEqual(joint.get("axis"), "1 0 0")
        flex = anchor.find("flexcomp")
        self.assertEqual(flex.get("dim"), "2")
        self.assertEqual(flex.get("count"), f"{GRID[0]} {GRID[1]} 1")
        self.assertEqual(flex.find("pin").get("gridrange"), "0 0 0 5")
        self.assertIsNotNone(root.find("./worldbody/geom[@name='floor']"))
        self.assertIsNotNone(root.find("./worldbody/geom[@name='collision_ball']"))

    def test_contact_options_explicit_and_separate(self):
        a = ET.fromstring(make_mjcf(selfcollide="auto", passive=True))
        b = ET.fromstring(make_mjcf(selfcollide="none", passive=False))
        aa = a.find(".//flexcomp/contact")
        bb = b.find(".//flexcomp/contact")
        self.assertEqual((aa.get("selfcollide"), aa.get("passive")), ("auto", "true"))
        self.assertEqual((bb.get("selfcollide"), bb.get("passive")), ("none", "false"))

    def test_invalid_modes_do_not_generate_xml(self):
        for value in ("all", "true", "Auto", ""):
            with self.assertRaises(ValueError):
                make_mjcf(selfcollide=value)
        with self.assertRaises(ValueError):
            make_mjcf(passive="false")

    def test_percentiles_nearest_rank_and_empty_fail(self):
        self.assertEqual(_percentile_ms([1_000_000, 2_000_000, 3_000_000, 4_000_000], .95), 4)
        with self.assertRaises(ValueError):
            _percentile_ms([], .95)

    def test_vertex_proximity_is_only_approximation(self):
        class NoVertices:
            pass
        class Vertices:
            flexvert_xpos = [(0.0, 0.0, -.01), (0.23, 0.0, .53)]
        self.assertEqual(_vertex_penetrations(NoVertices()), (None, None))
        floor, sphere = _vertex_penetrations(Vertices())
        self.assertAlmostEqual(floor, .014)
        self.assertAlmostEqual(sphere, .134)

    def test_print_xml_offline_no_dependency(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--print-mjcf", "--steps", "1"]), 0)
        self.assertEqual(ET.fromstring(output.getvalue()).tag, "mujoco")

    def test_bounded_work_and_optional_runtime(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["--steps", "20001", "--print-mjcf"])
        self.assertNotEqual(ctx.exception.code, 0)
        if importlib.util.find_spec("mujoco") is None:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--steps", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
