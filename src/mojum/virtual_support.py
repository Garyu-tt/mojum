"""CPU-only synthetic floor-support diagnostics for a future virtual body engine.

This pure read-only projection neither authenticates an engine nor controls a
body. A declared virtual floor contact is NOT verified physical floor contact.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

INPUT_SCHEMA = "mojum.virtual-physics-support-input/1"
OUTPUT_SCHEMA = "mojum.virtual-physics-support/1"
MAX_CONTACTS = 128
MAX_COORD_M = 10_000.0
MAX_FORCE_N = 1_000_000.0
MAX_WIRE_I64 = 2**63 - 1

_KEYS = frozenset({
    "schema", "tick", "time_ns", "backend", "synthetic",
    "physical_contact", "physical_actuation", "com_m", "floor_contacts",
})
_CONTACT_KEYS = frozenset({"id", "point_m", "vertical_force_n"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)


def _finite_number(value: object, maximum: float, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError("invalid " + label)
    try:
        number = float(value)
    except (OverflowError, ValueError, TypeError) as exc:
        raise ValueError("invalid " + label) from exc
    if not math.isfinite(number) or abs(number) > maximum:
        raise ValueError("out-of-range " + label)
    # Normalize signed zero so semantically identical JSON has one digest.
    return 0.0 if number == 0 else number


def _position(value: object, label: str) -> list[float]:
    if type(value) is not list or len(value) != 3:
        raise ValueError("invalid " + label)
    return [_finite_number(v, MAX_COORD_M, label) for v in value]


def observe_virtual_support(packet: object) -> dict:
    """Validate a standalone virtual support sample and derive force-weighted CoP.

    The caller must independently validate the engine, anatomy, clock and
    contact provenance. The SHA-256 digest is a reproducibility checksum,
    NOT authentication or tamper protection against source replacement.
    """
    if type(packet) is not dict or set(packet) != _KEYS:
        raise ValueError("invalid support envelope")
    if type(packet["schema"]) is not str or packet["schema"] != INPUT_SCHEMA:
        raise ValueError("unsupported support schema")
    for field in ("tick", "time_ns"):
        value = packet[field]
        if type(value) is not int or not 0 <= value <= MAX_WIRE_I64:
            raise ValueError("invalid " + field)
    if type(packet["backend"]) is not str or packet["backend"] not in ("fixture", "mujoco"):
        raise ValueError("unsupported backend")
    if (packet["synthetic"] is not True
            or packet["physical_contact"] is not False
            or packet["physical_actuation"] is not False):
        raise ValueError("physical observations or actuation are forbidden")
    com = _position(packet["com_m"], "center of mass")
    supplied = packet["floor_contacts"]
    if type(supplied) is not list or len(supplied) > MAX_CONTACTS:
        raise ValueError("unbounded floor contacts")

    contacts: list[dict] = []
    ids: set[str] = set()
    for contact in supplied:
        if type(contact) is not dict or set(contact) != _CONTACT_KEYS:
            raise ValueError("invalid virtual floor contact")
        ident = contact["id"]
        if type(ident) is not str or _ID.fullmatch(ident) is None or ident in ids:
            raise ValueError("invalid or duplicate contact id")
        ids.add(ident)
        point = _position(contact["point_m"], "virtual contact position")
        force = _finite_number(contact["vertical_force_n"], MAX_FORCE_N, "vertical force")
        if force < 0:
            raise ValueError("negative floor reaction")
        contacts.append({"id": ident, "point_m": point, "vertical_force_n": force})
    contacts.sort(key=lambda c: c["id"])

    # Sorted, bounded inputs make the result independent of source list order.
    load = math.fsum(c["vertical_force_n"] for c in contacts)
    loaded = sum(c["vertical_force_n"] > 0 for c in contacts)
    cop = None
    if load > 0:
        cop = [
            math.fsum(c["point_m"][axis] * c["vertical_force_n"]
                      for c in contacts) / load
            for axis in (0, 1)
        ]
        cop = [0.0 if v == 0 else v for v in cop]
    normalized = {
        "schema": INPUT_SCHEMA,
        "tick": packet["tick"],
        "time_ns": packet["time_ns"],
        "backend": packet["backend"],
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "com_m": com,
        "floor_contacts": contacts,
    }
    canonical = json.dumps(
        normalized, sort_keys=True, ensure_ascii=True,
        allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": OUTPUT_SCHEMA,
        "source_schema": INPUT_SCHEMA,
        "tick": normalized["tick"],
        "time_ns": normalized["time_ns"],
        "backend": normalized["backend"],
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "com_m": com,
        "cop_xy_m": cop,
        "contact_count": len(contacts),
        "loaded_contact_count": loaded,
        "total_vertical_force_n": load,
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
    }
