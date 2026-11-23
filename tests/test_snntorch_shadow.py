"""Offline CPU tests for optional shadow snnTorch reference (no torch required)."""

import unittest
from unittest.mock import patch

from mojum import snntorch_shadow as s


class ShadowTests(unittest.TestCase):
    def fixture(self):
        return s.synthetic_fixture(1337, ticks=16, neurons=3)

    def run_ref(self, inputs=None):
        return s.run_shadow(self.fixture() if inputs is None else inputs,
                            enabled=True, source_class="PUBLIC_SYNTHETIC")

    def test_disabled_default(self):
        with self.assertRaisesRegex(s.ShadowError, "opt-in"):
            s.run_shadow(self.fixture(), source_class="PUBLIC_SYNTHETIC")

    def test_public_only(self):
        for label in ("PRIVATE", "PUBLIC", "", "PUBLIC_SYNTHETIC "):
            with self.subTest(label=label), self.assertRaises(s.ShadowError):
                s.run_shadow(self.fixture(), enabled=True, source_class=label)

    def test_reference_repeats(self):
        a, b = self.run_ref(), self.run_ref()
        self.assertEqual(a, b)
        self.assertEqual(a.engine, "reference")
        self.assertEqual(len(a.source_digest), 64)

    def test_seed_changes_input_hash(self):
        a = self.run_ref()
        b = self.run_ref(s.synthetic_fixture(1338, ticks=16, neurons=3))
        self.assertNotEqual(a.source_digest, b.source_digest)

    def test_reference_delayed_reset_subtract(self):
        # Old membrane > threshold produces a reset on the *next* tick.
        actual = self.run_ref(((1.5,), (0.0,), (0.0,)))
        self.assertEqual(actual.spikes, ((1,), (0,), (0,)))
        self.assertAlmostEqual(actual.membranes[1][0], -0.1)

    def test_ragged_and_empty(self):
        for inp in ((), ((),), ((0.2, 0.1), (0.3,)), ((0.1,) * 33,)):
            with self.subTest(inp=inp), self.assertRaises(s.ShadowError):
                self.run_ref(inp)

    def test_ticks_bound(self):
        with self.assertRaises(s.ShadowError):
            self.run_ref(((0.2,),) * 129)

    def test_nonfinite_and_bool_currents(self):
        for bad in (float("nan"), float("inf"), float("-inf"), True, "0", 3.0):
            with self.subTest(bad=bad), self.assertRaises(s.ShadowError):
                self.run_ref(((bad,),))

    def test_bad_beta_threshold(self):
        for kw in ({"beta": 1.0}, {"beta": -0.1}, {"beta": True},
                   {"threshold": 0}, {"threshold": float("nan")}):
            with self.subTest(kw=kw), self.assertRaises(s.ShadowError):
                s.run_shadow(((0.1,),), enabled=True, source_class="PUBLIC_SYNTHETIC", **kw)

    def test_unrecognized_engine(self):
        with self.assertRaisesRegex(s.ShadowError, "engine"):
            s.run_shadow(((0.1,),), enabled=True, source_class="PUBLIC_SYNTHETIC", engine="cuda")

    def test_output_reject_corrupt_backend(self):
        good = (((0,),), ((0.1,),))
        for bad in ((((2,),), good[1]), (good[0], ((float("nan"),),)),
                    ((), ()), (([0],), good[1]), (((0,),), ((float("inf"),),))):
            with self.subTest(bad=bad), self.assertRaises(s.ShadowError):
                s._validate_output(*bad, 1, 1)

    def test_huge_integer_membrane_output_fails_closed(self):
        # math.isfinite(10**1000) raises OverflowError; this boundary
        # must issue ShadowError for both signs without leaking that exception.
        for value in (10**1000, -(10**1000)):
            with self.subTest(positive=value > 0):
                with self.assertRaisesRegex(s.ShadowError, "invalid backend membrane"):
                    s._validate_output(((0,),), ((value,),), 1, 1)

    def test_native_optional_version_gate(self):
        class FakeSNN:
            __version__ = "0.0"
        class FakeTorch:
            pass
        with patch.dict("sys.modules", {"torch": FakeTorch(), "snntorch": FakeSNN()}):
            with self.assertRaisesRegex(s.ShadowError, "pinned"):
                s.run_shadow(((0.1,),), enabled=True, source_class="PUBLIC_SYNTHETIC", engine="snntorch")

    def test_synthetic_fixture_boundaries(self):
        self.assertEqual(s.synthetic_fixture(0), s.synthetic_fixture(0))
        for kw in ({"seed": True}, {"seed": -1}, {"ticks": 0}, {"neurons": 33}):
            with self.subTest(kw=kw), self.assertRaises(s.ShadowError):
                s.synthetic_fixture(**kw)

    def test_cli_default_off_and_optin(self):
        with patch("sys.stderr"):
            self.assertEqual(s.main([]), 2)
        with patch("builtins.print") as output:
            self.assertEqual(s.main(["--enable"]), 0)
            self.assertIn('"motor_authority": false', output.call_args[0][0])

    def test_deny_bad_comparison_tolerance_preimport(self):
        with self.assertRaises(s.ShadowError):
            s.compare_shadow(((0.1,),), enabled=True, source_class="PUBLIC_SYNTHETIC", max_delta=0.5)

    def test_huge_integer_current_rejected(self):
        with self.assertRaises(s.ShadowError):
            self.run_ref(((10**10000,),))

    def test_real_snntorch_if_installed(self):
        try:
            import snntorch
            import torch
        except ImportError:
            self.skipTest("optional CPU torch/snnTorch unavailable")
        if snntorch.__version__ != s.UPSTREAM_VERSION:
            self.skipTest("only pinned snnTorch runtime supported")
        result = s.compare_shadow(self.fixture(), enabled=True, source_class="PUBLIC_SYNTHETIC")
        self.assertTrue(result.passed, result)
        self.assertFalse(result.motor_authority)
        self.assertFalse(result.learning_measured)


if __name__ == "__main__":
    unittest.main()
