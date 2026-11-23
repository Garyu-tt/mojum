import copy
import math
import sys
import types
import unittest
from unittest import mock

from mojum.annarchy_rate_probe import (
    ANNARCHY_VERSION, BRIAN2_VERSION, MAX_STEPS, ProbeError, RateShadow,
    RateSpec, SCHEMA, compare_rate_traces, _simulate_annarchy
)


def fixture(count=40):
    # Entirely synthetic, fixed and PUBLIC. Every sample is a known scalar.
    return [[(tick + neuron * 3) % 11 / 10
             for neuron in range(3)] for tick in range(count)]


class RateShadowProofTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with self.assertRaisesRegex(ProbeError, "opt-in"):
            compare_rate_traces(fixture())

    def test_deterministic_replay_and_provenance(self):
        a = compare_rate_traces(fixture(), enabled=True)
        b = compare_rate_traces(fixture(), enabled=True)
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], SCHEMA)
        self.assertEqual(a["tick_count"], 40)
        self.assertEqual(a["max_abs_error"], 0)
        self.assertIsNone(a["motor_intention"])
        self.assertFalse(a["physical_contact"])
        self.assertEqual(len(a["reference_sha256"]), 64)

    def test_euler_step_known_value(self):
        p = RateShadow(RateSpec(neurons=2, dt_ms=1, tau_ms=10))
        self.assertEqual(p.step([1, 0])["rates"], [0.1, 0.0])
        self.assertEqual(p.step([1, 1])["rates"], [0.19, 0.1])

    def test_snapshot_reopen_and_causal_replay(self):
        p = RateShadow()
        for frame in fixture(20):
            p.step(frame)
        saved = p.snapshot()
        q = RateShadow.restore(saved)
        future = fixture(40)[20:]
        self.assertEqual([p.step(f) for f in future],
                         [q.step(f) for f in future])
        self.assertEqual(p.snapshot(), q.snapshot())
        saved["rates"][0] = 666
        self.assertNotEqual(p.snapshot()["rates"][0], 666)

    def test_snapshot_hash_and_schema_fail_closed(self):
        p = RateShadow()
        p.step([0.1, 0.2, 0.3])
        good = p.snapshot()
        for key, value in (("tick", 0), ("schema", "another/1"),
                           ("rates", [1, 1, 1]), ("sha256", "0" * 64),
                           ("spec", {"neurons": 3})):
            with self.subTest(key=key):
                s = copy.deepcopy(good)
                s[key] = value
                with self.assertRaises(ProbeError):
                    RateShadow.restore(s)
        s = copy.deepcopy(good)
        s["private_snapshot"] = "forbidden"
        with self.assertRaises(ProbeError):
            RateShadow.restore(s)

    def test_zero_step_hash_checked(self):
        s = RateShadow().snapshot()
        s["rates"][1] = 0.5
        with self.assertRaises(ProbeError):
            RateShadow.restore(s)

    def test_nan_bool_and_invalid_values_refused(self):
        for bad in (None, True, False, math.nan, math.inf, -1, 1.01,
                    "0.2", {}, 10**5000):
            with self.subTest(bad_type=type(bad).__name__), self.assertRaises(ProbeError):
                compare_rate_traces([[bad, 0, 0]], enabled=True)

    def test_no_unknown_or_ragged_or_extra_data(self):
        for bad in ([], [[0, 0]], [[0, 0, 0, 0]], [{}],
                    {"private": "secret"}, [[0, 0, 0]] * (MAX_STEPS + 1)):
            with self.subTest(shape=str(bad)[:30]), self.assertRaises(ProbeError):
                compare_rate_traces(bad, enabled=True)

    def test_strict_spec_and_budget(self):
        for params in ({"neurons": 0}, {"neurons": 17}, {"neurons": True},
                       {"tau_ms": math.nan}, {"tau_ms": 1},
                       {"dt_ms": 0}, {"dt_ms": True}, {"dt_ms": 1.01},
                       {"dt_ms": 1, "tau_ms": 1}):
            with self.subTest(params=params), self.assertRaises(ProbeError):
                RateSpec(**params)

    def test_reference_bounded_to_512_steps(self):
        p = RateShadow(RateSpec(neurons=1))
        for _ in range(MAX_STEPS):
            p.step([0])
        snapshot = p.snapshot()
        with self.assertRaisesRegex(ProbeError, "budget"):
            p.step([0])
        self.assertEqual(p.snapshot(), snapshot)

    def test_wrong_backend_and_tolerance_refused(self):
        for backend in ("cuda", "litellm", "", None):
            with self.subTest(backend=backend), self.assertRaises(ProbeError):
                compare_rate_traces(fixture(2), backend=backend, enabled=True)
        for tolerance in (math.nan, math.inf, False, -1, 0.1, "1e-4"):
            with self.subTest(tolerance=tolerance), self.assertRaises(ProbeError):
                compare_rate_traces(fixture(2), tolerance=tolerance, enabled=True)

    def test_optional_dependency_missing_fails_closed(self):
        for name in ("ANNarchy", "brian2"):
            with self.subTest(name=name), mock.patch.dict(sys.modules, {name: None}):
                with self.assertRaisesRegex(ProbeError, "optional"):
                    compare_rate_traces(fixture(2),
                                        backend="annarchy" if name == "ANNarchy" else "brian2",
                                        enabled=True)

    def test_reject_unpinned_backends_before_running(self):
        for name in ("ANNarchy", "brian2"):
            with self.subTest(name=name):
                fake = types.ModuleType(name)
                fake.__version__ = "0.0.0"
                with mock.patch.dict(sys.modules, {name: fake}):
                    with self.assertRaisesRegex(ProbeError, "version not pinned"):
                        compare_rate_traces(fixture(2),
                                            backend="annarchy" if name == "ANNarchy" else "brian2",
                                            enabled=True)

    def test_fake_annarchy_network_contract_without_compiler(self):
        class FakePop:
            def __init__(self, n):
                self.r = [0.0] * n
                self.drive = [0.0] * n
        class FakeNetwork:
            def create(self, n, model):
                self.population = FakePop(n)
                return self.population
            def compile(self, *, directory):
                self.compiled = True
            def simulate(self, dt):
                self.population.r = [r + dt / 10 * (i - r)
                                     for r, i in zip(self.population.r,
                                                     self.population.drive)]
        fake = types.ModuleType("ANNarchy")
        fake.__version__ = ANNARCHY_VERSION
        fake.Neuron = lambda **kwargs: kwargs
        fake.Network = FakeNetwork
        with mock.patch.dict(sys.modules, {"ANNarchy": fake}):
            output = compare_rate_traces(fixture(15), backend="annarchy", enabled=True)
        self.assertEqual(output["max_abs_error"], 0)
        self.assertEqual(output["backend"], "annarchy")
        self.assertIsNone(output["motor_intention"])

    def test_forged_backend_divergence_rejected(self):
        with mock.patch("mojum.annarchy_rate_probe._simulate_annarchy",
                        return_value=((999.0, 0.0, 0.0),)):
            with self.assertRaisesRegex(ProbeError, "unsafe"):
                compare_rate_traces(fixture(1), enabled=True, backend="annarchy")
        with mock.patch("mojum.annarchy_rate_probe._simulate_annarchy",
                        return_value=((0.0, 0.0, 0.0),)):
            with self.assertRaisesRegex(ProbeError, "diverged"):
                compare_rate_traces(fixture(1), enabled=True, backend="annarchy")

    def test_frozen_input_cannot_be_changed_by_simulator(self):
        data = fixture()
        frozen = copy.deepcopy(data)
        compare_rate_traces(data, enabled=True)
        self.assertEqual(data, frozen)


if __name__ == "__main__":
    unittest.main()
