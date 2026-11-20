"""Provisional object continuity inferred from self-position and numeric cues.

No privileged toy identity, hidden geometry, labels or sensor-to-truth mapping
are used here. Same numeric cue in ambiguous positions may be *unknown*.
This is a bounded CPU-only local-association experiment, not recognition,
a neural representation, visual SLAM, persistent-world proof or reasoning.
"""
from __future__ import annotations

import copy
import math

from .sensory_play import ContactAssociations, CuriosityError

SCHEMA = "mojum.object-memory.provisional/1"
BEARING_SCHEMA = "mojum.sensor.synthetic-azimuth/1"
MAX_TRACKS = 64
MAX_FRAMES = 3999
ASSOCIATION_RADIUS_M = 0.32
CONTACT_CONFLICT_THRESHOLD = 0.30


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (ValueError, TypeError, OverflowError):
        return False


def _bearing(sensor, direction):
    # Verify exactly the sensor contract, no trusted hidden object ID.
    cue = ContactAssociations._key(sensor)
    if (not isinstance(direction, dict) or set(direction) != {
            "schema", "tick", "world_revision", "octant", "source",
        } or direction.get("schema") != BEARING_SCHEMA
            or direction.get("source") != "synthetic_direction_quantization_not_camera"
            or direction.get("tick") != sensor["tick"]
            or direction.get("world_revision") != sensor["world_revision"]):
        raise CuriosityError("invalid or stale egocentric direction")
    sector = direction.get("octant")
    if cue is None:
        if sector is not None:
            raise CuriosityError("direction leaked hidden object with no visible cue")
    elif type(sector) is not int or not 0 <= sector <= 7:
        raise CuriosityError("visible cue needs 0..7 egocentric sector")
    return cue, sector


def _contact(sensor, result):
    if result is None:
        return None
    if (not isinstance(result, dict) or set(result) != {
            "contact", "measured_deflection", "instrument", "source_tick",
        } or type(result["contact"]) is not bool
            or result["instrument"] != "synthetic_probe_not_material_classifier"
            or type(result["source_tick"]) is not int
            or result["source_tick"] != sensor["tick"]):
        raise CuriosityError("invalid measurement provenance")
    if not result["contact"]:
        if result["measured_deflection"] is not None:
            raise CuriosityError("noncontact probe disclosed material response")
        return None
    val = result["measured_deflection"]
    if not sensor["visible"] or not _finite(val) or not 0 <= val <= 1:
        raise CuriosityError("invalid physical contact evidence")
    return float(val)


class ProvisionalObjectMemory:
    """Local, evidence-counted identity hypotheses, **not** verified objects."""

    def __init__(self, *, contact_guard=False):
        if type(contact_guard) is not bool:
            raise CuriosityError("contact guard must be true or false")
        self.contact_guard = contact_guard
        self.tracks = {}
        self.next_track = 1
        self.last_tick = -1
        self.accepted_sightings = 0
        self.touch_updates = 0

    def snapshot(self):
        result = {
            "schema": SCHEMA, "tracks": copy.deepcopy(self.tracks),
            "next_track": self.next_track, "last_tick": self.last_tick,
            "accepted_sightings": self.accepted_sightings,
            "touch_updates": self.touch_updates,
        }
        # The default v1 snapshot is bit-for-bit unchanged. Opt-in guarded
        # memory includes an explicit mode marker to prevent silent replay.
        if self.contact_guard:
            result["contact_guard"] = True
        return result

    @classmethod
    def from_snapshot(cls, snap):
        keys = {
            "schema", "tracks", "next_track", "last_tick",
            "accepted_sightings", "touch_updates",
        }
        if (not isinstance(snap, dict)
                or set(snap) not in (keys, keys | {"contact_guard"})
                or ("contact_guard" in snap and snap["contact_guard"] is not True)
                or snap["schema"] != SCHEMA or not isinstance(snap["tracks"], dict)
                or len(snap["tracks"]) > MAX_TRACKS
                or type(snap["next_track"]) is not int
                or not 1 <= snap["next_track"] <= MAX_TRACKS + 1
                or type(snap["last_tick"]) is not int
                or not -1 <= snap["last_tick"] < MAX_FRAMES
                or type(snap["accepted_sightings"]) is not int
                or not 0 <= snap["accepted_sightings"] <= MAX_FRAMES
                or type(snap["touch_updates"]) is not int
                or not 0 <= snap["touch_updates"] <= snap["accepted_sightings"]):
            raise CuriosityError("invalid object-memory snapshot")
        accepted = touches = 0
        expected_ids = {f"h{i:04d}" for i in range(1, snap["next_track"])}
        if set(snap["tracks"]) != expected_ids:
            raise CuriosityError("hypothesis numbering discontinuity")
        for tr in snap["tracks"].values():
            if not isinstance(tr, dict) or set(tr) != {
                "cue", "estimate", "first_tick", "last_tick",
                "sightings", "contacts", "mean_deflection",
            }:
                raise CuriosityError("invalid object hypothesis")
            rgb = tr["cue"].split(":") if isinstance(tr["cue"], str) else []
            if len(rgb) != 3 or any(
                not x.isdecimal() or str(int(x)) != x or not 0 <= int(x) <= 255
                for x in rgb
            ):
                raise CuriosityError("invalid hypothesis sensory cue")
            pos = tr["estimate"]
            if (not isinstance(pos, list) or len(pos) != 2
                    or not all(_finite(x) and -1000 <= x <= 1000 for x in pos)
                    or type(tr["first_tick"]) is not int
                    or type(tr["last_tick"]) is not int
                    or not 0 <= tr["first_tick"] <= tr["last_tick"] <= snap["last_tick"]
                    or type(tr["sightings"]) is not int or not 1 <= tr["sightings"] <= MAX_FRAMES
                    or type(tr["contacts"]) is not int or not 0 <= tr["contacts"] <= tr["sightings"]):
                raise CuriosityError("invalid hypothesis position or evidence")
            if tr["contacts"] == 0:
                if tr["mean_deflection"] is not None:
                    raise CuriosityError("unmeasured material was preloaded")
            elif not _finite(tr["mean_deflection"]) or not 0 <= tr["mean_deflection"] <= 1:
                raise CuriosityError("invalid empirical material estimate")
            accepted += tr["sightings"]
            touches += tr["contacts"]
        if accepted != snap["accepted_sightings"] or touches != snap["touch_updates"]:
            raise CuriosityError("inconsistent object evidence totals")
        fresh = cls(contact_guard=snap.get("contact_guard", False))
        fresh.tracks = copy.deepcopy(snap["tracks"])
        fresh.next_track = snap["next_track"]
        fresh.last_tick = snap["last_tick"]
        fresh.accepted_sightings = snap["accepted_sightings"]
        fresh.touch_updates = snap["touch_updates"]
        return fresh

    def observe(self, sensor, direction, observer_xy, contact_result=None):
        cue, sector = _bearing(sensor, direction)
        if (not isinstance(observer_xy, (tuple, list)) or len(observer_xy) != 2
                or not all(_finite(x) and 0 <= x <= 1000 for x in observer_xy)):
            raise CuriosityError("valid proprioceptive position required")
        tick = sensor["tick"]
        if tick != self.last_tick + 1 or tick >= MAX_FRAMES:
            raise CuriosityError("discontinuous object-memory tick")
        measurement = _contact(sensor, contact_result)
        if measurement is not None and cue is None:
            raise CuriosityError("contact without an observed numeric cue")

        if cue is None:
            self.last_tick = tick
            return {
                "status": "no_visible_signal", "hypothesis": None,
                "estimated_position": None, "candidates": [],
                "sightings": 0, "touches": 0, "mean_deflection": None,
                "gap_ticks": None, "identity_verified": False,
                "basis": "numeric_local_perception_only",
            }

        distance_m = (sensor["range_bucket_10cm"] + 0.5) * 0.1
        theta = sector * (math.pi / 4)
        estimate = [
            round(observer_xy[0] + distance_m * math.cos(theta), 6),
            round(observer_xy[1] + distance_m * math.sin(theta), 6),
        ]
        candidates = sorted(
            key for key, tr in self.tracks.items()
            if tr["cue"] == cue
            and math.hypot(estimate[0] - tr["estimate"][0],
                           estimate[1] - tr["estimate"][1]) <= ASSOCIATION_RADIUS_M
        )
        self.last_tick = tick
        if len(candidates) > 1:
            # Fail closed on conflicting identity candidates; don't assign
            # measured material or blend unrelated objects arbitrarily.
            return {
                "status": "ambiguous_identity", "hypothesis": None,
                "estimated_position": estimate, "candidates": candidates,
                "sightings": 0, "touches": 0, "mean_deflection": None,
                "gap_ticks": None, "identity_verified": False,
                "basis": "multiple_compatible_local_hypotheses",
            }
        if len(candidates) == 1 and self.contact_guard and measurement is not None:
            prior = self.tracks[candidates[0]]
            if (prior["contacts"] > 0
                    and abs(measurement - prior["mean_deflection"])
                    > CONTACT_CONFLICT_THRESHOLD):
                # An already learned contact response contradicts this
                # location-based identity. Without extra sensory evidence,
                # abstain; do not corrupt the old object's measurements.
                return {
                    "status": "contact_conflict_abstention", "hypothesis": None,
                    "estimated_position": estimate, "candidates": candidates,
                    "sightings": 0, "touches": 0, "mean_deflection": None,
                    "gap_ticks": None, "identity_verified": False,
                    "basis": "observed_contact_contradicts_local_prediction",
                }
        if not candidates:
            if len(self.tracks) >= MAX_TRACKS:
                raise CuriosityError("provisional object-memory capacity exhausted")
            key = f"h{self.next_track:04d}"
            self.next_track += 1
            self.tracks[key] = {
                "cue": cue, "estimate": estimate,
                "first_tick": tick, "last_tick": tick,
                "sightings": 0, "contacts": 0, "mean_deflection": None,
            }
            status = "new_hypothesis"
            gap = None
        else:
            key = candidates[0]
            gap = tick - self.tracks[key]["last_tick"]
            status = "possible_reencounter" if gap > 1 else "continuous_candidate"
        tr = self.tracks[key]
        tr["sightings"] += 1
        n = tr["sightings"]
        if n > MAX_FRAMES:
            raise CuriosityError("object sightings budget exhausted")
        tr["estimate"] = [
            round(tr["estimate"][i] + (estimate[i] - tr["estimate"][i]) / n, 6)
            for i in range(2)
        ]
        tr["last_tick"] = tick
        self.accepted_sightings += 1
        if measurement is not None:
            tr["contacts"] += 1
            old = tr["mean_deflection"]
            tr["mean_deflection"] = measurement if old is None else (
                old + (measurement - old) / tr["contacts"]
            )
            self.touch_updates += 1
        return {
            "status": status, "hypothesis": key,
            "estimated_position": estimate,
            "candidates": candidates,
            "sightings": tr["sightings"],
            "touches": tr["contacts"],
            "mean_deflection": tr["mean_deflection"],
            "gap_ticks": gap, "identity_verified": False,
            "basis": "quantized_rgb_range_azimuth_and_self_position",
        }
