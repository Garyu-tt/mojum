"""Regression for type-confusion in PRIVATE scripted-school event export."""
import copy
from pathlib import Path
import tempfile
import unittest

from mojum.school import SchoolContractError, load_catalog, run_scripted_activity, write_fixture

CATALOG = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class SchoolScalarTypeIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(CATALOG)

    def test_python_equal_but_json_different_scalar_tampering_is_rejected(self):
        original = run_scripted_activity(self.catalog, "music", start_tick=12)
        changes = {
            "float-tick": lambda run: run["events"][4].__setitem__("sim_tick", 16.0),
            "float-measurement": lambda run: run["events"][2]["evidence"].__setitem__(
                "measured", 200.0),
            "boolean-score": lambda run: run["feedback"].__setitem__("revised", False),
            "float-score": lambda run: run["feedback"].__setitem__("revised", 0.0),
        }
        with tempfile.TemporaryDirectory() as temp:
            for name, change in changes.items():
                with self.subTest(case=name):
                    poisoned = copy.deepcopy(original)
                    change(poisoned)
                    target = Path(temp) / name
                    with self.assertRaises(SchoolContractError):
                        write_fixture(poisoned, target)
                    self.assertFalse(target.exists())

    def test_unmodified_private_outputs_remain_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            for section in ("music", "visual_arts"):
                for mode in ("revision", "frozen_control"):
                    with self.subTest(section=section, mode=mode):
                        run = run_scripted_activity(self.catalog, section,
                                                    start_tick=12, mode=mode)
                        target = Path(temp) / (section + "-" + mode)
                        self.assertEqual(write_fixture(run, target), target)
                        self.assertTrue((target / "events.json").is_file())


if __name__ == "__main__":
    unittest.main()
