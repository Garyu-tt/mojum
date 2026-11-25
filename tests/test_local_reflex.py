"""Behavioral regression for the optional, synthetic local reflex controller.

The observations below are explicit fixture measurements.  These tests do not
claim articulated-body physics or actual force control.
"""

import copy
from dataclasses import replace
import hashlib
import json
import unittest
from unittest import mock

from mojum.local_reflex import JointReflex, LocalReflexController
from mojum.motor_gate import MotorGate


BODY = "reflex-fixture"
REVISION = 3
PERIOD_NS = 10_000_000


def profiles():
    return (
        JointReflex("left", partner="right", withdrawal_direction=1),
        JointReflex("right", partner="left", withdrawal_direction=-1),
    )


def controller(*, gate=None, joints=None, **kwargs):
    if gate is None:
        gate = MotorGate({"left": (-1.0, 1.0), "right": (-1.0, 1.0)}, REVISION)
    return LocalReflexController(
        gate, body_id=BODY, joints=profiles() if joints is None else joints, **kwargs
    )


def observation(tick=0, *, time_ns=None):
    if time_ns is None:
        time_ns = tick * PERIOD_NS
    return {
        "schema": "mojum.local-reflex.observation/1",
        "body_id": BODY,
        "revision": REVISION,
        "physics": {
            "schema": "mojum.virtual-physics-observation/1",
            "tick": tick,
            "time_ns": time_ns,
            "backend": "fixture",
            "synthetic": True,
            "physical_contact": False,
            "physical_actuation": False,
            "joints": [
                {"id": name, "position_rad": 0.0, "velocity_rad_s": 0.0}
                for name in ("left", "right")
            ],
            "virtual_contacts": [],
        },
        "proprioception": [
            {
                "joint": name,
                "torque_nm": 0.0,
                "load_n": 0.0,
                "contact": False,
                "support": False,
                "hazard": False,
            }
            for name in ("left", "right")
        ],
    }


def set_joint(packet, name, **values):
    for record in packet["physics"]["joints"]:
        if record["id"] == name:
            record.update({key: value for key, value in values.items()
                           if key in ("position_rad", "velocity_rad_s")})
    for record in packet["proprioception"]:
        if record["joint"] == name:
            record.update({key: value for key, value in values.items()
                           if key not in ("position_rad", "velocity_rad_s")})
    return packet


def intention(*, sequence=0, issued_at_ns=0, expires_at_ns=30_000_000,
              targets=None, revision=REVISION):
    return {
        "schema": "mojum.local-reflex.intent/1",
        "body_id": BODY,
        "revision": revision,
        "sequence": sequence,
        "issued_at_ns": issued_at_ns,
        "expires_at_ns": expires_at_ns,
        "targets": {"left": 0.25} if targets is None else targets,
    }


def commands(frame):
    return {item["joint"]: item for item in frame["commands"]}


def restore(gate, snapshot, *, expected_sha256=None, joints=None, **kwargs):
    return LocalReflexController.from_snapshot(
        gate, body_id=BODY, joints=profiles() if joints is None else joints,
        snapshot=snapshot,
        expected_sha256=snapshot["sha256"] if expected_sha256 is None else expected_sha256,
        **kwargs,
    )


def rehash(snapshot):
    """Tests can make a consistent fixture digest; this does not confer trust."""
    payload = {key: value for key, value in snapshot.items() if key != "sha256"}
    snapshot["sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")).hexdigest()
    return snapshot


class LocalReflexInputTests(unittest.TestCase):
    def assert_rejected_without_state_change(self, ctl, packet, intent=None,
                                             *, evaluated_at_ns=0):
        before = ctl.snapshot()
        with self.assertRaises((ValueError, TypeError)):
            ctl.step(packet, intent, evaluated_at_ns=evaluated_at_ns)
        self.assertEqual(ctl.snapshot(), before)

    def test_rejects_unknown_or_missing_top_level_fields(self):
        for where in (None, "physics", "proprioception"):
            for operation in ("extra", "missing"):
                with self.subTest(where=where, operation=operation):
                    packet = observation()
                    obj = packet if where is None else packet[where]
                    if isinstance(obj, list):
                        obj = obj[0]
                    if operation == "extra":
                        obj["unrecognized"] = 1
                    else:
                        del obj[next(iter(obj))]
                    self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_non_native_mapping_and_sequence_types(self):
        class DictSubclass(dict):
            pass

        class ListSubclass(list):
            pass

        malformed = [None, [], "{}", DictSubclass(observation())]
        packet = observation()
        packet["proprioception"] = ListSubclass(packet["proprioception"])
        malformed.append(packet)
        packet = observation()
        packet["physics"]["joints"] = tuple(packet["physics"]["joints"])
        malformed.append(packet)
        for packet in malformed:
            with self.subTest(type=type(packet)):
                self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_foreign_identity_and_revision(self):
        for field, values in (
            ("body_id", ("other-body", "", True)),
            ("revision", (REVISION - 1, REVISION + 1, True)),
            ("schema", ("mojum.local-reflex.observation/2", 1)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    packet = observation()
                    packet[field] = value
                    self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_nonfinite_bool_and_unbounded_measurements(self):
        for field in ("position_rad", "velocity_rad_s", "torque_nm", "load_n"):
            for value in (float("nan"), float("inf"), -float("inf"), True, 10**1000):
                with self.subTest(field=field, value=str(value)[:20]):
                    packet = set_joint(observation(), "left", **{field: value})
                    self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_negative_load_and_non_bool_contact_signals(self):
        bad_values = (("load_n", -0.1), ("contact", 1), ("support", "yes"),
                      ("hazard", 0))
        for field, value in bad_values:
            with self.subTest(field=field):
                packet = set_joint(observation(), "left", **{field: value})
                self.assert_rejected_without_state_change(controller(), packet)

    def test_support_requires_measured_contact(self):
        packet = set_joint(observation(), "left", support=True, contact=False)
        self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_duplicate_missing_and_unknown_joint_measurements(self):
        for section in ("physics", "proprioception"):
            for operation in ("duplicate", "missing", "unknown"):
                packet = observation()
                records = packet["physics"]["joints"] if section == "physics" \
                    else packet["proprioception"]
                if operation == "duplicate":
                    records.append(copy.deepcopy(records[0]))
                elif operation == "missing":
                    records.pop()
                else:
                    records[0]["id" if section == "physics" else "joint"] = "foreign"
                with self.subTest(section=section, operation=operation):
                    self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_hardware_and_non_synthetic_observation_claims(self):
        for field, value in (("synthetic", False), ("physical_actuation", True),
                             ("physical_contact", True), ("backend", "real-robot")):
            packet = observation()
            packet["physics"][field] = value
            with self.subTest(field=field):
                self.assert_rejected_without_state_change(controller(), packet)

    def test_rejects_future_and_stale_measurements(self):
        self.assert_rejected_without_state_change(
            controller(), observation(time_ns=1), evaluated_at_ns=0
        )
        self.assert_rejected_without_state_change(
            controller(), observation(), evaluated_at_ns=50_000_001
        )
        # The maximum admitted observation age remains inclusive.
        controller().step(observation(), evaluated_at_ns=50_000_000)

    def test_rejects_malformed_simulation_times(self):
        for field in ("tick", "time_ns"):
            for value in (-1, True, 1.5, "0", 2**63):
                packet = observation()
                packet["physics"][field] = value
                with self.subTest(field=field, value=value):
                    self.assert_rejected_without_state_change(controller(), packet)
        for value in (-1, True, 1.5, "0", 2**63):
            self.assert_rejected_without_state_change(
                controller(), observation(), evaluated_at_ns=value
            )

    def test_tick_sequence_and_sample_time_do_not_advance_on_failure(self):
        ctl = controller()
        ctl.step(observation(), evaluated_at_ns=0)
        for packet in (observation(), observation(2), observation(1, time_ns=0)):
            self.assert_rejected_without_state_change(
                ctl, packet, evaluated_at_ns=20_000_000
            )
        ctl.step(observation(1), evaluated_at_ns=PERIOD_NS)

    def test_evaluation_time_cannot_move_backwards(self):
        ctl = controller()
        ctl.step(observation(), evaluated_at_ns=20_000_000)
        self.assert_rejected_without_state_change(
            ctl, observation(1), evaluated_at_ns=15_000_000
        )
        ctl.step(observation(1), evaluated_at_ns=20_000_000)

    def test_out_of_bounds_measured_position_is_not_clamped(self):
        for position in (-1.0001, 1.0001):
            packet = set_joint(observation(), "left", position_rad=position)
            self.assert_rejected_without_state_change(controller(), packet)

    def test_invalid_descending_target_is_not_clamped_into_acceptance(self):
        for target in (-1.0001, 1.0001, float("nan"), float("inf"), True, 10**1000):
            with self.subTest(target=str(target)[:20]):
                self.assert_rejected_without_state_change(
                    controller(), observation(), intention(targets={"left": target})
                )

    def test_intent_requires_known_joints_body_and_bounded_ttl(self):
        variants = []
        for field, value in (
            ("body_id", "someone-else"), ("schema", "unknown"),
            ("sequence", True), ("sequence", -1),
            ("issued_at_ns", True), ("expires_at_ns", True),
            ("expires_at_ns", 0), ("expires_at_ns", 500_000_001),
            ("targets", {"foreign": 0.0}),
        ):
            packet = intention()
            packet[field] = value
            variants.append((field, packet))
        for field, packet in variants:
            with self.subTest(field=field):
                self.assert_rejected_without_state_change(controller(), observation(), packet)

    def test_replayed_intent_sequence_cannot_replace_accepted_command(self):
        ctl = controller()
        ctl.step(observation(), intention(sequence=5), evaluated_at_ns=0)
        for tick, sequence in ((1, 4), (2, 5)):
            frame = ctl.step(
                observation(tick), intention(sequence=sequence, targets={"left": -0.25}),
                evaluated_at_ns=tick * PERIOD_NS,
            )
            self.assertEqual(frame["intent_status"], "replayed")
            self.assertGreater(commands(frame)["left"]["target_rad"], 0)

    def test_future_intent_is_rejected_without_advancing_state(self):
        self.assert_rejected_without_state_change(
            controller(), observation(), intention(issued_at_ns=1)
        )

    def test_virtual_contact_budget_preserves_regular_contact_admission(self):
        packet = observation()
        contact = {"geom_a": "foot", "geom_b": "floor", "normal_force_n": 1.0}
        packet["physics"]["virtual_contacts"] = [dict(contact) for _ in range(256)]
        controller().step(packet, evaluated_at_ns=0)
        packet["physics"]["virtual_contacts"].append(dict(contact))
        self.assert_rejected_without_state_change(controller(), packet)

    def test_native_field_names_are_required_before_key_comparison(self):
        class HostileName(str):
            def __eq__(self, other):
                raise AssertionError("untrusted field name was compared")

            __hash__ = str.__hash__

        for section in (None, "physics", "proprioception"):
            packet = observation()
            obj = packet if section is None else packet[section]
            if isinstance(obj, list):
                obj = obj[0]
            key = next(iter(obj))
            value = obj.pop(key)
            obj[HostileName(key)] = value
            with self.subTest(section=section):
                self.assert_rejected_without_state_change(controller(), packet)

    def test_gate_revision_change_invalidates_controller(self):
        gate = MotorGate({"left": (-1.0, 1.0), "right": (-1.0, 1.0)}, REVISION)
        ctl = controller(gate=gate)
        gate.restore_revision(REVISION + 1)
        with self.assertRaises(ValueError):
            ctl.step(observation(), evaluated_at_ns=0)


class LocalReflexProfileTests(unittest.TestCase):
    def test_invalid_joint_config_does_not_create_controller(self):
        for field, value in (
            ("joint", "unknown"), ("withdrawal_direction", 0),
            ("withdrawal_direction", True), ("max_step_rad", 0),
            ("max_step_rad", float("nan")), ("withdrawal_step_rad", 0.06),
            ("neutral_rad", 1.1), ("stretch_band_rad", -1),
            ("velocity_threshold_rad_s", 0), ("damping_s", -1),
            ("torque_limit_nm", 0), ("load_limit_n", True),
        ):
            with self.subTest(field=field):
                with self.assertRaises((TypeError, ValueError)):
                    controller(joints=(replace(profiles()[0], **{field: value}), profiles()[1]))

    def test_partner_relationship_is_explicit_and_symmetric(self):
        for joints in (
            (replace(profiles()[0], partner="left"), profiles()[1]),
            (replace(profiles()[0], partner="missing"), profiles()[1]),
            (profiles()[0], replace(profiles()[1], partner=None)),
        ):
            with self.assertRaises((TypeError, ValueError)):
                controller(joints=joints)

    def test_missing_duplicate_and_over_budget_profiles_rejected(self):
        for joints in ((), (profiles()[0], profiles()[0]), profiles() * 33):
            with self.assertRaises((TypeError, ValueError)):
                controller(joints=joints)

    def test_forged_dataclass_still_requires_primitive_validation(self):
        original = profiles()[0]
        object.__setattr__(original, "withdrawal_direction", True)
        with self.assertRaises((TypeError, ValueError)):
            controller(joints=(original, profiles()[1]))
        forged = object.__new__(JointReflex)
        with self.assertRaises((TypeError, ValueError)):
            controller(joints=(forged, profiles()[1]))

    def test_invalid_controller_time_budgets_rejected(self):
        for name in ("max_observation_age_ns", "max_intent_ttl_ns"):
            for value in (0, -1, True, 0.1, 2**63):
                with self.subTest(name=name, value=value):
                    with self.assertRaises((TypeError, ValueError)):
                        controller(**{name: value})


class LocalReflexBehaviorTests(unittest.TestCase):
    def test_no_cognition_holds_measured_pose_and_declares_synthetic_output(self):
        packet = set_joint(observation(), "left", position_rad=0.2)
        frame = controller().step(packet, evaluated_at_ns=0)
        self.assertEqual(frame["schema"], "mojum.local-reflex.frame/1")
        self.assertEqual(frame["visibility"], "PUBLIC_SYNTHETIC")
        self.assertIs(frame["physical_actuation"], False)
        self.assertEqual(frame["intent_status"], "absent")
        left = commands(frame)["left"]
        self.assertEqual(left["source"], "hold")
        self.assertEqual(left["target_rad"], 0.2)
        self.assertEqual((left["flexor"], left["extensor"]), (0.0, 0.0))

    def test_local_withdrawal_operates_without_any_cognitive_refresh(self):
        ctl = controller()
        for tick in range(6):
            packet = observation(tick)
            set_joint(packet, "left", hazard=True)
            set_joint(packet, "right", hazard=True)
            selected = commands(ctl.step(packet, evaluated_at_ns=tick * PERIOD_NS))
            self.assertEqual(selected["left"]["source"], "withdrawal")
            self.assertEqual(selected["right"]["source"], "withdrawal")
            self.assertAlmostEqual(selected["left"]["target_rad"], 0.05)
            self.assertAlmostEqual(selected["right"]["target_rad"], -0.05)

    def test_withdrawal_overrides_conflicting_cognition_and_stretch(self):
        packet = set_joint(observation(), "left", position_rad=0.8, hazard=True)
        selected = commands(controller().step(
            packet, intention(targets={"left": -0.8}), evaluated_at_ns=0
        ))
        self.assertEqual(selected["left"]["source"], "withdrawal")
        self.assertAlmostEqual(selected["left"]["target_rad"], 0.85)

    def test_cognitive_intent_survives_stall_but_expires_at_exact_boundary(self):
        ctl = controller()
        first = ctl.step(observation(), intention(), evaluated_at_ns=0)
        self.assertEqual(first["intent_status"], "accepted")
        for tick in (1, 2):
            frame = ctl.step(observation(tick), evaluated_at_ns=tick * PERIOD_NS)
            self.assertEqual(frame["intent_status"], "retained")
            self.assertEqual(commands(frame)["left"]["source"], "cognitive")
            self.assertGreater(commands(frame)["left"]["target_rad"], 0)
        expired = ctl.step(observation(3), evaluated_at_ns=30_000_000)
        self.assertEqual(expired["intent_status"], "expired")
        self.assertEqual(commands(expired)["left"]["source"], "hold")
        self.assertEqual(commands(expired)["left"]["target_rad"], 0)
        self.assertEqual(ctl.snapshot()["last_intent_sequence"], 0)

    def test_expired_stale_and_replayed_intents_do_not_block_local_response(self):
        for variant in ("expired", "stale_revision", "replayed"):
            ctl = controller()
            ctl.step(observation(), intention(sequence=5), evaluated_at_ns=0)
            packet = set_joint(observation(1), "left", hazard=True)
            if variant == "expired":
                intent = intention(sequence=6, expires_at_ns=PERIOD_NS)
            elif variant == "stale_revision":
                intent = intention(sequence=6, revision=REVISION - 1)
            else:
                intent = intention(sequence=5, targets={"left": -0.9})
            with self.subTest(variant=variant):
                frame = ctl.step(packet, intent, evaluated_at_ns=PERIOD_NS)
                self.assertEqual(frame["intent_status"], variant)
                self.assertEqual(commands(frame)["left"]["source"], "withdrawal")
                self.assertGreater(commands(frame)["left"]["target_rad"], 0)

    def test_cold_expired_intent_cannot_revive_after_clock_progress(self):
        ctl = controller()
        frame = ctl.step(
            observation(time_ns=30_000_000), intention(sequence=7),
            evaluated_at_ns=30_000_000,
        )
        self.assertEqual(frame["intent_status"], "expired")
        replay = intention(sequence=7, issued_at_ns=30_000_000, expires_at_ns=60_000_000)
        frame = ctl.step(observation(1, time_ns=40_000_000), replay,
                         evaluated_at_ns=40_000_000)
        self.assertEqual(frame["intent_status"], "replayed")
        self.assertEqual(commands(frame)["left"]["source"], "hold")

    def test_crossed_support_requires_contact_support_and_partner_hazard(self):
        for supported in (False, True):
            packet = set_joint(observation(), "left", hazard=True)
            set_joint(packet, "right", position_rad=0.2,
                      contact=supported, support=supported)
            selected = commands(controller().step(packet, evaluated_at_ns=0))
            right = selected["right"]
            with self.subTest(supported=supported):
                self.assertEqual(right["source"], "crossed_support" if supported else "hold")
                self.assertAlmostEqual(right["target_rad"], 0.15 if supported else 0.2)
        packet = set_joint(observation(), "right", position_rad=0.2,
                           contact=True, support=True)
        self.assertEqual(commands(controller().step(packet, evaluated_at_ns=0))["right"]["source"],
                         "hold")

    def test_own_hazard_takes_priority_over_crossed_support(self):
        packet = set_joint(observation(), "left", hazard=True)
        set_joint(packet, "right", position_rad=-0.2, contact=True, support=True, hazard=True)
        right = commands(controller().step(packet, evaluated_at_ns=0))["right"]
        self.assertEqual(right["source"], "withdrawal")
        self.assertAlmostEqual(right["target_rad"], -0.25)

    def test_torque_and_load_limit_override_all_competing_response_sources(self):
        for field, value in (("torque_nm", 10.0), ("torque_nm", -10.0), ("load_n", 100.0)):
            packet = set_joint(observation(), "left", position_rad=0.8, velocity_rad_s=2.0,
                               hazard=True, contact=True, support=True, **{field: value})
            set_joint(packet, "right", hazard=True)
            left = commands(controller().step(
                packet, intention(targets={"left": -0.8}), evaluated_at_ns=0
            ))["left"]
            with self.subTest(field=field, value=value):
                self.assertEqual(left["source"], "force_limit")
                self.assertEqual(left["target_rad"], 0.8)
                self.assertEqual((left["flexor"], left["extensor"]), (0.0, 0.0))

    def test_stretch_returns_toward_neutral_in_either_direction(self):
        for position in (-0.8, 0.8):
            packet = set_joint(observation(), "left", position_rad=position)
            left = commands(controller().step(packet, evaluated_at_ns=0))["left"]
            with self.subTest(position=position):
                self.assertEqual(left["source"], "stretch")
                self.assertAlmostEqual(abs(left["target_rad"]), 0.75)

    def test_perturbation_response_opposes_measured_velocity(self):
        for velocity in (-2.0, 2.0):
            packet = set_joint(observation(), "left", velocity_rad_s=velocity)
            left = commands(controller().step(packet, evaluated_at_ns=0))["left"]
            with self.subTest(velocity=velocity):
                self.assertEqual(left["source"], "perturbation")
                self.assertLess(left["target_rad"] * velocity, 0)
                self.assertAlmostEqual(abs(left["target_rad"]), 0.05)

    def test_exact_stretch_and_velocity_thresholds_do_not_trigger_response(self):
        packet = set_joint(observation(), "left", position_rad=0.4, velocity_rad_s=1.0)
        left = commands(controller().step(packet, evaluated_at_ns=0))["left"]
        self.assertEqual(left["source"], "hold")
        self.assertEqual(left["target_rad"], 0.4)

    def test_reciprocal_inhibition_never_drives_both_virtual_antagonists(self):
        for position in (-0.95, -0.1, 0.0, 0.1, 0.95):
            for target in (-1.0, -0.1, 0.0, 0.1, 1.0):
                packet = set_joint(observation(), "left", position_rad=position)
                left = commands(controller().step(
                    packet, intention(targets={"left": target}), evaluated_at_ns=0
                ))["left"]
                flexor, extensor = left["flexor"], left["extensor"]
                with self.subTest(position=position, target=target):
                    self.assertGreaterEqual(flexor, 0)
                    self.assertGreaterEqual(extensor, 0)
                    self.assertLessEqual(flexor + extensor, 1)
                    self.assertEqual(flexor * extensor, 0)
                    delta = left["target_rad"] - position
                    self.assertLessEqual(abs(delta), 0.05 + 1e-15)
                    if delta > 0:
                        self.assertGreater(flexor, 0)
                    elif delta < 0:
                        self.assertGreater(extensor, 0)
                    else:
                        self.assertEqual(flexor + extensor, 0)

    def test_local_withdrawal_saturates_at_existing_gate_limits(self):
        packet = set_joint(observation(), "left", position_rad=0.99, hazard=True)
        set_joint(packet, "right", position_rad=-0.99, hazard=True)
        chosen = commands(controller().step(packet, evaluated_at_ns=0))
        self.assertEqual(chosen["left"]["target_rad"], 1.0)
        self.assertEqual(chosen["right"]["target_rad"], -1.0)

    def test_every_returned_target_passes_the_actual_supplied_motor_gate(self):
        gate = MotorGate({"left": (-1.0, 1.0), "right": (-1.0, 1.0)}, REVISION)
        ctl = controller(gate=gate)
        admitted = []
        original = MotorGate.validate

        def record(gate_self, intent):
            target = original(gate_self, intent)
            self.assertIs(gate_self, gate)
            admitted.append((intent.joint, target, intent.revision))
            return target

        packet = set_joint(observation(), "left", hazard=True)
        with mock.patch.object(MotorGate, "validate", record):
            frame = ctl.step(packet, intention(targets={"right": 0.2}), evaluated_at_ns=0)
        self.assertEqual(admitted, [(c["joint"], c["target_rad"], c["revision"])
                                    for c in frame["commands"]])
        self.assertEqual(len(admitted), 2)

    def test_late_gate_rejection_returns_no_partial_frame_or_pending_state(self):
        ctl = controller()
        before = ctl.snapshot()
        original = MotorGate.validate

        def reject_second(gate_self, intent):
            if intent.joint == "right":
                raise PermissionError("injected gate rejection")
            return original(gate_self, intent)

        with mock.patch.object(MotorGate, "validate", reject_second):
            with self.assertRaises(PermissionError):
                ctl.step(observation(), intention(sequence=7), evaluated_at_ns=0)
        self.assertEqual(ctl.snapshot(), before)
        frame = ctl.step(observation(), intention(sequence=7), evaluated_at_ns=0)
        self.assertEqual(frame["intent_status"], "accepted")

    def test_estop_suppresses_all_commands_even_during_hazard(self):
        gate = MotorGate({"left": (-1.0, 1.0), "right": (-1.0, 1.0)}, REVISION)
        ctl = controller(gate=gate)
        gate.trip_estop()
        packet = set_joint(observation(), "left", hazard=True)
        frame = ctl.step(packet, intention(), evaluated_at_ns=0)
        self.assertTrue(frame["estop_latched"])
        self.assertEqual(frame["commands"], [])

    def test_input_order_does_not_change_deterministic_command_order(self):
        packet = set_joint(observation(), "left", hazard=True)
        reversed_packet = copy.deepcopy(packet)
        reversed_packet["physics"]["joints"].reverse()
        reversed_packet["proprioception"].reverse()
        normal = controller().step(packet, evaluated_at_ns=0)
        reversed_frame = controller(joints=tuple(reversed(profiles()))).step(
            reversed_packet, evaluated_at_ns=0
        )
        self.assertEqual(normal, reversed_frame)

    def test_caller_mutations_do_not_change_cached_intent_or_profile(self):
        settings = profiles()
        ctl = controller(joints=settings)
        intent = intention()
        ctl.step(observation(), intent, evaluated_at_ns=0)
        intent["targets"]["left"] = -0.9
        object.__setattr__(settings[0], "withdrawal_direction", -1)
        frame = ctl.step(observation(1), evaluated_at_ns=PERIOD_NS)
        self.assertGreater(commands(frame)["left"]["target_rad"], 0)
        hazard = set_joint(observation(2), "left", hazard=True)
        frame = ctl.step(hazard, evaluated_at_ns=2 * PERIOD_NS)
        self.assertGreater(commands(frame)["left"]["target_rad"], 0)


class LocalReflexSnapshotTests(unittest.TestCase):
    def gate(self):
        return MotorGate({"left": (-1.0, 1.0), "right": (-1.0, 1.0)}, REVISION)

    def test_checkpoint_roundtrip_preserves_cold_deterministic_continuation(self):
        uninterrupted = controller()
        uninterrupted.step(observation(), intention(), evaluated_at_ns=0)
        checkpoint = json.loads(json.dumps(uninterrupted.snapshot()))
        resumed = restore(self.gate(), checkpoint)
        self.assertEqual(resumed.snapshot(), checkpoint)
        for tick in range(1, 7):
            packet = observation(tick)
            if tick in (2, 4):
                set_joint(packet, "right", hazard=True)
            intent = intention(sequence=1, issued_at_ns=40_000_000,
                               expires_at_ns=80_000_000) if tick == 4 else None
            expected = uninterrupted.step(packet, intent, evaluated_at_ns=tick * PERIOD_NS)
            actual = resumed.step(copy.deepcopy(packet), copy.deepcopy(intent),
                                  evaluated_at_ns=tick * PERIOD_NS)
            self.assertEqual(actual, expected)
            self.assertEqual(resumed.snapshot(), uninterrupted.snapshot())

    def test_empty_snapshot_roundtrip(self):
        checkpoint = controller().snapshot()
        self.assertEqual(restore(self.gate(), checkpoint).snapshot(), checkpoint)

    def test_late_expired_packet_keeps_live_cached_intent_across_restore(self):
        ctl = controller()
        ctl.step(observation(time_ns=20),
                 intention(sequence=5, issued_at_ns=10, expires_at_ns=100),
                 evaluated_at_ns=20)
        late = intention(sequence=6, issued_at_ns=50, expires_at_ns=60,
                         targets={"left": -0.25})
        frame = ctl.step(observation(1, time_ns=70), late, evaluated_at_ns=70)
        self.assertEqual(frame["intent_status"], "expired")
        self.assertEqual(commands(frame)["left"]["source"], "cognitive")
        self.assertGreater(commands(frame)["left"]["target_rad"], 0)
        checkpoint = ctl.snapshot()
        self.assertEqual(checkpoint["last_intent_sequence"], 6)
        self.assertEqual(checkpoint["intent"]["sequence"], 5)
        resumed = restore(self.gate(), checkpoint)
        for tick, time_ns in ((2, 80), (3, 90), (4, 100)):
            replay = intention(sequence=6, issued_at_ns=70, expires_at_ns=150,
                               targets={"left": -0.25}) if tick == 3 else None
            expected = ctl.step(observation(tick, time_ns=time_ns), replay,
                                evaluated_at_ns=time_ns)
            actual = resumed.step(observation(tick, time_ns=time_ns), replay,
                                  evaluated_at_ns=time_ns)
            self.assertEqual(actual, expected)
            left = commands(actual)["left"]
            if time_ns < 100:
                self.assertEqual(left["source"], "cognitive")
                self.assertGreater(left["target_rad"], 0)
            else:
                self.assertEqual(left["source"], "hold")
                self.assertEqual(left["target_rad"], 0)

    def test_snapshot_and_returned_frames_are_detached(self):
        ctl = controller()
        frame = ctl.step(observation(), intention(), evaluated_at_ns=0)
        checkpoint = ctl.snapshot()
        original = copy.deepcopy(checkpoint)
        checkpoint["intent"]["targets"]["left"] = -1.0
        frame["commands"][0]["target_rad"] = -1.0
        self.assertEqual(ctl.snapshot(), original)
        self.assertGreater(commands(ctl.step(observation(1), evaluated_at_ns=PERIOD_NS))["left"]["target_rad"], 0)

    def test_restore_rejects_rehashed_checkpoint_when_external_pin_differs(self):
        ctl = controller()
        ctl.step(observation(), intention(), evaluated_at_ns=0)
        checkpoint = ctl.snapshot()
        expected_digest = checkpoint["sha256"]
        checkpoint["intent"]["targets"]["left"] = -0.8
        rehash(checkpoint)
        with self.assertRaises(ValueError):
            restore(self.gate(), checkpoint, expected_sha256=expected_digest)

    def test_malformed_snapshot_rejected_before_estop_side_effect(self):
        ctl = controller()
        ctl.step(observation(), intention(), evaluated_at_ns=0)
        for field, value in (
            ("next_tick", True), ("last_sample_ns", 2), ("last_evaluated_ns", -1),
            ("last_intent_sequence", -1), ("schema", "unknown"),
            ("body_id", "other"), ("revision", REVISION + 1),
        ):
            checkpoint = ctl.snapshot()
            checkpoint["estop_latched"] = True
            checkpoint[field] = value
            rehash(checkpoint)
            gate = self.gate()
            with self.subTest(field=field):
                with self.assertRaises((ValueError, TypeError)):
                    restore(gate, checkpoint)
                self.assertFalse(gate.estop_latched)

    def test_restore_requires_matching_profile_limits_and_age_budget(self):
        checkpoint = controller().snapshot()
        variants = (
            (self.gate(), {"joints": (replace(profiles()[0], neutral_rad=0.1), profiles()[1])}),
            (MotorGate({"left": (-2.0, 2.0), "right": (-1.0, 1.0)}, REVISION), {}),
            (self.gate(), {"max_observation_age_ns": 40_000_000}),
            (self.gate(), {"max_intent_ttl_ns": 400_000_000}),
        )
        for gate, options in variants:
            with self.assertRaises(ValueError):
                restore(gate, checkpoint, **options)

    def test_restore_preserves_both_checkpoint_and_current_gate_estop(self):
        running = controller()
        running.step(observation(), intention(), evaluated_at_ns=0)
        clear_snapshot = running.snapshot()
        latched_gate = self.gate()
        latched_gate.trip_estop()
        resumed = restore(latched_gate, clear_snapshot)
        self.assertTrue(latched_gate.estop_latched)
        self.assertEqual(resumed.step(observation(1), evaluated_at_ns=PERIOD_NS)["commands"], [])

        stopped_gate = self.gate()
        stopped = controller(gate=stopped_gate)
        stopped_gate.trip_estop()
        stopped.step(observation(), intention(), evaluated_at_ns=0)
        fresh_gate = self.gate()
        resumed = restore(fresh_gate, stopped.snapshot())
        self.assertTrue(fresh_gate.estop_latched)
        self.assertEqual(resumed.step(observation(1), evaluated_at_ns=PERIOD_NS)["commands"], [])

    def test_restored_intent_and_order_state_do_not_alias_checkpoint_input(self):
        ctl = controller()
        ctl.step(observation(), intention(), evaluated_at_ns=0)
        checkpoint = ctl.snapshot()
        resumed = restore(self.gate(), checkpoint)
        checkpoint["intent"]["targets"]["left"] = -0.8
        checkpoint["next_tick"] = 50
        frame = resumed.step(observation(1), evaluated_at_ns=PERIOD_NS)
        self.assertGreater(commands(frame)["left"]["target_rad"], 0)


if __name__ == "__main__":
    unittest.main()
