import copy
import unittest

from mojum.neural import NeuralError, PulsePredictor
from mojum.neural_observe_guard import (
    MAX_TIMESTAMP_NS,
    NeuralObserveGuardError,
    PUBLIC_SYNTHETIC,
    observe_public_synthetic,
)


def sample(at=0, *, beat=0, phase=0, pulse=True, synthetic=True):
    return {
        "kind": "interoception",
        "observed_at_ns": at,
        "heartbeat": {
            "beat_index": beat,
            "phase_milli": phase,
            "pulse": pulse,
            "synthetic": synthetic,
        },
    }


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


class HostileDict(dict):
    def get(self, *args, **kwargs):
        raise AssertionError("custom get must never be called")


class NeuralObserveGuardTests(unittest.TestCase):
    def test_valid_frames_delegate_to_predictor(self):
        model = PulsePredictor()
        first = observe_public_synthetic(model, sample(), visibility=PUBLIC_SYNTHETIC)
        second = observe_public_synthetic(
            model, sample(10, beat=1, phase=10, pulse=False), visibility=PUBLIC_SYNTHETIC
        )
        self.assertFalse(first["trained"])
        self.assertTrue(second["trained"])
        self.assertEqual(model.updates, 1)
        self.assertIsNone(second["motor_intention"])

    def test_requires_explicit_exact_visibility(self):
        for visibility in (None, "PRIVATE", StrSubclass(PUBLIC_SYNTHETIC), True):
            with self.subTest(visibility=visibility):
                model = PulsePredictor()
                before = model.snapshot()
                with self.assertRaises(NeuralObserveGuardError):
                    observe_public_synthetic(model, sample(), visibility=visibility)
                self.assertEqual(model.snapshot(), before)

    def test_requires_exact_predictor_type(self):
        class PredictorChild(PulsePredictor):
            pass
        with self.assertRaises(NeuralObserveGuardError):
            observe_public_synthetic(PredictorChild(), sample(), visibility=PUBLIC_SYNTHETIC)

    def test_rejects_dict_subclasses_without_calling_custom_methods(self):
        model = PulsePredictor()
        bad = HostileDict(sample())
        before = model.snapshot()
        with self.assertRaises(NeuralObserveGuardError):
            observe_public_synthetic(model, bad, visibility=PUBLIC_SYNTHETIC)
        self.assertEqual(model.snapshot(), before)

    def test_rejects_non_native_and_hostile_keys(self):
        model = PulsePredictor()
        bad = sample()
        bad[StrSubclass("extra")] = 1
        with self.assertRaises(NeuralObserveGuardError):
            observe_public_synthetic(model, bad, visibility=PUBLIC_SYNTHETIC)
        self.assertEqual(model.updates, 0)

    def test_rejects_missing_and_unknown_outer_fields(self):
        for mutate in (lambda x: x.pop("heartbeat"), lambda x: x.update(extra=0)):
            bad = sample()
            mutate(bad)
            with self.subTest(bad=bad), self.assertRaises(NeuralObserveGuardError):
                observe_public_synthetic(PulsePredictor(), bad, visibility=PUBLIC_SYNTHETIC)

    def test_rejects_missing_and_unknown_heartbeat_fields(self):
        for mutate in (lambda x: x.pop("synthetic"), lambda x: x.update(extra=0)):
            bad = sample()
            mutate(bad["heartbeat"])
            with self.subTest(bad=bad), self.assertRaises(NeuralObserveGuardError):
                observe_public_synthetic(PulsePredictor(), bad, visibility=PUBLIC_SYNTHETIC)

    def test_rejects_bad_outer_and_heartbeat_types(self):
        cases = []
        x = sample(); x["kind"] = StrSubclass("interoception"); cases.append(x)
        x = sample(); x["observed_at_ns"] = True; cases.append(x)
        x = sample(); x["observed_at_ns"] = IntSubclass(0); cases.append(x)
        x = sample(); x["heartbeat"] = HostileDict(x["heartbeat"]); cases.append(x)
        x = sample(); x["heartbeat"] = []; cases.append(x)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(NeuralObserveGuardError):
                observe_public_synthetic(PulsePredictor(), bad, visibility=PUBLIC_SYNTHETIC)

    def test_rejects_bad_timestamp_range_and_huge_integer(self):
        for at in (-1, MAX_TIMESTAMP_NS + 1, 10**5000):
            with self.subTest(at=type(at).__name__), self.assertRaises(NeuralObserveGuardError):
                observe_public_synthetic(PulsePredictor(), sample(at), visibility=PUBLIC_SYNTHETIC)

    def test_rejects_invalid_heartbeat_scalars(self):
        cases = (
            {"beat_index": True, "phase_milli": 0, "pulse": True, "synthetic": True},
            {"beat_index": -1, "phase_milli": 0, "pulse": True, "synthetic": True},
            {"beat_index": 0, "phase_milli": True, "pulse": True, "synthetic": True},
            {"beat_index": 0, "phase_milli": 1000, "pulse": True, "synthetic": True},
            {"beat_index": 0, "phase_milli": 0, "pulse": 1, "synthetic": True},
            {"beat_index": 0, "phase_milli": 0, "pulse": True, "synthetic": 1},
            {"beat_index": 0, "phase_milli": 0, "pulse": True, "synthetic": False},
        )
        for heartbeat in cases:
            bad = sample()
            bad["heartbeat"] = heartbeat
            with self.subTest(heartbeat=heartbeat), self.assertRaises(NeuralObserveGuardError):
                observe_public_synthetic(PulsePredictor(), bad, visibility=PUBLIC_SYNTHETIC)

    def test_rejects_bad_kind_before_mutation(self):
        model = PulsePredictor()
        bad = sample(); bad["kind"] = "world"
        before = copy.deepcopy(model.snapshot())
        with self.assertRaises(NeuralObserveGuardError):
            observe_public_synthetic(model, bad, visibility=PUBLIC_SYNTHETIC)
        self.assertEqual(model.snapshot(), before)

    def test_stale_timestamp_is_rejected_without_mutation(self):
        model = PulsePredictor()
        observe_public_synthetic(model, sample(10), visibility=PUBLIC_SYNTHETIC)
        before = model.snapshot()
        with self.assertRaises(NeuralError):
            observe_public_synthetic(model, sample(10, beat=1), visibility=PUBLIC_SYNTHETIC)
        self.assertEqual(model.snapshot(), before)

    def test_caller_owned_frames_are_not_rewritten_or_retained(self):
        model = PulsePredictor()
        frame = sample()
        before = copy.deepcopy(frame)
        observe_public_synthetic(model, frame, visibility=PUBLIC_SYNTHETIC)
        self.assertEqual(frame, before)
        frame["heartbeat"]["phase_milli"] = 999
        self.assertEqual(model.snapshot()["pending_features"], [1.0, 0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
