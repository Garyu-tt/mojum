"""Offline, dependency-free boundary QA for snnTorch LIF research spike."""

import copy
import math
import unittest

from mojum.snntorch_lif_probe import (
    LIFReference, ProbeError, MAX_NEURONS, MAX_STEPS, public_fixture,
    reference_trace, run_snntorch, validate_fixture, verify_native_trace,
)


class LIFProbeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = public_fixture()

    def test_fixed_public_trajectory_and_checksum(self):
        first = reference_trace(self.fixture)
        self.assertEqual(first, reference_trace(self.fixture))
        self.assertEqual((first["steps"], first["neurons"]), (24, 3))
        self.assertTrue(any(s for row in first["spikes"] for s in row))
        self.assertTrue(all(s in (0, 1) for row in first["spikes"] for s in row))

    def test_delayed_reset_uses_old_membrane(self):
        x = LIFReference(beta=0.5, threshold=1.0, neurons=1)
        self.assertEqual(x.step([0.8])[0], [0])
        self.assertEqual(x.step([0.8])[0], [1])
        self.assertAlmostEqual(x.membrane[0], 1.2)
        self.assertEqual(x.step([0.8])[0], [0])
        self.assertAlmostEqual(x.membrane[0], 0.4)

    def test_snapshot_resume_matches_uninterrupted(self):
        _, _, rows = validate_fixture(self.fixture)
        full = LIFReference(beta=0.75, threshold=1.0, neurons=3)
        for r in rows:
            full.step(r)
        prefix = LIFReference(beta=0.75, threshold=1.0, neurons=3)
        for r in rows[:12]:
            prefix.step(r)
        resumed = LIFReference.restore(prefix.snapshot())
        for r in rows[12:]:
            resumed.step(r)
        self.assertEqual(full.snapshot(), resumed.snapshot())

    def test_snapshot_tamper_and_unreachable(self):
        r = LIFReference(beta=0.75, threshold=1.0, neurons=1)
        s = r.snapshot()
        s["membrane"][0] = 0.2
        with self.assertRaises(ProbeError):
            LIFReference.restore(s)
        s = r.snapshot()
        s["extra"] = "bad"
        with self.assertRaises(ProbeError):
            LIFReference.restore(s)

    def test_reject_visibility_extra_and_schema(self):
        for field, value in [("visibility", "PRIVATE"), ("schema", "mojum.neural.fake/1"),
                             ("beta", True), ("threshold", math.inf)]:
            with self.subTest(field=field), self.assertRaises(ProbeError):
                f = copy.deepcopy(self.fixture)
                f[field] = value
                validate_fixture(f)
        with self.assertRaises(ProbeError):
            f = copy.deepcopy(self.fixture)
            f["motor_intention"] = "walk"
            validate_fixture(f)

    def test_reject_bounded_shape_values(self):
        bad = [[], [[0.1] * (MAX_NEURONS + 1)], [[0.1]] * (MAX_STEPS + 1),
               [[0.3], [0.1, 0.2]], [[float('nan')]], [[True]], [[-0.1]], [[3.0]]]
        for currents in bad:
            with self.subTest(currents=str(currents)[:40]), self.assertRaises(ProbeError):
                f = dict(self.fixture, currents=currents)
                validate_fixture(f)

    def test_rejected_step_does_not_mutate_state(self):
        r = LIFReference(beta=0.5, threshold=1, neurons=1)
        before = r.snapshot()
        with self.assertRaises(ProbeError):
            r.step([False])
        self.assertEqual(before, r.snapshot())

    def test_budget_enforced(self):
        r = LIFReference(beta=0.5, threshold=1, neurons=1)
        for _ in range(MAX_STEPS):
            r.step([0.0])
        before = r.snapshot()
        with self.assertRaises(ProbeError):
            r.step([0.0])
        self.assertEqual(before, r.snapshot())

    def test_native_never_runs_by_default(self):
        with self.assertRaisesRegex(ProbeError, "enabled=True"):
            run_snntorch(self.fixture)
        with self.assertRaisesRegex(ProbeError, "exact torch version"):
            run_snntorch(self.fixture, enabled=True)

    def test_exact_native_trace_acceptance_and_spike_refusal(self):
        ref = reference_trace(self.fixture)
        result = verify_native_trace(ref, copy.deepcopy(ref["spikes"]),
                                     copy.deepcopy(ref["membrane"]))
        self.assertEqual(result["status"], "SHADOW_ONLY_NUMERIC_MATCH")
        self.assertIsNone(result["motor_intention"])
        spikes = copy.deepcopy(ref["spikes"])
        spikes[0][0] = 1 - spikes[0][0]
        with self.assertRaises(ProbeError):
            verify_native_trace(ref, spikes, ref["membrane"])

    def test_native_shape_nan_bool_and_tolerance(self):
        ref = reference_trace(self.fixture)
        for value in [float('nan'), True, 100]:
            with self.subTest(value=value), self.assertRaises(ProbeError):
                mem = copy.deepcopy(ref["membrane"])
                mem[0][0] = value
                verify_native_trace(ref, ref["spikes"], mem)
        with self.assertRaises(ProbeError):
            verify_native_trace(ref, [], [])
        with self.assertRaises(ProbeError):
            mem = copy.deepcopy(ref["membrane"])
            mem[0][0] += 0.01
            verify_native_trace(ref, ref["spikes"], mem)

    def test_heldout_different_currents_not_same_digest(self):
        f = copy.deepcopy(self.fixture)
        f["currents"][0][0] = 0.7
        self.assertNotEqual(reference_trace(self.fixture)["trace_sha256"],
                            reference_trace(f)["trace_sha256"])

    def test_optional_native_if_installed(self):
        try:
            import torch
            import snntorch
        except ImportError:
            self.skipTest("optional snntorch/PyTorch not installed")
        if snntorch.__version__ != "1.0.0":
            self.skipTest("requires pinned snntorch 1.0.0")
        result = run_snntorch(self.fixture, enabled=True,
                              expected_torch_version=torch.__version__)
        self.assertEqual(result["status"], "SHADOW_ONLY_NUMERIC_MATCH")


if __name__ == "__main__":
    unittest.main()
