"""Offline and optional-native checks for the isolated Nengo baseline."""
import importlib.util
import sys
import types
import unittest
from unittest import mock

from mojum.nengo_lif_baseline import (
    LIFSpec, MAX_CELLS, NENGO_PIN, SCHEMA, compare, fixture,
    run_nengo, run_reference, validate,
)


class _Array(list):
    """Minimal stdlib NumPy stand-in for optional-boundary unit tests only."""
    def fill(self, value):
        self[:] = [value] * len(self)

    def __setitem__(self, index, value):
        if isinstance(index, slice) and type(value) in (int, float):
            value = [value] * len(self[index])
        super().__setitem__(index, value)

    def __lt__(self, value):
        return [v < value for v in self]


class _Finite(list):
    def all(self):
        return all(self)


FAKE_NUMPY = types.SimpleNamespace(
    float64=float,
    zeros=lambda n, dtype=None: _Array([0.0] * n),
    asarray=lambda values, dtype=None: _Array(values),
    isfinite=lambda values: _Finite([__import__("math").isfinite(v) for v in values]),
    any=any,
)


class NengoLIFBaselineTests(unittest.TestCase):
    def test_reference_deterministic(self):
        spec = fixture(1000)
        a, b = run_reference(spec), run_reference(spec)
        self.assertEqual(a, b)
        self.assertEqual(a.steps, 1000)
        self.assertEqual(a.neurons, 5)
        self.assertEqual(a.counts[:2], (0, 0))
        self.assertTrue(all(v > 0 for v in a.counts[2:]))
        self.assertEqual(len(a.sha256), 64)

    def test_fixture_no_auto_import_of_native(self):
        spec = fixture(10)
        with mock.patch.dict(sys.modules, {"nengo": None}):
            self.assertEqual(run_reference(spec).steps, 10)
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                run_nengo(spec)

    def test_bad_step_parameter(self):
        for value in (True, 0, -1, 4097, 1.0, "100", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fixture(value)

    def test_reject_invalid_envelopes(self):
        bad = [
            LIFSpec(((0.0,),), schema="unknown"),
            LIFSpec([]),
            LIFSpec(((),)),
            LIFSpec(((1.0,), (1.0, 2.0))),
            LIFSpec(((True,),)),
            LIFSpec(((float("nan"),),)),
            LIFSpec(((float("inf"),),)),
            LIFSpec(((-0.001,),)),
            LIFSpec(((5.001,),)),
            LIFSpec(((10 ** 1000,),)),
            LIFSpec((("PRIVATE",),)),
            LIFSpec(((1.0,),), dt_s=0),
            LIFSpec(((1.0,),), dt_s=True),
            LIFSpec(((1.0,),), tau_ref_s=1),
            LIFSpec(((1.0,),), tau_rc_s=0.003),
            LIFSpec(((1.0,),), tau_rc_s=float("inf")),
            LIFSpec(((1.0,) * 33,)),
            LIFSpec(((1.0,) * 32,) * (MAX_CELLS // 32 + 1)),
        ]
        for spec in bad:
            with self.subTest(spec=str(spec)[:60]), self.assertRaises(ValueError):
                validate(spec)

    def test_native_pin_is_fail_closed(self):
        spec = fixture(5)
        with mock.patch.dict(sys.modules, {"nengo": types.SimpleNamespace(__version__="3.2.0")}):
            with self.assertRaisesRegex(RuntimeError, "exact 4.1.0"):
                run_nengo(spec)

    def test_native_low_level_step_shape_with_stub(self):
        class FakeLIF:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def step(self, dt, j, output, voltage, refractory):
                output[:] = [1.0 / dt if value > 1.0 else 0.0 for value in j]
                voltage[:] = 0.0
                refractory[:] = 0.0

        fake = types.SimpleNamespace(__version__=NENGO_PIN, LIF=FakeLIF)
        with mock.patch.dict(sys.modules, {"nengo": fake, "numpy": FAKE_NUMPY}):
            r = run_nengo(fixture(5))
        self.assertEqual(r.counts, (0, 0, 5, 5, 5))
        self.assertEqual(r.events[0], (0, 0, 1, 1, 1))

    def test_native_invalid_state_is_rejected(self):
        class BadLIF:
            def __init__(self, **kwargs):
                pass

            def step(self, dt, j, output, voltage, refractory):
                output[:] = float("nan")

        fake = types.SimpleNamespace(__version__=NENGO_PIN, LIF=BadLIF)
        with mock.patch.dict(sys.modules, {"nengo": fake, "numpy": FAKE_NUMPY}):
            with self.assertRaisesRegex(ValueError, "invalid Nengo"):
                run_nengo(fixture(2))

    def test_compare_is_research_not_admission(self):
        class FakeLIF:
            def __init__(self, **kwargs):
                pass

            def step(self, dt, j, output, voltage, refractory):
                output[:] = [1.0 / dt if value > 1.0 else 0.0 for value in j]

        fake = types.SimpleNamespace(__version__=NENGO_PIN, LIF=FakeLIF)
        with mock.patch.dict(sys.modules, {"nengo": fake, "numpy": FAKE_NUMPY}):
            out = compare(fixture(20))
        self.assertIs(out["approved"], False)
        self.assertEqual(len(out["nengo_rates_hz"]), 5)
        self.assertGreaterEqual(out["max_rate_gap_hz"], 0)

    @unittest.skipUnless(importlib.util.find_spec("nengo"), "optional nengo==4.1.0 not installed")
    def test_real_nengo_4_1_if_installed(self):
        spec = fixture(1000)
        a, b = run_nengo(spec), run_nengo(spec)
        self.assertEqual(a, b)
        self.assertEqual(a.counts[:2], (0, 0))
        self.assertTrue(all(v > 0 for v in a.counts[2:]))


if __name__ == "__main__":
    unittest.main()
