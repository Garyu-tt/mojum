import importlib.metadata
import importlib.util
import json
import subprocess
import sys
import unittest
from unittest import mock

from mojum.nengo_shadow import (
    MAX_NEURONS, MAX_SEED, MAX_STEPS, NengoUnavailableError,
    PINNED_NENGO, run_nengo, run_reference,
)


class NengoShadowFixtureTests(unittest.TestCase):
    def test_reference_deterministic_and_bounded(self):
        a = run_reference(seed=7, steps=128, neurons=16)
        self.assertEqual(a, run_reference(seed=7, steps=128, neurons=16))
        self.assertEqual(a["schema"], "mojum.nengo-shadow-spike-fixture/1")
        self.assertEqual(a["mode"], "shadow_only")
        self.assertEqual(a["source"], "PUBLIC_SYNTHETIC_ONLY")
        self.assertGreater(a["spike_events"], 0)
        self.assertLessEqual(a["spike_events"], 128 * 16)
        self.assertEqual(len(a["spike_bins_sha256"]), 64)
        self.assertNotIn("motor_command", a)
        self.assertNotIn("private_payload", a)

    def test_seeds_change_reference_trace(self):
        self.assertNotEqual(
            run_reference(seed=1)["spike_bins_sha256"],
            run_reference(seed=2)["spike_bins_sha256"],
        )

    def test_exact_integer_and_resource_limits(self):
        for key, bad in (
            ("seed", True), ("seed", -1), ("seed", MAX_SEED + 1),
            ("seed", 1.0), ("steps", False), ("steps", 0),
            ("steps", MAX_STEPS + 1), ("steps", 1.0),
            ("neurons", True), ("neurons", 0),
            ("neurons", MAX_NEURONS + 1), ("neurons", 1.0),
        ):
            with self.subTest(key=key, bad=bad):
                with self.assertRaises(ValueError):
                    run_reference(**{key: bad})
                with self.assertRaises(ValueError):
                    run_nengo(enabled=True, **{key: bad})

    def test_nengo_requires_explicit_opt_in_even_when_installed(self):
        with self.assertRaisesRegex(ValueError, "explicit enabled"):
            run_nengo()
        with self.assertRaisesRegex(ValueError, "explicit enabled"):
            run_nengo(enabled=1)

    def test_wrong_version_fails_closed_before_import(self):
        with mock.patch.object(importlib.metadata, "version", return_value="0.0.1"):
            with self.assertRaises(NengoUnavailableError):
                run_nengo(enabled=True)

    def test_missing_package_fails_closed(self):
        with mock.patch.object(
            importlib.metadata, "version",
            side_effect=importlib.metadata.PackageNotFoundError("nengo"),
        ):
            with self.assertRaises(NengoUnavailableError):
                run_nengo(enabled=True)

    def test_cli_requires_execute(self):
        process = subprocess.run(
            [sys.executable, "-m", "mojum.nengo_shadow",
             "--backend", "stdlib"],
            capture_output=True, text=True,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("--execute", process.stderr)
        self.assertEqual(process.stdout, "")

    def test_cli_reference_replay(self):
        command = [sys.executable, "-m", "mojum.nengo_shadow",
                   "--backend", "stdlib", "--execute",
                   "--steps", "64", "--neurons", "8"]
        a = subprocess.run(command, capture_output=True, text=True, check=True)
        b = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(a.stdout), json.loads(b.stdout))

    @unittest.skipUnless(
        importlib.util.find_spec("nengo") is not None,
        "optional Nengo not installed",
    )
    def test_actual_nengo_replay_if_exact_pin_installed(self):
        if importlib.metadata.version("nengo") != PINNED_NENGO:
            self.skipTest("Nengo installed but not at reviewed pin")
        a = run_nengo(enabled=True, steps=128, neurons=16)
        b = run_nengo(enabled=True, steps=128, neurons=16)
        self.assertEqual(a, b)
        self.assertEqual(a["engine"], "nengo_lif")
        self.assertGreater(a["spike_events"], 0)
        self.assertLessEqual(a["spike_events"], 128 * 16)


if __name__ == "__main__":
    unittest.main()
