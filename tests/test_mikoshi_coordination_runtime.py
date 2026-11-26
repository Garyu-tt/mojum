import json
import unittest
from dataclasses import replace

from mojum import autonomic_homeostasis as ah
from mojum import cerebellar_coordination as cc
from mojum.mikoshi_coordination_runtime import (
    MikoshiCoordinationRuntime,
    RuntimeError,
    SCHEMA,
    SNAPSHOT_SCHEMA,
    snapshot_json,
)


REVISION = 13


def c_observation(tick, **changes):
    values = {
        "schema": cc.OBSERVATION_SCHEMA,
        "classification": cc.CLASSIFICATION,
        "tick": tick,
        "revision": REVISION,
        "posture_error_milli": 100,
        "velocity_error_milli": 0,
        "support_error_milli": 0,
        "fault": False,
    }
    values.update(changes)
    return cc.CoordinationObservation(**values)


def a_observation(tick, **changes):
    values = {
        "schema": ah.OBSERVATION_SCHEMA,
        "classification": ah.CLASSIFICATION,
        "tick": tick,
        "revision": REVISION,
        "energy_milli": 600,
        "load_milli": 400,
        "thermal_deviation_milli": 0,
        "fault": False,
    }
    values.update(changes)
    return ah.AutonomicObservation(**values)


class RuntimeTests(unittest.TestCase):
    def runtime(self, *, c_cadence=2, a_cadence=5):
        return MikoshiCoordinationRuntime(
            replace(cc.default_profile(enabled=True), cadence_ticks=c_cadence),
            replace(ah.default_profile(enabled=True), cadence_ticks=a_cadence),
            revision=REVISION,
        )

    def test_independent_cadences_on_one_shared_clock(self):
        runtime = self.runtime()
        results = [
            runtime.step(c_observation(tick), a_observation(tick), tick=tick)
            for tick in range(21)
        ]
        self.assertEqual(results[0].schema, SCHEMA)
        self.assertEqual(runtime.next_tick, 21)
        self.assertEqual(runtime.coordination_state.update_count, 11)
        self.assertEqual(runtime.autonomic_state.update_count, 5)
        self.assertEqual(
            [item.tick for item in results if item.coordination.ran],
            list(range(0, 21, 2)),
        )
        self.assertEqual(
            [item.tick for item in results if item.autonomic.ran],
            [0, 5, 10, 15, 20],
        )

    def test_fault_is_local_to_its_subsystem(self):
        runtime = self.runtime()
        result = runtime.step(
            c_observation(0, fault=True),
            a_observation(0),
            tick=0,
        )
        self.assertEqual(result.coordination.status, "fault")
        self.assertEqual(result.coordination.correction_milli, 0)
        self.assertEqual(result.autonomic.status, "updated")
        self.assertEqual(runtime.coordination_state.fault_count, 1)
        self.assertEqual(runtime.autonomic_state.fault_count, 0)

    def test_invalid_second_input_does_not_commit_first_transition(self):
        runtime = self.runtime()
        before = runtime.snapshot()
        invalid = a_observation(0, revision=REVISION + 1)
        with self.assertRaises(ah.HomeostasisError):
            runtime.step(c_observation(0), invalid, tick=0)
        self.assertEqual(runtime.snapshot(), before)
        self.assertEqual(runtime.next_tick, 0)

    def test_shared_clock_and_observation_validation(self):
        runtime = self.runtime()
        with self.assertRaises(RuntimeError):
            runtime.step(c_observation(0), a_observation(0), tick=1)
        with self.assertRaises(cc.CoordinationError):
            runtime.step(c_observation(1), a_observation(0), tick=0)
        self.assertEqual(runtime.next_tick, 0)

    def test_snapshot_json_roundtrip_and_deterministic_continuation(self):
        original = self.runtime()
        for tick in range(10):
            original.step(
                c_observation(tick, posture_error_milli=tick * 20),
                a_observation(tick, energy_milli=500 + tick * 10),
                tick=tick,
            )
        encoded = snapshot_json(original)
        self.assertEqual(encoded, snapshot_json(original))
        restored = MikoshiCoordinationRuntime.restore(
            replace(cc.default_profile(enabled=True), cadence_ticks=2),
            replace(ah.default_profile(enabled=True), cadence_ticks=5),
            json.loads(encoded),
        )
        direct_results = []
        restored_results = []
        for tick in range(10, 30):
            c_sample = c_observation(tick, posture_error_milli=(tick % 9) * 30)
            a_sample = a_observation(tick, energy_milli=400 + tick % 300)
            direct_results.append(
                original.step(c_sample, a_sample, tick=tick)
            )
            restored_results.append(
                restored.step(c_sample, a_sample, tick=tick)
            )
        self.assertEqual(direct_results, restored_results)
        self.assertEqual(original.snapshot(), restored.snapshot())

    def test_snapshot_rejects_profile_mismatch_clock_mismatch_and_unknown_keys(self):
        runtime = self.runtime()
        runtime.step(c_observation(0), a_observation(0), tick=0)
        payload = runtime.snapshot()
        with self.assertRaises(cc.CoordinationError):
            MikoshiCoordinationRuntime.restore(
                replace(cc.default_profile(enabled=True), cadence_ticks=3),
                replace(ah.default_profile(enabled=True), cadence_ticks=5),
                payload,
            )
        bad = json.loads(json.dumps(payload))
        bad["coordination"]["state"]["next_tick"] = 2
        with self.assertRaises(RuntimeError):
            MikoshiCoordinationRuntime.restore(
                replace(cc.default_profile(enabled=True), cadence_ticks=2),
                replace(ah.default_profile(enabled=True), cadence_ticks=5),
                bad,
            )
        bad = runtime.snapshot()
        bad["unexpected"] = True
        with self.assertRaises(RuntimeError):
            MikoshiCoordinationRuntime.restore(
                replace(cc.default_profile(enabled=True), cadence_ticks=2),
                replace(ah.default_profile(enabled=True), cadence_ticks=5),
                bad,
            )

    def test_restore_can_enforce_monotonic_live_tick(self):
        runtime = self.runtime()
        payload = runtime.snapshot()
        with self.assertRaises(RuntimeError):
            MikoshiCoordinationRuntime.restore(
                replace(cc.default_profile(enabled=True), cadence_ticks=2),
                replace(ah.default_profile(enabled=True), cadence_ticks=5),
                payload,
                minimum_next_tick=1,
            )

    def test_default_runtime_is_inert_and_opt_in(self):
        runtime = MikoshiCoordinationRuntime(revision=REVISION)
        result = runtime.step(c_observation(0), a_observation(0), tick=0)
        self.assertEqual(result.coordination.status, "disabled")
        self.assertEqual(result.autonomic.status, "disabled")
        self.assertEqual(result.coordination.correction_milli, 0)
        self.assertEqual(result.autonomic.arousal_milli, 0)
        self.assertEqual(result.autonomic.rest_drive_milli, 1000)

    def test_native_revision_and_clock_types_are_strict(self):
        with self.assertRaises(RuntimeError):
            MikoshiCoordinationRuntime(revision=True)
        with self.assertRaises(RuntimeError):
            MikoshiCoordinationRuntime(start_tick=True)


if __name__ == "__main__":
    unittest.main()
