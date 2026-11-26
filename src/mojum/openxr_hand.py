"""Offline, read-only XR_EXT_hand_tracking observations, not an OpenXR runtime.

This module never imports an XR SDK, touches a device, opens a socket, writes
an organism journal or produces motor commands. The source remains untrusted:
a SHA-256 hash chain here detects accidental edits, NOT sender authenticity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from threading import Lock

INPUT_SCHEMA = "mojum.openxr.hand-locations/1"
OBSERVATION_SCHEMA = "mojum.sensor.openxr-hand/1"
EXTENSION = "XR_EXT_hand_tracking"
ZERO_HEAD = "0" * 64
MAX_JSON_BYTES = 64 * 1024
MAX_COORDINATE_M = 1000.0
MAX_RADIUS_M = 0.2
MAX_I64 = (1 << 63) - 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

# Exact Khronos XrHandJointEXT enum order, XR_HAND_JOINT_COUNT_EXT = 26.
JOINTS = (
    "palm", "wrist",
    "thumb_metacarpal", "thumb_proximal", "thumb_distal", "thumb_tip",
    "index_metacarpal", "index_proximal", "index_intermediate", "index_distal", "index_tip",
    "middle_metacarpal", "middle_proximal", "middle_intermediate", "middle_distal", "middle_tip",
    "ring_metacarpal", "ring_proximal", "ring_intermediate", "ring_distal", "ring_tip",
    "little_metacarpal", "little_proximal", "little_intermediate", "little_distal", "little_tip",
)
# XrSpaceLocationFlags: orientation valid, position valid, orientation tracked, position tracked.
ORIENTATION_VALID = 0x01
POSITION_VALID = 0x02
ORIENTATION_TRACKED = 0x04
POSITION_TRACKED = 0x08
_VALID_FLAGS = 0x0F
_INPUT_KEYS = frozenset({
    "schema", "extension", "hand", "base_space", "calibration_sha256",
    "frame_index", "time_ns", "is_active", "joints",
})
_JOINT_KEYS = frozenset({
    "joint", "location_flags", "position_m", "orientation_xyzw", "radius_m",
})


class HandTrackingError(ValueError):
    """Invalid, stale or otherwise inadmissible external hand observation."""


def _exact(obj, keys, what):
    if type(obj) is not dict or set(obj) != keys:
        raise HandTrackingError(f"invalid {what} fields")


def _finite_number(x, bound, what):
    if type(x) not in (int, float):
        raise HandTrackingError(f"invalid {what}")
    try:
        if not math.isfinite(x) or abs(x) > bound:
            raise HandTrackingError(f"out-of-range {what}")
    except (OverflowError, ValueError, TypeError) as exc:
        raise HandTrackingError(f"invalid {what}") from exc
    return float(x)


def _vector(value, count, bound, what):
    if type(value) is not list or len(value) != count:
        raise HandTrackingError(f"invalid {what}")
    return [_finite_number(v, bound, what) for v in value]


def _sha(value):
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _identifiers(hand, base_space, calibration_sha256):
    if hand not in ("left", "right") or type(hand) is not str:
        raise HandTrackingError("invalid hand")
    # No implicit transformations into the virtual room/rig or headset VIEW.
    if base_space not in ("local", "stage") or type(base_space) is not str:
        raise HandTrackingError("unsupported base space")
    if not _sha(calibration_sha256):
        raise HandTrackingError("missing/invalid calibration revision")


def _validate_frame(raw):
    _exact(raw, _INPUT_KEYS, "hand frame")
    if (type(raw["schema"]) is not str or raw["schema"] != INPUT_SCHEMA
            or type(raw["extension"]) is not str or raw["extension"] != EXTENSION):
        raise HandTrackingError("unsupported hand input schema/extension")
    _identifiers(raw["hand"], raw["base_space"], raw["calibration_sha256"])
    for key in ("frame_index", "time_ns"):
        value = raw[key]
        if type(value) is not int or not 0 <= value <= MAX_I64:
            raise HandTrackingError(f"invalid {key}")
    if type(raw["is_active"]) is not bool:
        raise HandTrackingError("invalid is_active")
    joints = raw["joints"]
    if type(joints) is not list or len(joints) != len(JOINTS):
        raise HandTrackingError("expected exactly 26 ordered joints")
    clean = []
    for name, item in zip(JOINTS, joints):
        _exact(item, _JOINT_KEYS, "joint")
        if type(item["joint"]) is not str or item["joint"] != name:
            raise HandTrackingError("duplicate, missing or reordered joint")
        flags = item["location_flags"]
        if type(flags) is not int or not 0 <= flags <= _VALID_FLAGS:
            raise HandTrackingError("unknown location flags")
        if flags & ORIENTATION_TRACKED and not flags & ORIENTATION_VALID:
            raise HandTrackingError("tracked orientation without valid orientation")
        if flags & POSITION_TRACKED and not flags & POSITION_VALID:
            raise HandTrackingError("tracked position without valid position")
        if not raw["is_active"]:
            if (flags != 0 or item["position_m"] is not None
                    or item["orientation_xyzw"] is not None
                    or item["radius_m"] is not None):
                raise HandTrackingError("inactive hand must contain no valid joint locations")
            position = orientation = radius = None
        else:
            if flags & (POSITION_VALID | ORIENTATION_VALID) != (POSITION_VALID | ORIENTATION_VALID):
                raise HandTrackingError("active joint lacks required valid pose flags")
            position = _vector(item["position_m"], 3, MAX_COORDINATE_M, "joint position")
            orientation = _vector(item["orientation_xyzw"], 4, 1.0, "joint orientation")
            norm = math.fsum(v * v for v in orientation)
            if abs(norm - 1.0) > 0.01:
                raise HandTrackingError("joint orientation quaternion is not unit length")
            radius = _finite_number(item["radius_m"], MAX_RADIUS_M, "joint radius")
            if radius < 0:
                raise HandTrackingError("negative joint radius")
        clean.append({
            "joint": name, "location_flags": flags,
            "position_m": position, "orientation_xyzw": orientation, "radius_m": radius,
        })
    return clean


def _unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise HandTrackingError("duplicate JSON key")
        out[key] = value
    return out


def _bad_constant(value):
    raise HandTrackingError("nonfinite JSON value")


def decode_frame_json(data):
    """Decode a bounded UTF-8 JSON frame, refusing duplicate keys and NaN."""
    if type(data) is not bytes or not 0 < len(data) <= MAX_JSON_BYTES:
        raise HandTrackingError("invalid JSON frame size/type")
    try:
        obj = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_pairs,
            parse_constant=_bad_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HandTrackingError("invalid hand frame JSON") from exc
    _validate_frame(obj)
    return obj


def _canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


class HandStream:
    """One explicitly identified untrusted sensor stream; never a motor path.

    Separate instances are required for left/right hands and each calibrated
    origin. expected_head is mandatory optimistic concurrency protection.
    """

    def __init__(self, *, hand, base_space, calibration_sha256):
        _identifiers(hand, base_space, calibration_sha256)
        self.hand = hand
        self.base_space = base_space
        self.calibration_sha256 = calibration_sha256
        self.head = ZERO_HEAD
        self._lock = Lock()
        self.last_time_ns = -1
        self.last_frame_index = -1

    def ingest_json(self, data: bytes, *, expected_head: str):
        return self.ingest(decode_frame_json(data), expected_head=expected_head)

    def ingest(self, raw: dict, *, expected_head: str):
        # Serialize check, packet hash and head update: one CAS winner per head.
        with self._lock:
            if type(expected_head) is not str or expected_head != self.head:
                raise HandTrackingError("stale stream head")
            joints = _validate_frame(raw)
            if (raw["hand"] != self.hand or raw["base_space"] != self.base_space
                    or raw["calibration_sha256"] != self.calibration_sha256):
                raise HandTrackingError("hand or reference-space calibration changed")
            if raw["time_ns"] <= self.last_time_ns or raw["frame_index"] <= self.last_frame_index:
                raise HandTrackingError("nonmonotonic hand stream time/frame")
            packet = {
                "schema": OBSERVATION_SCHEMA,
                "provenance": "untrusted-openxr-readonly/1",
                "external_actuator": None,
                "hand": self.hand,
                "base_space": self.base_space,
                "calibration_sha256": self.calibration_sha256,
                "frame_index": raw["frame_index"],
                "time_ns": raw["time_ns"],
                "is_active": raw["is_active"],
                "joints": joints,
                "previous_sha256": self.head,
            }
            packet["sha256"] = hashlib.sha256(_canonical(packet)).hexdigest()
            # No state mutation until every validation, encoding and hashing succeeds.
            self.head = packet["sha256"]
            self.last_time_ns = raw["time_ns"]
            self.last_frame_index = raw["frame_index"]
            return packet


def verify_observation_chain(frames):
    """Check a bounded in-memory observation list; NOT a signature check.

    Exactly one tracked hand, origin and calibration per chain. No I/O.
    """
    if type(frames) not in (tuple, list) or not 1 <= len(frames) <= 4096:
        raise HandTrackingError("invalid frame sequence")
    hand = base_space = calibration = None
    previous = ZERO_HEAD
    last_time = last_frame = -1
    for frame in frames:
        if type(frame) is not dict or set(frame) != {
            "schema", "provenance", "external_actuator", "hand", "base_space",
            "calibration_sha256", "frame_index", "time_ns", "is_active",
            "joints", "previous_sha256", "sha256",
        }:
            raise HandTrackingError("invalid observation packet fields")
        if (type(frame["schema"]) is not str or frame["schema"] != OBSERVATION_SCHEMA
                or type(frame["provenance"]) is not str
                or frame["provenance"] != "untrusted-openxr-readonly/1"
                or frame["external_actuator"] is not None
                or not _sha(frame["sha256"]) or not _sha(frame["previous_sha256"])
                or frame["previous_sha256"] != previous):
            raise HandTrackingError("invalid observation chain/schema")
        raw = {key: frame[key] for key in _INPUT_KEYS if key not in ("schema", "extension")}
        raw.update(schema=INPUT_SCHEMA, extension=EXTENSION)
        _validate_frame(raw)
        identity = (frame["hand"], frame["base_space"], frame["calibration_sha256"])
        if hand is None:
            hand, base_space, calibration = identity
        elif identity != (hand, base_space, calibration):
            raise HandTrackingError("mixed hand or reference-space calibration")
        if frame["time_ns"] <= last_time or frame["frame_index"] <= last_frame:
            raise HandTrackingError("nonmonotonic observation chain")
        check = dict(frame)
        check.pop("sha256")
        if hashlib.sha256(_canonical(check)).hexdigest() != frame["sha256"]:
            raise HandTrackingError("observation digest mismatch")
        previous = frame["sha256"]
        last_time, last_frame = frame["time_ns"], frame["frame_index"]
    return previous
