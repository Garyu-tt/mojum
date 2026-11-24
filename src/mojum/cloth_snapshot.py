"""Bounded offline snapshots and replay checks for synthetic cloth proxy state.

This module serializes caller-supplied PUBLIC_SYNTHETIC numerical fixtures. It
does not run a solver, load assets, or establish physical correctness.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA = "mojum.cloth-snapshot/1"
MAX_VERTICES = 4096
MAX_BYTES = 1_048_576
MAX_REVISION = 128
MAX_SOLVER_ID = 64
MAX_TICK = (1 << 63) - 1


class ClothSnapshotError(ValueError):
    """Invalid, oversized, inconsistent, or non-canonical cloth snapshot."""


@dataclass(frozen=True)
class ClothSnapshot:
    tick: int
    time_ns: int
    dt_ns: int
    mesh_sha256: str
    rig_revision: str
    profile_revision: str
    solver_id: str
    positions: tuple[tuple[float, float, float], ...]
    velocities: tuple[tuple[float, float, float], ...]
    sha256: str


def _native_int(value: Any, name: str, *, minimum: int = 0, maximum: int = MAX_TICK) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ClothSnapshotError(f"{name} must be a native integer in range")
    return value


def _text(value: Any, name: str, limit: int, *, digest: bool = False) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise ClothSnapshotError(f"{name} must be non-empty bounded text")
    if digest and (len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
        raise ClothSnapshotError(f"{name} must be a lowercase SHA-256 digest")
    if any(ord(c) < 32 for c in value):
        raise ClothSnapshotError(f"{name} contains control characters")
    return value


def _vectors(value: Any, name: str) -> tuple[tuple[float, float, float], ...]:
    if type(value) is not list or not 1 <= len(value) <= MAX_VERTICES:
        raise ClothSnapshotError(f"{name} must contain 1..{MAX_VERTICES} vectors")
    result = []
    for index, vector in enumerate(value):
        if type(vector) is not list or len(vector) != 3:
            raise ClothSnapshotError(f"{name}[{index}] must be a 3-vector")
        row = []
        for component in vector:
            if type(component) not in (int, float):
                raise ClothSnapshotError(f"{name}[{index}] components must be native numbers")
            number = float(component)
            if not math.isfinite(number) or abs(number) > 1_000_000:
                raise ClothSnapshotError(f"{name}[{index}] component is outside finite bounds")
            row.append(number)
        result.append(tuple(row))
    return tuple(result)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ClothSnapshotError("snapshot is not canonical JSON data") from exc


def _state(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise ClothSnapshotError("state must be an exact dictionary")
    required = {"tick", "time_ns", "dt_ns", "mesh_sha256", "rig_revision",
                "profile_revision", "solver_id", "positions", "velocities"}
    if set(value) != required:
        raise ClothSnapshotError("state shape mismatch")
    tick = _native_int(value["tick"], "tick")
    time_ns = _native_int(value["time_ns"], "time_ns")
    dt_ns = _native_int(value["dt_ns"], "dt_ns", minimum=1)
    mesh = _text(value["mesh_sha256"], "mesh_sha256", 64, digest=True)
    rig = _text(value["rig_revision"], "rig_revision", MAX_REVISION)
    profile = _text(value["profile_revision"], "profile_revision", MAX_REVISION)
    solver = _text(value["solver_id"], "solver_id", MAX_SOLVER_ID)
    positions = _vectors(value["positions"], "positions")
    velocities = _vectors(value["velocities"], "velocities")
    if len(positions) != len(velocities):
        raise ClothSnapshotError("position/velocity vertex count mismatch")
    if time_ns != tick * dt_ns:
        raise ClothSnapshotError("time_ns must equal tick * dt_ns")
    return {
        "tick": tick, "time_ns": time_ns, "dt_ns": dt_ns,
        "mesh_sha256": mesh, "rig_revision": rig, "profile_revision": profile,
        "solver_id": solver,
        "positions": [list(row) for row in positions],
        "velocities": [list(row) for row in velocities],
    }


def encode_snapshot(state: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 envelope for one PUBLIC_SYNTHETIC state."""
    if not isinstance(state, dict) or type(state) is not dict:
        raise ClothSnapshotError("state must be an exact dictionary")
    normalized = _state(state)
    payload = _canonical(normalized)
    envelope = {"schema": SCHEMA, "state": normalized,
                "sha256": hashlib.sha256(payload).hexdigest()}
    encoded = _canonical(envelope)
    if len(encoded) > MAX_BYTES:
        raise ClothSnapshotError("snapshot exceeds byte budget")
    return encoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClothSnapshotError("duplicate JSON key")
        result[key] = value
    return result


def decode_snapshot(data: bytes | str) -> ClothSnapshot:
    """Decode only canonical, bounded, integrity-checked snapshot bytes."""
    if type(data) is bytes:
        raw = data
    elif type(data) is str:
        try:
            raw = data.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ClothSnapshotError("snapshot must be ASCII canonical JSON") from exc
    else:
        raise ClothSnapshotError("snapshot must be bytes or text")
    if not raw or len(raw) > MAX_BYTES:
        raise ClothSnapshotError("snapshot byte size outside bounds")
    try:
        parsed = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ClothSnapshotError("non-finite JSON number")))
    except ClothSnapshotError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ClothSnapshotError("invalid snapshot JSON") from exc
    if type(parsed) is not dict or set(parsed) != {"schema", "state", "sha256"} or parsed["schema"] != SCHEMA:
        raise ClothSnapshotError("snapshot envelope/schema mismatch")
    normalized = _state(parsed["state"])
    payload = _canonical(normalized)
    expected = hashlib.sha256(payload).hexdigest()
    supplied = _text(parsed["sha256"], "sha256", 64, digest=True)
    if not hashlib.compare_digest(expected, supplied):
        raise ClothSnapshotError("snapshot digest mismatch")
    if _canonical({"schema": SCHEMA, "state": normalized, "sha256": expected}) != raw:
        raise ClothSnapshotError("snapshot is not canonical")
    return ClothSnapshot(
        tick=normalized["tick"], time_ns=normalized["time_ns"], dt_ns=normalized["dt_ns"],
        mesh_sha256=normalized["mesh_sha256"], rig_revision=normalized["rig_revision"],
        profile_revision=normalized["profile_revision"], solver_id=normalized["solver_id"],
        positions=tuple(tuple(row) for row in normalized["positions"]),
        velocities=tuple(tuple(row) for row in normalized["velocities"]), sha256=expected,
    )


def verify_replay(sequence: list[bytes | str]) -> tuple[str, ...]:
    """Validate a bounded contiguous sequence and return its ordered digests."""
    if type(sequence) is not list or not 1 <= len(sequence) <= 10_000:
        raise ClothSnapshotError("replay must contain 1..10000 snapshots")
    snapshots = [decode_snapshot(item) for item in sequence]
    first = snapshots[0]
    for index, current in enumerate(snapshots):
        if current.tick != first.tick + index:
            raise ClothSnapshotError("replay ticks must be contiguous")
        if (current.dt_ns, current.mesh_sha256, current.rig_revision,
                current.profile_revision, current.solver_id, len(current.positions)) != (
                first.dt_ns, first.mesh_sha256, first.rig_revision,
                first.profile_revision, first.solver_id, len(first.positions)):
            raise ClothSnapshotError("replay configuration changed")
    return tuple(item.sha256 for item in snapshots)
