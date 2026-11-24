"""Bounded offline continuity audit for PUBLIC_SYNTHETIC joint trajectories."""
from __future__ import annotations
import hashlib, json, math
SCHEMA = "mojum.joint-continuity/1"
TWO_PI = 2.0 * math.pi
MAX_SAMPLES, MAX_JOINTS, MAX_ID_LENGTH = 10_000, 512, 128

def _number(v):
    if type(v) not in (int, float):
        return False
    try:
        return math.isfinite(v)
    except (OverflowError, TypeError, ValueError):
        return False

def _identifier(v):
    return type(v) is str and 0 < len(v) <= MAX_ID_LENGTH and v.strip() == v and all(ord(c) >= 32 and ord(c) != 127 for c in v)

def _delta(a, b, wrap):
    d = b - a
    return abs((d + math.pi) % TWO_PI - math.pi) if wrap else abs(d)

def audit_joint_continuity(samples, limits, *, revolute=(), expected_revision=None, max_samples=MAX_SAMPLES, max_joints=MAX_JOINTS):
    """Audit consecutive position samples; caller declares radians and wrap semantics."""
    if type(max_samples) is not int or not 1 <= max_samples <= MAX_SAMPLES: raise ValueError("invalid max_samples")
    if type(max_joints) is not int or not 1 <= max_joints <= MAX_JOINTS: raise ValueError("invalid max_joints")
    if type(samples) not in (list, tuple) or not 1 <= len(samples) <= max_samples: raise ValueError("invalid samples")
    if type(limits) is not dict or not limits or len(limits) > max_joints: raise ValueError("invalid limits")
    if type(revolute) not in (list, tuple, set, frozenset): raise ValueError("invalid revolute set")
    if any(not _identifier(k) for k in limits): raise ValueError("invalid joint id")
    if any(not _number(v) or v <= 0 or v > TWO_PI for v in limits.values()): raise ValueError("invalid angular limit")
    if any(not _identifier(k) for k in revolute) or len(set(revolute)) != len(revolute): raise ValueError("invalid revolute id")
    if not set(revolute).issubset(limits): raise ValueError("unknown revolute joint")
    if expected_revision is not None and not _identifier(expected_revision): raise ValueError("invalid expected revision")
    joints = tuple(sorted(limits))
    previous = None
    previous_tick = None
    revision = None
    maxima = {j: 0.0 for j in joints}
    first_bad = {}
    normalized = []
    for i, row in enumerate(samples):
        if type(row) is not dict or set(row) != {"tick", "revision", "positions"}: raise ValueError("invalid sample shape")
        tick, rev, positions = row["tick"], row["revision"], row["positions"]
        if type(tick) is not int or tick < 0: raise ValueError("invalid tick")
        if not _identifier(rev): raise ValueError("invalid rig revision")
        if type(positions) is not dict or set(positions) != set(joints): raise ValueError("inconsistent joint set")
        if any(not _number(v) for v in positions.values()): raise ValueError("non-finite joint position")
        if i == 0:
            revision = rev
            if expected_revision is not None and rev != expected_revision: raise ValueError("unexpected rig revision")
        else:
            if tick != previous_tick + 1: raise ValueError("ticks must be consecutive")
            if rev != revision: raise ValueError("rig revision changed")
            for j in joints:
                d = _delta(previous[j], positions[j], j in revolute)
                maxima[j] = max(maxima[j], d)
                if d > limits[j] and j not in first_bad: first_bad[j] = tick
        normalized.append({"tick": tick, "revision": rev, "positions": {j: positions[j] for j in joints}})
        previous, previous_tick = positions, tick
    violations = [{"joint": j, "first_offending_tick": first_bad[j], "max_step_rad": maxima[j], "limit_rad": limits[j]} for j in joints if j in first_bad]
    payload = {"schema": SCHEMA, "revision": revision, "limits": {j: limits[j] for j in joints}, "revolute": sorted(revolute), "samples": normalized}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"schema": SCHEMA, "ok": not violations, "revision": revision, "sample_count": len(normalized), "joint_count": len(joints), "max_steps_rad": maxima, "violations": violations, "digest_sha256": hashlib.sha256(raw).hexdigest(), "authority": "PUBLIC_SYNTHETIC_QA_ONLY"}
