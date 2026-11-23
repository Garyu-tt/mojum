"""Adversarial, deterministic tests for the frozen pulse horizon audit."""
import copy
import math
import unittest

from mojum.neural import PulsePredictor
from mojum.pulse_horizon_audit import (
    INPUT_SCHEMA, MAX_HORIZON, MAX_ORIGINS, MAX_FRAMES,
    PulseHorizonError, audit_pulse_horizons,
)


def make_document(*, max_horizon=3, max_origins=8, pulses=None, phases=None):
    count = max_horizon + max_origins
    pulses = list(pulses) if pulses is not None else [False, True, True, False, True, False, False, True, True, False, True]
    phases = list(phases) if phases is not None else [(i * 137) % 1000 for i in range(count)]
    frames = []
    for i in range(count):
        frames.append({
            "tick": 20 + i,
            "observed_at_ns": 1_000_000 + i * 10_000_000,
            "phase_milli": phases[i],
            "pulse": pulses[i],
        })
    predictor = PulsePredictor(learning_rate=0.1, summation="compensated")
    predictor.observe({"kind": "interoception", "observed_at_ns": 100, "heartbeat": {"phase_milli": 100, "pulse": False}})
    predictor.observe({"kind": "interoception", "observed_at_ns": 200, "heartbeat": {"phase_milli": 200, "pulse": True}})
    return {
        "schema": INPUT_SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "consent": True,
        "summation": "compensated",
        "snapshot": predictor.snapshot(),
        "frames": frames,
        "max_horizon": max_horizon,
        "max_origins": max_origins,
    }


class PulseHorizonAuditTests(unittest.TestCase):
    def test_returns_every_requested_horizon_and_bounded_sample_count(self):
        report = audit_pulse_horizons(make_document(max_horizon=4, max_origins=5))
        self.assertEqual([m.horizon for m in report.metrics], [1, 2, 3, 4])
        self.assertEqual([m.sample_count for m in report.metrics], [5] * 4)
        self.assertEqual(report.origin_count, 5)
        self.assertEqual(report.max_horizon, 4)

    def test_report_is_deterministic(self):
        document = make_document()
        self.assertEqual(audit_pulse_horizons(document), audit_pulse_horizons(copy.deepcopy(document)))

    def test_future_label_changes_scores_but_not_autoregressive_forecasts(self):
        document = make_document(max_horizon=3, max_origins=1)
        changed = copy.deepcopy(document)
        for frame in changed["frames"][1:]:
            frame["pulse"] = not frame["pulse"]
        a = audit_pulse_horizons(document)
        b = audit_pulse_horizons(changed)
        self.assertEqual(a.forecast_sha256, b.forecast_sha256)
        self.assertNotEqual(a.inputs_sha256, b.inputs_sha256)
        self.assertNotEqual([m.model_brier for m in a.metrics],
                            [m.model_brier for m in b.metrics])

    def test_future_phase_covariate_changes_forecasts(self):
        document = make_document()
        changed = copy.deepcopy(document)
        changed["frames"][1]["phase_milli"] = (changed["frames"][1]["phase_milli"] + 1) % 1000
        self.assertNotEqual(audit_pulse_horizons(document).forecast_sha256,
                            audit_pulse_horizons(changed).forecast_sha256)

    def test_persistence_baseline_is_reported(self):
        report = audit_pulse_horizons(make_document(max_horizon=2, max_origins=4))
        self.assertTrue(all(metric.persistence_mae >= 0 for metric in report.metrics))
        self.assertTrue(all(metric.persistence_brier >= 0 for metric in report.metrics))

    def test_report_serializes_to_detached_plain_data(self):
        report = audit_pulse_horizons(make_document())
        exported = report.to_dict()
        exported["metrics"][0]["horizon"] = 999
        self.assertEqual(report.metrics[0].horizon, 1)
        self.assertFalse(report.to_dict()["training_performed"])
        self.assertFalse(report.to_dict()["future_labels_used_as_features"])

    def test_model_rollout_does_not_mutate_source_snapshot(self):
        document = make_document()
        before = copy.deepcopy(document["snapshot"])
        audit_pulse_horizons(document)
        self.assertEqual(document["snapshot"], before)

    def test_zero_weights_produce_persistence_like_zero_forecasts(self):
        document = make_document(max_horizon=2, max_origins=3)
        for frame in document["frames"]:
            frame["pulse"] = False
        document["snapshot"]["weights"] = [0.0, 0.0, 0.0]
        report = audit_pulse_horizons(document)
        self.assertTrue(all(metric.model_brier == 0.0 for metric in report.metrics))

    def test_rejects_missing_or_false_consent_and_nonpublic_provenance(self):
        for changes in ({"consent": False}, {"consent": 1}, {"provenance": "PRIVATE"}, {"provenance": "public_synthetic"}):
            document = make_document()
            document.update(changes)
            with self.subTest(changes=changes), self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)

    def test_rejects_unknown_or_missing_input_fields(self):
        for mutate in (
            lambda d: d.update(extra=True),
            lambda d: d.pop("summation"),
        ):
            document = make_document()
            mutate(document)
            with self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)

    def test_rejects_subclassed_mapping_and_nonstring_key(self):
        class DictChild(dict):
            pass
        document = make_document()
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(DictChild(document))
        document = make_document()
        document[1] = "bad"
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)

    def test_rejects_malformed_snapshot_weights_and_numeric_types(self):
        for weights in ([True, 0.0, 0.0], [math.nan, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 0.0]):
            document = make_document()
            document["snapshot"]["weights"] = weights
            with self.subTest(weights=weights), self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)

    def test_rejects_unknown_snapshot_field(self):
        document = make_document()
        document["snapshot"]["unexpected"] = 1
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)

    def test_rejects_invalid_summation_and_snapshot_schema(self):
        for changes in ({"summation": "fast"},):
            document = make_document()
            document.update(changes)
            with self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)
        document = make_document()
        document["snapshot"]["schema"] = "future/2"
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)

    def test_rejects_tick_gaps_and_duplicate_timestamps(self):
        for field, value in (("tick", 999), ("observed_at_ns", 1_000_000)):
            document = make_document()
            document["frames"][2][field] = value
            with self.subTest(field=field), self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)

    def test_rejects_bool_numeric_and_wrong_native_frame_types(self):
        for index, field, value in (
            (1, "tick", True), (1, "observed_at_ns", False),
            (1, "phase_milli", True), (1, "pulse", 1),
        ):
            document = make_document()
            document["frames"][index][field] = value
            with self.subTest(field=field), self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)

    def test_rejects_frame_schema_drift_and_nonfinite_values(self):
        document = make_document()
        document["frames"][1]["extra"] = 2
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)
        document = make_document()
        document["snapshot"]["loss_sum"] = math.inf
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)

    def test_rejects_horizon_origin_and_input_size_budget_overflow(self):
        for field, value in (("max_horizon", MAX_HORIZON + 1),
                             ("max_horizon", True),
                             ("max_origins", MAX_ORIGINS + 1),
                             ("max_origins", 0)):
            document = make_document()
            document[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)
        document = make_document(max_horizon=1, max_origins=1)
        document["frames"] = document["frames"] * (MAX_FRAMES // len(document["frames"]) + 1)
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)

    def test_rejects_too_few_frames_and_invalid_phase_pulse(self):
        document = make_document(max_horizon=2, max_origins=2)
        document["frames"] = document["frames"][:2]
        with self.assertRaises(PulseHorizonError):
            audit_pulse_horizons(document)
        for field, value in (("phase_milli", 1000), ("pulse", "yes")):
            document = make_document()
            document["frames"][1][field] = value
            with self.subTest(field=field), self.assertRaises(PulseHorizonError):
                audit_pulse_horizons(document)


if __name__ == "__main__":
    unittest.main()
