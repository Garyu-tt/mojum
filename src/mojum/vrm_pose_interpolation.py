"""Bounded interpolation of already-measured poses for read-only visualization."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math, re
from typing import Any

SCHEMA = "mojum.vrm-pose-interpolation/1"
_FRAME_SCHEMA = "mojum.vrm-measured-pose/1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_COORDINATE_SYSTEMS = frozenset({"METER_RIGHT_HANDED_Y_UP"})
_MAX_JOINTS = 256
_MAX_ABS_COORDINATE = 1000.0

class PoseInterpolationError(ValueError):
    """Raised when measured frames cannot be safely interpolated."""

@dataclass(frozen=True)
class InterpolatedPose:
    schema: str
    classification: str
    source_frame_a: str
    source_frame_b: str
    body_epoch: str
    rig_revision: int
    coordinate_system: str
    timestamp_a_ns: int
    timestamp_b_ns: int
    target_timestamp_ns: int
    ratio: float
    joint_names: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "classification": self.classification,
            "source_frame_a": self.source_frame_a, "source_frame_b": self.source_frame_b,
            "body_epoch": self.body_epoch, "rig_revision": self.rig_revision,
            "coordinate_system": self.coordinate_system,
            "timestamp_a_ns": self.timestamp_a_ns, "timestamp_b_ns": self.timestamp_b_ns,
            "target_timestamp_ns": self.target_timestamp_ns, "ratio": self.ratio,
            "joint_names": list(self.joint_names),
            "coordinates": [list(point) for point in self.coordinates], "digest": self.digest,
        }

def _fail(message: str) -> None:
    raise PoseInterpolationError(message)

def _bounded_finite_float(value: int | float, label: str, maximum: float) -> float:
    """Convert a native number without leaking conversion overflow."""
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        _fail(f"{label} must be finite and within its numeric bound")
    if not math.isfinite(converted) or abs(converted) > maximum:
        _fail(f"{label} must be finite and within its numeric bound")
    return converted

def _exact_keys(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(f"{label} must be an exact built-in dict")
    if len(value) != len(expected):
        _fail(f"{label} must contain exactly the declared fields")
    if any(type(key) is not str or len(key) > 32 for key in value):
        _fail(f"{label} keys must be bounded native strings")
    if frozenset(value) != expected:
        _fail(f"{label} fields do not match schema")
    return dict(value)

_FRAME_KEYS = frozenset({
    "schema", "classification", "frame_id", "body_epoch", "rig_revision",
    "coordinate_system", "timestamp_ns", "joints",
})

def _validate_frame(raw: Any, label: str) -> dict[str, Any]:
    frame = _exact_keys(raw, _FRAME_KEYS, label)
    for key in ("schema", "classification", "frame_id", "body_epoch", "coordinate_system"):
        if type(frame[key]) is not str or len(frame[key]) > 96:
            _fail(f"{label}.{key} must be a bounded native string")
    if frame["schema"] != _FRAME_SCHEMA or frame["classification"] != "PUBLIC_SYNTHETIC":
        _fail(f"{label} must be a PUBLIC_SYNTHETIC {_FRAME_SCHEMA} frame")
    if not _ID.fullmatch(frame["frame_id"]) or not _ID.fullmatch(frame["body_epoch"]):
        _fail(f"{label} identifiers are malformed")
    if frame["coordinate_system"] not in _COORDINATE_SYSTEMS:
        _fail(f"{label} coordinate system is unsupported")
    if type(frame["rig_revision"]) is not int or not 0 <= frame["rig_revision"] <= 2**31 - 1:
        _fail(f"{label}.rig_revision must be a bounded native integer")
    if type(frame["timestamp_ns"]) is not int or not 0 <= frame["timestamp_ns"] <= 2**63 - 1:
        _fail(f"{label}.timestamp_ns must be a bounded native integer")
    joints = frame["joints"]
    if type(joints) is not dict or not 1 <= len(joints) <= _MAX_JOINTS:
        _fail(f"{label}.joints must be a bounded non-empty native dict")
    # Bound attacker-controlled names before regex matching; the regex alone
    # still scans the entire input string when a caller supplies a huge key.
    if any(type(name) is not str or len(name) > 96 or not _ID.fullmatch(name) for name in joints):
        _fail(f"{label} joint names must be bounded native identifiers")
    detached: dict[str, tuple[float, float, float]] = {}
    for name, vector in joints.items():
        if type(vector) is not tuple or len(vector) != 3:
            _fail(f"{label} joint coordinates must be exact 3-tuples")
        if any(type(component) not in (int, float) for component in vector):
            _fail(f"{label} coordinates must be native numbers (bool is not numeric)")
        point = tuple(
            _bounded_finite_float(component, f"{label} coordinate", _MAX_ABS_COORDINATE)
            for component in vector
        )
        detached[name] = point
    frame["joints"] = detached
    return frame

def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def interpolate_measured_poses(frame_a: Any, frame_b: Any, *, target_timestamp_ns: int,
                               max_joint_delta: float = 2.0) -> InterpolatedPose:
    """Interpolate compatible measured frames in-range; never extrapolate."""
    a = _validate_frame(frame_a, "frame_a")
    b = _validate_frame(frame_b, "frame_b")
    if type(target_timestamp_ns) is not int or not 0 <= target_timestamp_ns <= 2**63 - 1:
        _fail("target_timestamp_ns must be a bounded native integer")
    if type(max_joint_delta) not in (int, float):
        _fail("max_joint_delta must be a finite positive native number <= 100")
    max_delta = _bounded_finite_float(max_joint_delta, "max_joint_delta", 100.0)
    if not 0 < max_delta <= 100.0:
        _fail("max_joint_delta must be a finite positive native number <= 100")
    if a["frame_id"] == b["frame_id"]:
        _fail("source frame identifiers must differ")
    if a["body_epoch"] != b["body_epoch"] or a["rig_revision"] != b["rig_revision"]:
        _fail("cannot interpolate across body epochs or rig revisions")
    if a["coordinate_system"] != b["coordinate_system"]:
        _fail("coordinate systems must match")
    if a["timestamp_ns"] >= b["timestamp_ns"]:
        _fail("source timestamps must be strictly increasing")
    if not a["timestamp_ns"] <= target_timestamp_ns <= b["timestamp_ns"]:
        _fail("extrapolation is forbidden; target must be inside source interval")
    names_a, names_b = set(a["joints"]), set(b["joints"])
    if names_a != names_b:
        _fail("source frames must have identical joint inventories")
    names = tuple(sorted(names_a))
    elapsed_ns = target_timestamp_ns - a["timestamp_ns"]
    remaining_ns = b["timestamp_ns"] - target_timestamp_ns
    duration_ns = b["timestamp_ns"] - a["timestamp_ns"]
    ratio = elapsed_ns / duration_ns
    interpolated: list[tuple[float, float, float]] = []
    for name in names:
        left, right = a["joints"][name], b["joints"][name]
        if any(abs(y - x) > max_delta for x, y in zip(left, right)):
            _fail(f"joint {name} exceeds caller-supplied discontinuity bound")
        if elapsed_ns == 0:
            point = left
        elif remaining_ns == 0:
            point = right
        elif elapsed_ns <= remaining_ns:
            point = tuple(x + (y - x) * ratio for x, y in zip(left, right))
        else:
            # Do not derive this weight as 1 - ratio: the display ratio may
            # round to 1.0 for an interior nanosecond in a long interval.
            reverse_ratio = remaining_ns / duration_ns
            point = tuple(y + (x - y) * reverse_ratio for x, y in zip(left, right))
        if any(not math.isfinite(value) for value in point):
            _fail("interpolation produced a non-finite coordinate")
        interpolated.append(point)
    payload = {
        "schema": SCHEMA, "classification": "PUBLIC_SYNTHETIC",
        "source_frame_a": a["frame_id"], "source_frame_b": b["frame_id"],
        "body_epoch": a["body_epoch"], "rig_revision": a["rig_revision"],
        "coordinate_system": a["coordinate_system"],
        "timestamp_a_ns": a["timestamp_ns"], "timestamp_b_ns": b["timestamp_ns"],
        "target_timestamp_ns": target_timestamp_ns, "ratio": ratio,
        "joint_names": list(names), "coordinates": [list(point) for point in interpolated],
    }
    return InterpolatedPose(
        schema=SCHEMA, classification="PUBLIC_SYNTHETIC",
        source_frame_a=a["frame_id"], source_frame_b=b["frame_id"],
        body_epoch=a["body_epoch"], rig_revision=a["rig_revision"],
        coordinate_system=a["coordinate_system"], timestamp_a_ns=a["timestamp_ns"],
        timestamp_b_ns=b["timestamp_ns"], target_timestamp_ns=target_timestamp_ns,
        ratio=ratio, joint_names=names, coordinates=tuple(interpolated),
        digest=_canonical_digest(payload),
    )

def verify_interpolated_pose(result: Any) -> bool:
    """Recompute consistency digest; this is not authentication."""
    if type(result) is not InterpolatedPose:
        _fail("result must be an InterpolatedPose")
    payload = result.as_dict()
    digest = payload.pop("digest")
    if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        _fail("result digest is malformed")
    return _canonical_digest(payload) == digest
