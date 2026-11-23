"""Offline admission of *untrusted* synthetic VR/game world observations.

No VR/game SDK, network, filesystem, clocks, device control, or private journal
access is performed. A caller-supplied grant is a scope fence, NOT an identity
credential. Admission hashes attest reproducibility, not source authenticity.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

FRAME_SCHEMA = "mojum.external-world-frame/1"
ADMISSION_SCHEMA = "mojum.external-world-admission/1"
CAPABILITY = "world.observe.synthetic"
_MAX_TICK = 1_000_000_000
_MAX_SEQ = 1_000_000_000
_MAX_OBJECTS = 64
_MAX_COORD = 10_000
_MAX_NS = 2**63 - 1
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FRAME_KEYS = frozenset({
    "schema", "audience", "synthetic", "source_kind", "source_id",
    "world_id", "capability", "source_revision", "sequence", "tick",
    "time_ns", "objects", "physical_contact", "physical_actuation",
})
_OBJECT_KEYS = frozenset({"id", "x_m", "y_m", "visible"})
_ADMISSION_KEYS = frozenset({"schema", "frame", "frame_sha256", "parent_sha256", "admission_sha256"})


def _fields(value: object, keys: frozenset[str]) -> bool:
    return (type(value) is dict and len(value) == len(keys)
            and all(type(k) is str for k in value)
            and set(value) == keys)


def _name(value: object) -> bool:
    return type(value) is str and _NAME.fullmatch(value) is not None


def _hex(value: object) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _bounded_int(value: object, high: int) -> bool:
    return type(value) is int and 0 <= value <= high


def _coord(value: object) -> bool:
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value) and -_MAX_COORD <= value <= _MAX_COORD
    except (OverflowError, TypeError, ValueError):
        return False


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class ExternalFrameGrant:
    """Local caller configuration, not cryptographic authentication."""

    source_kind: str
    source_id: str
    world_id: str
    tick_period_ns: int
    capability: str = CAPABILITY

    def __post_init__(self) -> None:
        if (type(self.source_kind) is not str
                or self.source_kind not in ("vr_fixture", "game_fixture")
                or not _name(self.source_id) or not _name(self.world_id)
                or type(self.capability) is not str or self.capability != CAPABILITY
                or not _bounded_int(self.tick_period_ns, 1_000_000_000)
                or self.tick_period_ns == 0):
            raise ValueError("invalid synthetic external-world grant")


def _validated_frame(frame: object, grant: ExternalFrameGrant, expected_tick: int) -> dict:
    if type(grant) is not ExternalFrameGrant:
        raise ValueError("explicit external-world grant required")
    if not _bounded_int(expected_tick, _MAX_TICK):
        raise ValueError("invalid expected logical tick")
    if not _fields(frame, _FRAME_KEYS):
        raise ValueError("invalid external-world frame keys")
    if (type(frame["schema"]) is not str or frame["schema"] != FRAME_SCHEMA
            or type(frame["audience"]) is not str or frame["audience"] != "PUBLIC_SYNTHETIC"
            or frame["synthetic"] is not True
            or frame["physical_contact"] is not False
            or frame["physical_actuation"] is not False):
        raise ValueError("only synthetic, nonphysical frames are admitted")
    for key, required in (("source_kind", grant.source_kind),
                          ("source_id", grant.source_id),
                          ("world_id", grant.world_id),
                          ("capability", grant.capability)):
        if type(frame[key]) is not str or frame[key] != required:
            raise ValueError("source or capability scope mismatch")
    if not _hex(frame["source_revision"]):
        raise ValueError("invalid source revision")
    if (not _bounded_int(frame["tick"], _MAX_TICK)
            or frame["tick"] != expected_tick
            or not _bounded_int(frame["sequence"], _MAX_SEQ)):
        raise ValueError("invalid or misaligned frame tick/sequence")
    if (not _bounded_int(frame["time_ns"], _MAX_NS)
            or frame["time_ns"] != expected_tick * grant.tick_period_ns):
        raise ValueError("invalid logical timestamp")
    objects = frame["objects"]
    if type(objects) is not list or len(objects) > _MAX_OBJECTS:
        raise ValueError("invalid external object count")
    copied_objects = []
    used_ids = set()
    for obj in objects:
        if not _fields(obj, _OBJECT_KEYS):
            raise ValueError("invalid external object fields")
        if (not _name(obj["id"]) or obj["id"] in used_ids
                or not _coord(obj["x_m"]) or not _coord(obj["y_m"])
                or type(obj["visible"]) is not bool):
            raise ValueError("invalid external object reading")
        used_ids.add(obj["id"])
        copied_objects.append({k: obj[k] for k in ("id", "x_m", "y_m", "visible")})
    result = {k: frame[k] for k in _FRAME_KEYS if k != "objects"}
    result["objects"] = copied_objects
    # Reject values that the canonical serializer cannot faithfully represent.
    try:
        _canonical(result)
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise ValueError("invalid external frame JSON") from exc
    return result


def _validate_previous(previous: object, grant: ExternalFrameGrant, expected_tick: int) -> dict:
    if not _fields(previous, _ADMISSION_KEYS):
        raise ValueError("invalid prior admission envelope")
    if type(previous["schema"]) is not str or previous["schema"] != ADMISSION_SCHEMA:
        raise ValueError("invalid prior admission schema")
    if expected_tick == 0:
        raise ValueError("genesis frame cannot have a predecessor")
    previous_frame = _validated_frame(previous["frame"], grant, expected_tick - 1)
    for key in ("frame_sha256", "admission_sha256"):
        if not _hex(previous[key]):
            raise ValueError("invalid predecessor digest")
    if previous["parent_sha256"] is not None and not _hex(previous["parent_sha256"]):
        raise ValueError("invalid parent digest")
    if _digest(previous_frame) != previous["frame_sha256"]:
        raise ValueError("predecessor frame was modified")
    unsigned = {k: previous[k] for k in _ADMISSION_KEYS if k != "admission_sha256"}
    if _digest(unsigned) != previous["admission_sha256"]:
        raise ValueError("predecessor admission was modified")
    return previous_frame


def admit_external_frame(frame: object, *, grant: ExternalFrameGrant,
                         expected_tick: int, previous_admission: dict | None = None) -> dict:
    """Detach + hash one frame, requiring contiguous tick and source sequence.

    The genesis frame requires tick=sequence=0; subsequent frames need
    the immediately prior admission (same grant and logical tick period).
    Hashes detect accidental/tampered replay of retained data, NOT a malicious
    source with signing authority. This routine never advances the real world.
    """
    clean = _validated_frame(frame, grant, expected_tick)
    parent = None
    if previous_admission is not None:
        prior = _validate_previous(previous_admission, grant, expected_tick)
        if (prior["sequence"] >= _MAX_SEQ
                or clean["sequence"] != prior["sequence"] + 1):
            raise ValueError("external frame sequence gap or replay")
        parent = previous_admission["admission_sha256"]
    elif clean["tick"] != 0 or clean["sequence"] != 0:
        # A bootstrap starts deterministically, not in an unknown frame.
        raise ValueError("missing predecessor for non-genesis external frame")
    result = {"schema": ADMISSION_SCHEMA,
              "frame": clean,
              "frame_sha256": _digest(clean),
              "parent_sha256": parent}
    result["admission_sha256"] = _digest(result)
    return result


def verify_external_admission(admission: object, *, grant: ExternalFrameGrant,
                              expected_tick: int, previous_admission: dict | None = None) -> dict:
    """Regenerate exactly one receipt and reject changes to any bytewise field."""
    if not _fields(admission, _ADMISSION_KEYS):
        raise ValueError("invalid external admission keys")
    if type(admission["schema"]) is not str or admission["schema"] != ADMISSION_SCHEMA:
        raise ValueError("unsupported external admission schema")
    if not _hex(admission["frame_sha256"]) or not _hex(admission["admission_sha256"]):
        raise ValueError("invalid external admission digest")
    if admission["parent_sha256"] is not None and not _hex(admission["parent_sha256"]):
        raise ValueError("invalid external admission parent")
    expected = admit_external_frame(admission["frame"], grant=grant,
                                    expected_tick=expected_tick,
                                    previous_admission=previous_admission)
    if _canonical(expected) != _canonical(admission):
        raise ValueError("external admission differs from recomputed receipt")
    return expected
