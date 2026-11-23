"""Bounded, offline audit of consecutive synthetic virtual-physics observations.

This module validates records; it does not establish engine provenance, simulate
physical dynamics, authorize control, or store records.
"""
from __future__ import annotations

import hashlib
import json

from mojum.physics_observation import validate_virtual_physics_observation

SCHEMA = "mojum.virtual-physics-trace-audit/1"
MAX_WIRE_I64 = 2**63 - 1
MAX_FRAMES = 32
MAX_TOTAL_JOINTS = 4096
MAX_TOTAL_CONTACTS = 8192
MAX_STEP_NS = 1_000_000_000


def audit_physics_trace(
    frames: object,
    *,
    start_tick: int,
    start_time_ns: int,
    step_ns: int,
    data_classification: str,
    consent: bool,
) -> dict:
    """Return a deterministic metadata-only receipt for a synthetic trace.

    Every packet is checked by the existing passive observation validator;
    ticks and times must form a caller-declared consecutive logical timeline.
    No packet, joint ID, or contact identifier appears in the returned receipt.
    """
    if type(data_classification) is not str or data_classification != "PUBLIC_SYNTHETIC":
        raise ValueError("PUBLIC_SYNTHETIC classification is required")
    if consent is not True:
        raise ValueError("explicit consent is required")
    if type(frames) not in (list, tuple) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("invalid physics trace frame count")
    if type(start_tick) is not int or not 0 <= start_tick <= MAX_WIRE_I64:
        raise ValueError("invalid start tick")
    if type(start_time_ns) is not int or not 0 <= start_time_ns <= MAX_WIRE_I64:
        raise ValueError("invalid start time")
    if type(step_ns) is not int or not 1 <= step_ns <= MAX_STEP_NS:
        raise ValueError("invalid logical step")

    steps = len(frames) - 1
    if (start_tick > MAX_WIRE_I64 - steps
            or start_time_ns > MAX_WIRE_I64 - steps * step_ns):
        raise ValueError("trace clock exceeds the signed 63-bit limit")

    snapshots = []
    backend = None
    joint_ids = None
    joint_total = 0
    contact_total = 0
    for index, frame in enumerate(frames):
        # Always use canonical detached observations; never trust caller-owned
        # nested containers after validating them.
        checked = validate_virtual_physics_observation(frame)
        if (checked["tick"] != start_tick + index
                or checked["time_ns"] != start_time_ns + index * step_ns):
            raise ValueError("nonconsecutive physics clock")
        if backend is None:
            backend = checked["backend"]
        elif checked["backend"] != backend:
            raise ValueError("backend changed during one trace")
        current_ids = tuple(sorted(j["id"] for j in checked["joints"]))
        if joint_ids is None:
            joint_ids = current_ids
        elif current_ids != joint_ids:
            raise ValueError("joint inventory changed during one trace")
        joint_total += len(checked["joints"])
        contact_total += len(checked["virtual_contacts"])
        if joint_total > MAX_TOTAL_JOINTS or contact_total > MAX_TOTAL_CONTACTS:
            raise ValueError("trace observation budget exceeded")
        snapshots.append(checked)

    # Bind the receipt to the *entire* validated trace, including numeric
    # measurements and contact records, without returning raw measurements.
    canonical = json.dumps({
        "schema": SCHEMA,
        "start_tick": start_tick,
        "start_time_ns": start_time_ns,
        "step_ns": step_ns,
        "frames": snapshots,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return {
        "schema": SCHEMA,
        "data_classification": "PUBLIC_SYNTHETIC",
        "frame_count": len(snapshots),
        "backend": backend,
        "joint_count": len(joint_ids),
        "virtual_contact_count": contact_total,
        "start_tick": start_tick,
        "end_tick": start_tick + steps,
        "start_time_ns": start_time_ns,
        "end_time_ns": start_time_ns + steps * step_ns,
        "step_ns": step_ns,
        "trace_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
