"""Bounded offline scalar joint-limit auditing for public synthetic samples."""
from dataclasses import dataclass
import hashlib
import json
import math
import re

SCHEMA = "mojum.joint-limits/1"
MAX_JOINTS = 256
MAX_SAMPLES = 4096
MAX_ID_LENGTH = 64
MAX_REVISION_LENGTH = 128
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}", re.ASCII)


@dataclass(frozen=True)
class JointLimitReport:
    schema: str
    rig_revision: str
    sample_count: int
    joint_ids: tuple[str, ...]
    minimum_observed: tuple[tuple[str, float], ...]
    maximum_observed: tuple[tuple[str, float], ...]
    digest: str


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        converted = float(value)
        return math.isfinite(converted) and (type(value) is float or converted == value)
    except (OverflowError, TypeError, ValueError):
        return False


def _identifier(value, what):
    if type(value) is not str or len(value) > MAX_ID_LENGTH or not _ID.fullmatch(value):
        raise ValueError(f"invalid {what}")
    return value


def audit_joint_limits(limits, samples, *, rig_revision):
    """Validate ordered (tick, revision, positions) samples against scalar limits.

    limits is a list/tuple of (joint_id, lower, upper) triples.
    Each sample is a list/tuple of (tick, revision, positions_dict).
    Position bounds are inclusive. Ticks must be consecutive and all samples
    must carry the same rig revision and exact joint inventory.
    Positions and bounds accept finite native floats or native integers exactly
    representable as finite floats; report extrema remain floats.
    """
    if (type(rig_revision) is not str or not rig_revision
            or len(rig_revision) > MAX_REVISION_LENGTH):
        raise ValueError("invalid rig revision")
    if type(limits) not in (tuple, list) or not 1 <= len(limits) <= MAX_JOINTS:
        raise ValueError("invalid joint limit collection")

    normalized_limits = {}
    for raw in limits:
        if type(raw) not in (tuple, list) or len(raw) != 3:
            raise ValueError("invalid joint limit record")
        joint_id = _identifier(raw[0], "joint identifier")
        lower, upper = raw[1], raw[2]
        if not _finite_number(lower) or not _finite_number(upper) or lower >= upper:
            raise ValueError("invalid joint limit bounds")
        if joint_id in normalized_limits:
            raise ValueError("duplicate joint identifier")
        normalized_limits[joint_id] = (lower, upper)

    if type(samples) not in (tuple, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise ValueError("invalid sample collection")
    joint_ids = tuple(sorted(normalized_limits))
    minima = {key: None for key in joint_ids}
    maxima = {key: None for key in joint_ids}
    normalized_samples = []
    previous_tick = None

    for raw in samples:
        if type(raw) not in (tuple, list) or len(raw) != 3:
            raise ValueError("invalid sample record")
        tick, revision, positions = raw
        if type(tick) is not int or tick < 0:
            raise ValueError("invalid logical tick")
        if previous_tick is not None and tick != previous_tick + 1:
            raise ValueError("sample ticks must be strictly consecutive")
        previous_tick = tick
        if (type(revision) is not str or not revision
                or len(revision) > MAX_REVISION_LENGTH or revision != rig_revision):
            raise ValueError("stale or malformed rig revision")
        if type(positions) is not dict or len(positions) != len(joint_ids):
            raise ValueError("joint inventory mismatch")
        keys = tuple(positions.keys())
        if any(type(key) is not str or len(key) > MAX_ID_LENGTH or not _ID.fullmatch(key)
               for key in keys):
            raise ValueError("invalid position joint identifier")
        if set(keys) != set(joint_ids):
            raise ValueError("missing or extra position joints")

        values = []
        for joint_id in joint_ids:
            value = positions[joint_id]
            if not _finite_number(value):
                raise ValueError("joint position must be a finite native number "
                                 "exactly representable as float")
            lower, upper = normalized_limits[joint_id]
            if value < lower or value > upper:
                raise ValueError(f"joint position outside limits: {joint_id}")
            value = float(value)
            minima[joint_id] = value if minima[joint_id] is None else min(minima[joint_id], value)
            maxima[joint_id] = value if maxima[joint_id] is None else max(maxima[joint_id], value)
            values.append((joint_id, value))
        normalized_samples.append((tick, revision, tuple(values)))

    canonical = {
        "schema": SCHEMA,
        "rig_revision": rig_revision,
        "limits": [(key, float(normalized_limits[key][0]), float(normalized_limits[key][1]))
                   for key in joint_ids],
        "samples": [(tick, revision, list(values))
                    for tick, revision, values in normalized_samples],
    }
    encoded = json.dumps(canonical, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return JointLimitReport(
        schema=SCHEMA,
        rig_revision=rig_revision,
        sample_count=len(normalized_samples),
        joint_ids=joint_ids,
        minimum_observed=tuple((key, minima[key]) for key in joint_ids),
        maximum_observed=tuple((key, maxima[key]) for key in joint_ids),
        digest=digest,
    )
