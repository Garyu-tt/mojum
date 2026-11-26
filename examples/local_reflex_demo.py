"""Run the opt-in local-reflex CPU fixture and report measured Python latency.

Every observation below is a scripted synthetic input. Targets are not applied
to a physical or simulated body; this is a controller fixture, not locomotion.
Run from the repository root with PYTHONPATH=src.
"""
from __future__ import annotations

import argparse
import json
import platform
import time

from mojum.local_reflex import (
    INTENT_SCHEMA, OBSERVATION_SCHEMA, JointReflex, LocalReflexController,
)
from mojum.motor_gate import MotorGate


BODY_ID = "local-reflex-demo"
REVISION = 7
STEP_NS = 10_000_000  # Deterministic fixture time, not a biological rate.


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def make_controller(count=2):
    names = tuple(f"joint{i:02d}" for i in range(count))
    profiles = tuple(
        JointReflex(joint=name, partner=names[i ^ 1],
                    withdrawal_direction=1 if i % 2 == 0 else -1)
        for i, name in enumerate(names)
    )
    gate = MotorGate({name: (-1.0, 1.0) for name in names}, revision=REVISION)
    controller = LocalReflexController(gate, body_id=BODY_ID, joints=profiles)
    return controller, gate, profiles


def observation(tick, profiles, changes=None):
    changes = changes or {}
    joints, proprioception = [], []
    for profile in profiles:
        values = {
            "position_rad": 0.0, "velocity_rad_s": 0.0, "torque_nm": 0.0,
            "load_n": 0.0, "contact": False, "support": False, "hazard": False,
            **changes.get(profile.joint, {}),
        }
        joints.append({"id": profile.joint, "position_rad": values["position_rad"],
                       "velocity_rad_s": values["velocity_rad_s"]})
        proprioception.append({
            "joint": profile.joint,
            **{key: values[key] for key in
               ("torque_nm", "load_n", "contact", "support", "hazard")},
        })
    return {
        "schema": OBSERVATION_SCHEMA, "body_id": BODY_ID, "revision": REVISION,
        "physics": {
            "schema": "mojum.virtual-physics-observation/1", "tick": tick,
            "time_ns": tick * STEP_NS, "backend": "fixture", "synthetic": True,
            "physical_contact": False, "physical_actuation": False,
            "joints": joints, "virtual_contacts": [],
        },
        "proprioception": proprioception,
    }


def initial_intent():
    return {
        "schema": INTENT_SCHEMA, "body_id": BODY_ID, "revision": REVISION,
        "sequence": 0, "issued_at_ns": 0, "expires_at_ns": 2 * STEP_NS,
        "targets": {"joint00": 0.3, "joint01": -0.3},
    }


def check_reciprocal_inhibition(frame):
    for command in frame["commands"]:
        flexor, extensor = command["flexor"], command["extensor"]
        require(0 <= flexor <= 1 and 0 <= extensor <= 1,
                "normalized activation outside [0, 1]")
        require(flexor * extensor == 0, "antagonists activated together")


def run_scenarios():
    controller, gate, profiles = make_controller()
    scenarios = []

    def record(name, tick, changes=None, intent=None):
        frame = controller.step(observation(tick, profiles, changes), intent,
                                evaluated_at_ns=tick * STEP_NS)
        check_reciprocal_inhibition(frame)
        scenarios.append({"name": name, "frame": frame})
        return frame

    frame = record("fresh_cognitive_target", 0, intent=initial_intent())
    require(frame["intent_status"] == "accepted", "intention was not accepted")
    require(all(c["source"] == "cognitive" for c in frame["commands"]),
            "expected cognitive targets")
    frame = record("cognition_stalled_cached_target", 1)
    require(frame["intent_status"] == "retained", "cached intention was not retained")

    changes = {"joint00": {"hazard": True},
               "joint01": {"position_rad": 0.2, "contact": True, "support": True}}
    frame = record("expired_cognition_withdrawal_and_crossed_support", 2, changes)
    require(frame["intent_status"] == "expired", "exclusive expiry did not apply")
    require([c["source"] for c in frame["commands"]] == ["withdrawal", "crossed_support"],
            "local protection stopped with cognition")
    checkpoint = controller.snapshot()
    restored_gate = MotorGate({p.joint: (-1.0, 1.0) for p in profiles}, revision=REVISION)
    restored = LocalReflexController.from_snapshot(
        restored_gate, body_id=BODY_ID, joints=profiles,
        snapshot=checkpoint, expected_sha256=checkpoint["sha256"],
    )

    followups = (
        ("stretch_and_velocity_perturbation", 3,
         {"joint00": {"position_rad": 0.8}, "joint01": {"velocity_rad_s": 2.0}},
         ["stretch", "perturbation"]),
        ("force_limits_override_hazard_and_support", 4,
         {"joint00": {"torque_nm": -10.0, "hazard": True},
          "joint01": {"load_n": 100.0, "contact": True, "support": True}},
         ["force_limit", "force_limit"]),
    )
    for name, tick, changes, expected in followups:
        frame = record(name, tick, changes)
        replay = restored.step(observation(tick, profiles, changes),
                               evaluated_at_ns=tick * STEP_NS)
        require(frame == replay, "checkpoint continuation diverged")
        require([c["source"] for c in frame["commands"]] == expected,
                f"unexpected response in {name}")
        if name.startswith("force_limits"):
            require(all(c["flexor"] == c["extensor"] == 0 for c in frame["commands"]),
                    "overload did not inhibit activation")
    require(controller.snapshot() == restored.snapshot(), "continued snapshots differ")

    gate.trip_estop()
    frame = record("latched_emergency_stop", 5, {"joint00": {"hazard": True}})
    require(frame["estop_latched"] and not frame["commands"], "e-stop produced commands")
    stopped_snapshot = controller.snapshot()
    new_gate = MotorGate({p.joint: (-1.0, 1.0) for p in profiles}, revision=REVISION)
    stopped_restore = LocalReflexController.from_snapshot(
        new_gate, body_id=BODY_ID, joints=profiles, snapshot=stopped_snapshot,
        expected_sha256=stopped_snapshot["sha256"],
    )
    frame = stopped_restore.step(observation(6, profiles), evaluated_at_ns=6 * STEP_NS)
    require(new_gate.estop_latched and not frame["commands"], "restore lost e-stop")
    scenarios.append({"name": "restored_emergency_stop", "frame": frame})
    return {
        "checks_passed": True, "checkpoint_continuation_equal": True,
        "reciprocal_inhibition_verified": True,
        "checkpoint_sha256": checkpoint["sha256"], "scenarios": scenarios,
    }


def nearest_rank(values, percentile):
    ordered = sorted(values)
    return ordered[max(0, (len(ordered) * percentile + 99) // 100 - 1)]


def benchmark(ticks, joint_count):
    controller, _, profiles = make_controller(joint_count)
    samples = []
    cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
    for tick in range(ticks):
        changes = {"joint00": {"hazard": tick % 17 == 0},
                   "joint01": {"position_rad": 0.2, "contact": True, "support": True}}
        packet = observation(tick, profiles, changes)
        start = time.perf_counter_ns()
        frame = controller.step(packet, evaluated_at_ns=tick * STEP_NS)
        samples.append(time.perf_counter_ns() - start)
        check_reciprocal_inhibition(frame)
    cpu_ns, wall_ns = time.process_time_ns() - cpu_start, time.perf_counter_ns() - wall_start
    return {
        "ticks": ticks, "joints": joint_count, "python": platform.python_version(),
        "step_latency_ns": {"p50": nearest_rank(samples, 50),
                            "p95": nearest_rank(samples, 95),
                            "p99": nearest_rank(samples, 99), "max": max(samples)},
        "cpu_time_ns_including_fixture_setup": cpu_ns,
        "wall_time_ns_including_fixture_setup": wall_ns,
        "final_snapshot_sha256": controller.snapshot()["sha256"],
        "timing_is_informational": True,
        "timing_scope": "step only; total CPU/wall also include per-tick input construction and checks",
    }


def bounded_int(low, high, *, even=False):
    def parse(value):
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("an integer is required") from exc
        if not low <= number <= high or (even and number % 2):
            qualifier = "even " if even else ""
            raise argparse.ArgumentTypeError(f"expected an {qualifier}integer in [{low}, {high}]")
        return number
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=bounded_int(1, 10_000), default=500)
    parser.add_argument("--joints", type=bounded_int(2, 64, even=True), default=2)
    args = parser.parse_args(argv)
    result = {
        "schema": "mojum.local-reflex.demo/1", "visibility": "PUBLIC_SYNTHETIC",
        "physical_actuation": False, "body_simulated": False,
        "input_source": "scripted fixture measurements; targets are not applied to a body",
        "behavior": run_scenarios(), "benchmark": benchmark(args.ticks, args.joints),
    }
    print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
