import copy
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from mojum import autonomic_homeostasis as ah
from mojum import cerebellar_coordination as cc


REVISION = 7


def coordination_profile(**changes):
    return replace(cc.default_profile(enabled=True), **changes)


def coordination_observation(tick, **changes):
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


def autonomic_profile(**changes):
    return replace(ah.default_profile(enabled=True), **changes)


def autonomic_observation(tick, **changes):
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


class CoordinationTests(unittest.TestCase):
    def test_default_profile_is_opt_in_and_fail_closed(self):
        profile = cc.default_profile()
        state = cc.initial_state(revision=REVISION)
        state, result = cc.step(
            profile, state, coordination_observation(0), tick=0
        )
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.correction_milli, 0)
        self.assertEqual((state.update_count, state.hold_count), (0, 1))

    def test_weighted_correction_is_bounded_and_rate_limited(self):
        profile = coordination_profile()
        state = cc.initial_state(revision=REVISION)
        state, first = cc.step(
            profile,
            state,
            coordination_observation(0, posture_error_milli=1000),
            tick=0,
        )
        self.assertEqual((first.status, first.correction_milli), ("updated", -50))
        state, held = cc.step(
            profile,
            state,
            coordination_observation(1, posture_error_milli=1000),
            tick=1,
        )
        self.assertEqual((held.status, held.correction_milli), ("cadence", -50))
        state, second = cc.step(
            profile,
            state,
            coordination_observation(2, posture_error_milli=1000),
            tick=2,
        )
        self.assertEqual(second.correction_milli, -100)
        self.assertEqual((state.update_count, state.hold_count), (2, 1))

    def test_correction_sign_opposes_the_synthetic_error(self):
        state, result = cc.step(
            coordination_profile(),
            cc.initial_state(revision=REVISION),
            coordination_observation(0, posture_error_milli=-1000),
            tick=0,
        )
        self.assertEqual(result.correction_milli, 50)
        self.assertEqual(state.correction_milli, 50)

    def test_deadband_emits_zero_but_records_the_update(self):
        state, result = cc.step(
            coordination_profile(),
            cc.initial_state(revision=REVISION),
            coordination_observation(0, posture_error_milli=1),
            tick=0,
        )
        self.assertEqual((result.status, result.correction_milli), ("deadband", 0))
        self.assertTrue(result.ran)
        self.assertEqual(state.update_count, 1)

    def test_fault_forces_zero_without_waiting_for_cadence(self):
        profile = coordination_profile()
        state, _ = cc.step(
            profile,
            cc.initial_state(revision=REVISION),
            coordination_observation(0, posture_error_milli=1000),
            tick=0,
        )
        state, result = cc.step(
            profile, state, coordination_observation(1, fault=True), tick=1
        )
        self.assertEqual((result.status, result.correction_milli), ("fault", 0))
        self.assertEqual(state.fault_count, 1)

    def test_stale_input_forces_zero_without_waiting_for_cadence(self):
        profile = coordination_profile(stale_after_ticks=0)
        state, _ = cc.step(
            profile,
            cc.initial_state(revision=REVISION),
            coordination_observation(0, posture_error_milli=1000),
            tick=0,
        )
        state, result = cc.step(
            profile, state, coordination_observation(0), tick=1
        )
        self.assertEqual((result.status, result.correction_milli), ("stale", 0))
        self.assertEqual(state.stale_count, 1)

    def test_future_revision_gap_and_tick_regression_fail_closed(self):
        profile = coordination_profile()
        state = cc.initial_state(revision=REVISION, start_tick=5)
        for observation, tick in (
            (coordination_observation(6), 5),
            (coordination_observation(5, revision=REVISION - 1), 5),
        ):
            with self.subTest(observation=observation), self.assertRaises(
                cc.CoordinationError
            ):
                cc.step(profile, state, observation, tick=tick)
        with self.assertRaises(cc.CoordinationError):
            cc.step(profile, state, coordination_observation(5), tick=6)
        state, _ = cc.step(profile, state, coordination_observation(5), tick=5)
        with self.assertRaises(cc.CoordinationError):
            cc.step(profile, state, coordination_observation(4), tick=6)

    def test_invalid_transition_does_not_mutate_state(self):
        state = cc.initial_state(revision=REVISION)
        before = state
        with self.assertRaises(cc.CoordinationError):
            cc.step(
                coordination_profile(),
                state,
                coordination_observation(0),
                tick=1,
            )
        self.assertEqual(state, before)

    def test_snapshot_roundtrip_profile_pin_and_live_floor(self):
        profile = coordination_profile()
        state, _ = cc.step(
            profile,
            cc.initial_state(revision=REVISION),
            coordination_observation(0),
            tick=0,
        )
        payload = cc.snapshot(profile, state)
        roundtrip = json.loads(json.dumps(payload))
        self.assertEqual(cc.restore(profile, roundtrip), state)
        with self.assertRaises(cc.CoordinationError):
            cc.restore(replace(profile, cadence_ticks=3), roundtrip)
        with self.assertRaises(cc.CoordinationError):
            cc.restore(profile, roundtrip, minimum_next_tick=2)
        roundtrip["state"]["correction_milli"] = profile.max_correction_milli + 1
        with self.assertRaises(cc.CoordinationError):
            cc.restore(profile, roundtrip)

    def test_snapshot_is_detached_and_rejects_unknown_or_hostile_keys(self):
        profile = coordination_profile()
        state = cc.initial_state(revision=REVISION)
        payload = cc.snapshot(profile, state)
        payload["state"]["next_tick"] = 99
        self.assertEqual(state.next_tick, 0)
        bad = cc.snapshot(profile, state)
        bad["extra"] = 1
        with self.assertRaises(cc.CoordinationError):
            cc.restore(profile, bad)

        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("hostile equality must not execute")

        bad = cc.snapshot(profile, state)
        bad[HostileKey("schema")] = bad.pop("schema")
        with self.assertRaises(cc.CoordinationError):
            cc.restore(profile, bad)

    def test_state_outside_profile_bound_cannot_be_snapshotted(self):
        profile = coordination_profile(max_correction_milli=100)
        state = replace(
            cc.initial_state(revision=REVISION), correction_milli=101
        )
        with self.assertRaisesRegex(cc.CoordinationError, "profile bound"):
            cc.snapshot(profile, state)

    def test_strict_native_types_profiles_and_exact_contract_classes(self):
        for change in (
            {"enabled": 1},
            {"cadence_ticks": 0},
            {"posture_gain_milli": 0, "velocity_gain_milli": 0, "support_gain_milli": 0},
            {"max_delta_milli": 501},
        ):
            with self.subTest(change=change), self.assertRaises(cc.CoordinationError):
                replace(cc.default_profile(), **change)
        with self.assertRaises(cc.CoordinationError):
            coordination_observation(0, posture_error_milli=True)
        with self.assertRaises(cc.CoordinationError):
            cc.step(
                coordination_profile(),
                cc.initial_state(revision=REVISION),
                {},
                tick=0,
            )

    def test_update_counter_overflow_is_rejected(self):
        state = replace(
            cc.initial_state(revision=REVISION), update_count=cc.MAX_COUNTER
        )
        with self.assertRaisesRegex(cc.CoordinationError, "overflow"):
            cc.step(
                coordination_profile(),
                state,
                coordination_observation(0),
                tick=0,
            )


class AutonomicTests(unittest.TestCase):
    def test_default_profile_is_opt_in_and_fail_closed(self):
        state, result = ah.step(
            ah.default_profile(),
            ah.initial_state(revision=REVISION),
            autonomic_observation(0),
            tick=0,
        )
        self.assertEqual(result.status, "disabled")
        self.assertEqual(
            (result.arousal_milli, result.rest_drive_milli, result.rhythm_milli),
            (0, 1000, 0),
        )
        self.assertEqual((state.update_count, state.hold_count), (0, 1))

    def test_integer_triangular_rhythm_is_repeatable(self):
        profile = autonomic_profile(
            cadence_ticks=1,
            rhythm_period_ticks=4,
            rhythm_amplitude_milli=100,
        )
        self.assertEqual(
            [ah.rhythm_signal(profile, tick) for tick in range(4)],
            [-100, 0, 100, 0],
        )
        self.assertEqual(ah.rhythm_signal(profile, 4), -100)

    def test_disabled_profile_suppresses_public_rhythm_helper(self):
        profile = ah.default_profile()
        self.assertFalse(profile.enabled)
        self.assertEqual(
            [ah.rhythm_signal(profile, tick) for tick in range(100)],
            [0] * 100,
        )

    def test_arousal_and_rest_feedback_are_bounded(self):
        profile = autonomic_profile(
            cadence_ticks=1,
            rhythm_amplitude_milli=0,
            max_delta_milli=1000,
        )
        _, ready = ah.step(
            profile,
            ah.initial_state(revision=REVISION),
            autonomic_observation(0, energy_milli=1000, load_milli=0),
            tick=0,
        )
        self.assertEqual((ready.arousal_milli, ready.rest_drive_milli), (700, 300))
        _, rest = ah.step(
            profile,
            ah.initial_state(revision=REVISION),
            autonomic_observation(
                0,
                energy_milli=0,
                load_milli=1000,
                thermal_deviation_milli=1000,
            ),
            tick=0,
        )
        self.assertEqual((rest.arousal_milli, rest.rest_drive_milli), (0, 1000))

    def test_feedback_rate_limit_prevents_single_tick_jump(self):
        state, result = ah.step(
            autonomic_profile(),
            ah.initial_state(revision=REVISION),
            autonomic_observation(0, energy_milli=1000, load_milli=0),
            tick=0,
        )
        self.assertEqual((result.arousal_milli, result.rest_drive_milli), (40, 960))
        self.assertEqual((state.arousal_milli, state.rest_drive_milli), (40, 960))

    def test_zeroed_feedback_gains_remove_input_influence(self):
        profile = autonomic_profile(
            cadence_ticks=1,
            rhythm_period_ticks=4,
            rhythm_amplitude_milli=100,
            energy_gain_milli=0,
            load_gain_milli=0,
            thermal_gain_milli=0,
            max_delta_milli=1000,
        )
        outputs = []
        for observation in (
            autonomic_observation(
                1,
                energy_milli=0,
                load_milli=1000,
                thermal_deviation_milli=10_000,
            ),
            autonomic_observation(
                1,
                energy_milli=1000,
                load_milli=0,
                thermal_deviation_milli=0,
            ),
        ):
            _, result = ah.step(
                profile,
                ah.initial_state(revision=REVISION, start_tick=1),
                observation,
                tick=1,
            )
            outputs.append((result.arousal_milli, result.rest_drive_milli))
        self.assertEqual(outputs, [(500, 500), (500, 500)])

    def test_cadence_holds_last_advisory_values(self):
        profile = autonomic_profile()
        state, first = ah.step(
            profile,
            ah.initial_state(revision=REVISION),
            autonomic_observation(0),
            tick=0,
        )
        for tick in range(1, 5):
            state, result = ah.step(
                profile, state, autonomic_observation(tick), tick=tick
            )
            self.assertEqual(result.status, "cadence")
            self.assertEqual(result.arousal_milli, first.arousal_milli)
        state, result = ah.step(
            profile, state, autonomic_observation(5), tick=5
        )
        self.assertEqual(result.status, "updated")
        self.assertEqual((state.update_count, state.hold_count), (2, 4))

    def test_fault_and_stale_samples_force_rest_immediately(self):
        profile = autonomic_profile(stale_after_ticks=0)
        state, _ = ah.step(
            profile,
            ah.initial_state(revision=REVISION),
            autonomic_observation(0),
            tick=0,
        )
        state, stale = ah.step(
            profile, state, autonomic_observation(0), tick=1
        )
        self.assertEqual(
            (stale.status, stale.arousal_milli, stale.rest_drive_milli),
            ("stale", 0, 1000),
        )
        state, fault = ah.step(
            profile, state, autonomic_observation(2, fault=True), tick=2
        )
        self.assertEqual(
            (fault.status, fault.arousal_milli, fault.rest_drive_milli),
            ("fault", 0, 1000),
        )
        self.assertEqual((state.stale_count, state.fault_count), (1, 1))

    def test_future_revision_gap_and_observation_regression_fail_closed(self):
        profile = autonomic_profile()
        state = ah.initial_state(revision=REVISION, start_tick=5)
        for observation, tick in (
            (autonomic_observation(6), 5),
            (autonomic_observation(5, revision=REVISION + 1), 5),
        ):
            with self.subTest(observation=observation), self.assertRaises(
                ah.HomeostasisError
            ):
                ah.step(profile, state, observation, tick=tick)
        with self.assertRaises(ah.HomeostasisError):
            ah.step(profile, state, autonomic_observation(5), tick=6)
        state, _ = ah.step(profile, state, autonomic_observation(5), tick=5)
        with self.assertRaises(ah.HomeostasisError):
            ah.step(profile, state, autonomic_observation(4), tick=6)

    def test_snapshot_roundtrip_profile_pin_and_live_floor(self):
        profile = autonomic_profile()
        state, _ = ah.step(
            profile,
            ah.initial_state(revision=REVISION),
            autonomic_observation(0),
            tick=0,
        )
        payload = ah.snapshot(profile, state)
        roundtrip = json.loads(json.dumps(payload))
        self.assertEqual(ah.restore(profile, roundtrip), state)
        with self.assertRaises(ah.HomeostasisError):
            ah.restore(replace(profile, cadence_ticks=3), roundtrip)
        with self.assertRaises(ah.HomeostasisError):
            ah.restore(profile, roundtrip, minimum_next_tick=2)

    def test_snapshot_is_detached_and_rejects_unknown_or_hostile_keys(self):
        profile = autonomic_profile()
        state = ah.initial_state(revision=REVISION)
        payload = ah.snapshot(profile, state)
        payload["state"]["next_tick"] = 99
        self.assertEqual(state.next_tick, 0)
        bad = ah.snapshot(profile, state)
        bad["extra"] = 1
        with self.assertRaises(ah.HomeostasisError):
            ah.restore(profile, bad)

        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("hostile equality must not execute")

        bad = ah.snapshot(profile, state)
        bad[HostileKey("schema")] = bad.pop("schema")
        with self.assertRaises(ah.HomeostasisError):
            ah.restore(profile, bad)

    def test_state_rhythm_outside_profile_bound_fails_closed(self):
        profile = autonomic_profile(rhythm_amplitude_milli=10)
        state = replace(ah.initial_state(revision=REVISION), rhythm_milli=11)
        with self.assertRaisesRegex(ah.HomeostasisError, "profile bound"):
            ah.step(profile, state, autonomic_observation(0), tick=0)
        with self.assertRaisesRegex(ah.HomeostasisError, "profile bound"):
            ah.snapshot(profile, state)

        payload = ah.snapshot(profile, ah.initial_state(revision=REVISION))
        payload["state"]["rhythm_milli"] = 11
        with self.assertRaisesRegex(ah.HomeostasisError, "profile bound"):
            ah.restore(profile, payload)

    def test_strict_native_types_profiles_and_exact_contract_classes(self):
        for change in (
            {"enabled": 1},
            {"cadence_ticks": 0},
            {
                "rhythm_amplitude_milli": 0,
                "energy_gain_milli": 0,
                "load_gain_milli": 0,
                "thermal_gain_milli": 0,
            },
            {"max_delta_milli": 1001},
        ):
            with self.subTest(change=change), self.assertRaises(ah.HomeostasisError):
                replace(ah.default_profile(), **change)
        with self.assertRaises(ah.HomeostasisError):
            autonomic_observation(0, energy_milli=True)
        with self.assertRaises(ah.HomeostasisError):
            ah.step(
                autonomic_profile(),
                ah.initial_state(revision=REVISION),
                {},
                tick=0,
            )

    def test_update_counter_overflow_is_rejected(self):
        state = replace(ah.initial_state(revision=REVISION), update_count=ah.MAX_COUNTER)
        with self.assertRaisesRegex(ah.HomeostasisError, "overflow"):
            ah.step(
                autonomic_profile(),
                state,
                autonomic_observation(0),
                tick=0,
            )


class CombinedContractTests(unittest.TestCase):
    def test_independent_cadences(self):
        c_profile = coordination_profile(cadence_ticks=2)
        a_profile = autonomic_profile(cadence_ticks=5)
        c_state = cc.initial_state(revision=REVISION)
        a_state = ah.initial_state(revision=REVISION)
        for tick in range(21):
            c_state, _ = cc.step(
                c_profile, c_state, coordination_observation(tick), tick=tick
            )
            a_state, _ = ah.step(
                a_profile, a_state, autonomic_observation(tick), tick=tick
            )
        self.assertEqual((c_state.update_count, c_state.hold_count), (11, 10))
        self.assertEqual((a_state.update_count, a_state.hold_count), (5, 16))

    def test_snapshot_continuation_is_deterministic(self):
        c_profile = coordination_profile()
        a_profile = autonomic_profile()
        c_state = cc.initial_state(revision=REVISION)
        a_state = ah.initial_state(revision=REVISION)
        for tick in range(10):
            c_state, _ = cc.step(
                c_profile, c_state, coordination_observation(tick), tick=tick
            )
            a_state, _ = ah.step(
                a_profile, a_state, autonomic_observation(tick), tick=tick
            )
        c_payload = json.loads(json.dumps(cc.snapshot(c_profile, c_state)))
        a_payload = json.loads(json.dumps(ah.snapshot(a_profile, a_state)))

        def continue_from(c_start, a_start):
            outputs = []
            for tick in range(10, 30):
                c_start, c_result = cc.step(
                    c_profile,
                    c_start,
                    coordination_observation(
                        tick,
                        posture_error_milli=(tick % 7 - 3) * 100,
                    ),
                    tick=tick,
                )
                a_start, a_result = ah.step(
                    a_profile,
                    a_start,
                    autonomic_observation(
                        tick,
                        energy_milli=500 + tick % 100,
                        load_milli=300 + tick % 200,
                    ),
                    tick=tick,
                )
                outputs.append((c_result, a_result))
            return c_start, a_start, outputs

        direct = continue_from(c_state, a_state)
        replay = continue_from(
            cc.restore(c_profile, copy.deepcopy(c_payload)),
            ah.restore(a_profile, copy.deepcopy(a_payload)),
        )
        self.assertEqual(direct, replay)

    def test_faults_are_isolated_between_interfaces(self):
        c_state, c_result = cc.step(
            coordination_profile(),
            cc.initial_state(revision=REVISION),
            coordination_observation(0, fault=True),
            tick=0,
        )
        a_state, a_result = ah.step(
            autonomic_profile(),
            ah.initial_state(revision=REVISION),
            autonomic_observation(0),
            tick=0,
        )
        self.assertEqual((c_result.status, c_state.fault_count), ("fault", 1))
        self.assertEqual((a_result.status, a_state.fault_count), ("updated", 0))

    def test_bounded_cpu_stress_is_deterministic(self):
        c_profile = coordination_profile()
        a_profile = autonomic_profile()

        def run():
            c_state = cc.initial_state(revision=REVISION)
            a_state = ah.initial_state(revision=REVISION)
            for tick in range(10_000):
                c_state, _ = cc.step(
                    c_profile,
                    c_state,
                    coordination_observation(
                        tick,
                        posture_error_milli=(tick % 2001) - 1000,
                        velocity_error_milli=(tick % 401) - 200,
                    ),
                    tick=tick,
                )
                a_state, _ = ah.step(
                    a_profile,
                    a_state,
                    autonomic_observation(
                        tick,
                        energy_milli=tick % 1001,
                        load_milli=(tick * 3) % 1001,
                        thermal_deviation_milli=(tick % 2001) - 1000,
                    ),
                    tick=tick,
                )
            return c_state, a_state

        first = run()
        second = run()
        self.assertEqual(first, second)
        self.assertEqual((first[0].update_count, first[1].update_count), (5000, 2000))
        self.assertLessEqual(abs(first[0].correction_milli), 500)
        self.assertTrue(0 <= first[1].arousal_milli <= 1000)
        self.assertTrue(0 <= first[1].rest_drive_milli <= 1000)

    def test_contract_objects_are_frozen_and_have_no_actuation_surface(self):
        objects = (
            coordination_profile(),
            cc.initial_state(revision=REVISION),
            autonomic_profile(),
            ah.initial_state(revision=REVISION),
        )
        for value in objects:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    value.schema = "mutated"
                for method in (
                    "actuate",
                    "set_torque",
                    "set_heart_rate",
                    "write_journal",
                    "open_device",
                ):
                    self.assertFalse(hasattr(value, method))

    def test_profile_digests_are_stable_and_interface_specific(self):
        c_profile = coordination_profile()
        a_profile = autonomic_profile()
        self.assertEqual(cc.profile_digest(c_profile), cc.profile_digest(c_profile))
        self.assertEqual(ah.profile_digest(a_profile), ah.profile_digest(a_profile))
        self.assertNotEqual(cc.profile_digest(c_profile), ah.profile_digest(a_profile))


if __name__ == "__main__":
    unittest.main()
