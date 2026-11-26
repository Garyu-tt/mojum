"""Bounded passive stationary-support candidate audit for synthetic physics frames.

This is an offline *screening heuristic*, never a standing, balance, or
perturbation-recovery acceptance certificate. No IO, actuation or history writes.
"""
from __future__ import annotations

import hashlib
import json
import math

from mojum.physics_observation import validate_virtual_physics_observation


SCHEMA = "mojum.standing-window-candidate/1"
MAX_FRAMES = 512
MAX_JOINTS_PER_FRAME = 96
MAX_CONTACTS_PER_FRAME = 16
I64_MAX = 2**63 - 1


class StandingWindowError(ValueError):
    """Malformed, over-budget or temporally inconsistent observation window."""


def _native_keys(value: object, expected: frozenset[str], label: str) -> None:
    if type(value) is not dict or len(value) != len(expected):
        raise StandingWindowError("invalid " + label + " structure")
    # Inspect types before set/equality: Python keys may override __eq__.
    for key in value:
        if type(key) is not str or len(key) > 64:
            raise StandingWindowError("invalid " + label + " key")
    if set(value) != expected:
        raise StandingWindowError("invalid " + label + " fields")


_FRAME_KEYS = frozenset({
    "schema", "tick", "time_ns", "backend", "synthetic",
    "physical_contact", "physical_actuation", "joints", "virtual_contacts",
})
_JOINT_KEYS = frozenset({"id", "position_rad", "velocity_rad_s"})
_CONTACT_KEYS = frozenset({"geom_a", "geom_b", "normal_force_n"})


def _packet(value: object) -> dict:
    """Reject hostile container subclasses and excessive work before decoding."""
    _native_keys(value, _FRAME_KEYS, "frame")
    for selector in ("schema", "backend"):
        item = value[selector]
        if type(item) is not str or len(item) > 64:
            raise StandingWindowError("invalid frame selector")
    joints = value["joints"]
    contacts = value["virtual_contacts"]
    if (type(joints) is not list or not 1 <= len(joints) <= MAX_JOINTS_PER_FRAME
            or type(contacts) is not list or len(contacts) > MAX_CONTACTS_PER_FRAME):
        raise StandingWindowError("invalid or excessive frame samples")
    for joint in joints:
        _native_keys(joint, _JOINT_KEYS, "joint")
        if type(joint["id"]) is not str or len(joint["id"]) > 64:
            raise StandingWindowError("invalid joint identifier")
    for contact in contacts:
        _native_keys(contact, _CONTACT_KEYS, "contact")
        for field in ("geom_a", "geom_b"):
            if type(contact[field]) is not str or len(contact[field]) > 64:
                raise StandingWindowError("invalid contact identifier")
    try:
        return validate_virtual_physics_observation(value)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise StandingWindowError("invalid virtual physics observation") from exc


def _threshold(value: object, upper: float, name: str) -> float:
    if (type(value) not in (int, float) or not 0 <= value <= upper
            or not math.isfinite(value)):
        raise StandingWindowError("invalid " + name)
    return float(value)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def audit_standing_window(
    frames: object,
    *,
    expected_dt_ns: int,
    required_duration_ns: int = 5_000_000_000,
    min_floor_force_n: float = 1.0,
    max_joint_speed_rad_s: float = 0.1,
) -> dict:
    """Evaluate consecutive synthetic observations without changing them.

    Return a deterministic *candidate* receipt. A candidate only means every
    sample met the selected two scalar thresholds; CoM/CoP, actual engine
    provenance, perturbation recovery and physical stability are NOT proven.

    Malformed/gapped observations fail closed with StandingWindowError; valid
    but weak/short observations return candidate=False and explicit reasons.
    """
    if type(frames) is not list or not 2 <= len(frames) <= MAX_FRAMES:
        raise StandingWindowError("invalid or excessive frame count")
    if type(expected_dt_ns) is not int or not 1 <= expected_dt_ns <= 1_000_000_000:
        raise StandingWindowError("invalid expected timestep")
    if (type(required_duration_ns) is not int
            or not 1 <= required_duration_ns <= 60_000_000_000):
        raise StandingWindowError("invalid required duration")
    force_min = _threshold(min_floor_force_n, 1_000_000, "floor-force threshold")
    speed_max = _threshold(max_joint_speed_rad_s, 1_000, "joint-speed threshold")

    previous_tick: int | None = None
    previous_time: int | None = None
    first_tick: int | None = None
    first_time: int | None = None
    backend: str | None = None
    inventory: frozenset[str] | None = None
    minimum_force = math.inf
    maximum_speed = 0.0
    digest = hashlib.sha256()

    for packet in frames:
        observation = _packet(packet)
        tick, time_ns = observation["tick"], observation["time_ns"]
        joint_set = frozenset(joint["id"] for joint in observation["joints"])
        if previous_tick is None:
            first_tick, first_time = tick, time_ns
            backend, inventory = observation["backend"], joint_set
        else:
            if tick != previous_tick + 1 or time_ns != previous_time + expected_dt_ns:
                raise StandingWindowError("nonconsecutive physics clock")
            if observation["backend"] != backend or joint_set != inventory:
                raise StandingWindowError("backend or joint inventory changed")
        previous_tick, previous_time = tick, time_ns
        digest.update(_canonical(observation))
        digest.update(b"\n")

        support_forces = [
            float(contact["normal_force_n"])
            for contact in observation["virtual_contacts"]
            if contact["geom_a"] == "floor" or contact["geom_b"] == "floor"
        ]
        floor_force = math.fsum(support_forces)
        minimum_force = min(minimum_force, floor_force)
        frame_speed = max(
            abs(float(joint["velocity_rad_s"])) for joint in observation["joints"]
        )
        maximum_speed = max(maximum_speed, frame_speed)

    assert first_time is not None and previous_time is not None
    assert first_tick is not None and previous_tick is not None and backend is not None
    duration = previous_time - first_time
    failures = []
    if duration < required_duration_ns:
        failures.append("insufficient_duration")
    if minimum_force < force_min or minimum_force <= 0:
        failures.append("insufficient_floor_support")
    if maximum_speed > speed_max:
        failures.append("excessive_joint_motion")

    receipt = {
        "schema": SCHEMA,
        "backend_declared": backend,
        "frame_count": len(frames),
        "start_tick": first_tick,
        "end_tick": previous_tick,
        "duration_ns": duration,
        "expected_dt_ns": expected_dt_ns,
        "required_duration_ns": required_duration_ns,
        "min_floor_force_n": force_min,
        "max_joint_speed_rad_s": speed_max,
        "observed_min_floor_force_n": minimum_force,
        "observed_max_joint_speed_rad_s": maximum_speed,
        "evidence_sha256": digest.hexdigest(),
        "stationary_support_candidate": not failures,
        "reasons": failures,
        "standing_milestone_achieved": False,
        "perturbation_recovery_achieved": False,
        "source_authenticated": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_canonical(receipt)).hexdigest()
    return receipt
