"""Strict passive envelope for future MuJoCo virtual-body observations.

Validation is NOT evidence of engine provenance and does NOT actuate hardware.
The released point-room fixture remains authoritative until an independently
accepted optional MuJoCo adapter produces this schema.
"""
from __future__ import annotations

import json
import math
import re


_MAX_WIRE_I64 = 2**63 - 1
_SCHEMA = "mojum.virtual-physics-observation/1"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_ALLOWED = frozenset({
    "schema", "tick", "time_ns", "backend", "synthetic",
    "physical_contact", "physical_actuation", "joints", "virtual_contacts",
})


def _finite(value: object) -> bool:
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value)
    except (ValueError, TypeError, OverflowError):
        return False


def _name(value: object) -> bool:
    return type(value) is str and _NAME.fullmatch(value) is not None


def validate_virtual_physics_observation(payload: object) -> dict:
    """Validate and detach a bounded JSON-safe simulated observation.

    This is an input-only contract: model/body identity, physical truth,
    and engine origin must be separately authenticated by a future adapter.
    """
    if type(payload) is not dict or set(payload) != _ALLOWED:
        raise ValueError("invalid virtual physics packet keys")
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported physics schema")
    if type(payload["tick"]) is not int or not 0 <= payload["tick"] <= _MAX_WIRE_I64:
        raise ValueError("invalid tick")
    if type(payload["time_ns"]) is not int or not 0 <= payload["time_ns"] <= _MAX_WIRE_I64:
        raise ValueError("invalid simulation timestamp")
    if payload["backend"] not in ("fixture", "mujoco"):
        raise ValueError("unknown virtual physics backend")
    if (payload["synthetic"] is not True
            or payload["physical_contact"] is not False
            or payload["physical_actuation"] is not False):
        raise ValueError("virtual observation cannot claim physical hardware")
    joints, contacts = payload["joints"], payload["virtual_contacts"]
    if type(joints) is not list or len(joints) > 256:
        raise ValueError("invalid joint list")
    if type(contacts) is not list or len(contacts) > 4096:
        raise ValueError("invalid virtual contact list")
    joint_names = set()
    for j in joints:
        if type(j) is not dict or set(j) != {
            "id", "position_rad", "velocity_rad_s",
        }:
            raise ValueError("invalid joint fields")
        if not _name(j["id"]) or j["id"] in joint_names:
            raise ValueError("invalid or duplicate joint id")
        joint_names.add(j["id"])
        if not all(_finite(j[k]) and abs(j[k]) <= 1_000_000
                   for k in ("position_rad", "velocity_rad_s")):
            raise ValueError("nonfinite or unbounded joint reading")
    for c in contacts:
        if type(c) is not dict or set(c) != {
            "geom_a", "geom_b", "normal_force_n",
        }:
            raise ValueError("invalid virtual contact fields")
        if (not _name(c["geom_a"]) or not _name(c["geom_b"])
                or c["geom_a"] == c["geom_b"]):
            raise ValueError("invalid contact geometry")
        if not _finite(c["normal_force_n"]) or not 0 <= c["normal_force_n"] <= 1_000_000:
            raise ValueError("invalid contact force")
    # Canonical detached data: an upstream mutable dict cannot edit a trusted
    # acceptance artifact after validation.
    try:
        return json.loads(json.dumps(
            payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid physics JSON") from exc
