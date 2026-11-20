"""Bounded empirical movement affordances for a *synthetic* numeric room.

An untrained organism selects tiny world-fixture actions using only current
numeric proximity/heartbeat and its own *previous measured blocked outcomes*.
This is not a cognitive brain, motivation, object identity, MuJoCo, physical
motor actuation, or integration with the canonical Aquarium's event journal.
"""

from __future__ import annotations

import copy
import math
import re

SENSOR_SCHEMA = "mojum.sensor.synthetic-near-field/1"
SCHEMA = "mojum.exploration.affordance/1"
INTENT_SCHEMA = "mojum.exploration.world-intent/1"
ACTIONS = ("x+", "x-", "y+", "y-")
DELTA = 0.02
MAX_TICKS = 3999
MAX_CONTEXTS = 128
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT = re.compile(r"(?:0|1)\|(?:none|-|[0-9]{1,3}:[0-9]{1,3}:[0-9]{1,3})\|(?:-|[0-6])\Z")


class AffordanceError(ValueError):
    """Malformed sensory/outcome evidence or impossible learned state."""


def _finite_number(v):
    if type(v) not in (int, float):
        return False
    try:
        return math.isfinite(v)
    except (OverflowError, ValueError, TypeError):
        return False


def _cue(sensor, pulse):
    if (type(pulse) is not bool or not isinstance(sensor, dict)
            or set(sensor) != {
                "schema", "tick", "world_revision", "visible",
                "reflectance_rgb", "range_bucket_10cm", "source",
            }
            or sensor["schema"] != SENSOR_SCHEMA
            or sensor["source"] != "synthetic_local_proximity_not_camera"
            or type(sensor["tick"]) is not int
            or not 0 <= sensor["tick"] < MAX_TICKS
            or type(sensor["world_revision"]) is not str
            or _HEX.fullmatch(sensor["world_revision"]) is None
            or type(sensor["visible"]) is not bool):
        raise AffordanceError("invalid synthetic numeric sensory boundary")
    if not sensor["visible"]:
        if sensor["reflectance_rgb"] is not None or sensor["range_bucket_10cm"] is not None:
            raise AffordanceError("invisible sensor leaks an object cue")
        return f"{int(pulse)}|none|-"
    rgb, distance = sensor["reflectance_rgb"], sensor["range_bucket_10cm"]
    if (type(rgb) is not list or len(rgb) != 3
            or any(type(v) is not int or not 0 <= v <= 255 for v in rgb)
            or type(distance) is not int or not 0 <= distance <= 6):
        raise AffordanceError("invalid visible numeric observation")
    return f"{int(pulse)}|{':'.join(str(v) for v in rgb)}|{distance}"


def _check_key(key):
    if type(key) is not str or _CONTEXT.fullmatch(key) is None:
        return False
    pulse, rgb, distance = key.split("|")
    if rgb == "none":
        return distance == "-"
    if distance == "-":
        return False
    values = rgb.split(":")
    return len(values) == 3 and all(
        str(int(v)) == v and 0 <= int(v) <= 255 for v in values
    )


def _validate_result(result, tick):
    if (not isinstance(result, dict) or set(result) != {
                "tick", "revision", "position", "blocked",
                "provenance", "physical_contact",
            }
            or type(result["tick"]) is not int or result["tick"] != tick + 1
            or type(result["revision"]) is not str
            or _HEX.fullmatch(result["revision"]) is None
            or type(result["blocked"]) is not bool
            or result["provenance"] != "deterministic-point-room-fixture/v1"
            or result["physical_contact"] is not False
            or type(result["position"]) is not list
            or len(result["position"]) != 2
            or not all(_finite_number(v) and 0 <= v <= 1000
                       for v in result["position"])):
        raise AffordanceError("invalid or stale synthetic world outcome")


class AffordanceMemory:
    """Small per-numeric-context table of observed blocked/successful motions.

    Decision tie-breaking is deterministic. No reward, target coordinates,
    spectator-only object labels, future observations or preset preferences.
    The table must be cloned before use in a transactional event append;
    record() mutates only the candidate and the owner adopts it after CAS.
    """

    def __init__(self):
        self.table = {}
        self.last_tick = -1
        self.updates = 0

    def snapshot(self):
        return {"schema": SCHEMA, "table": copy.deepcopy(self.table),
                "last_tick": self.last_tick, "updates": self.updates}

    @classmethod
    def from_snapshot(cls, snap):
        if (type(snap) is not dict or set(snap) != {
                "schema", "table", "last_tick", "updates",
            } or snap["schema"] != SCHEMA
            or type(snap["last_tick"]) is not int
            or not -1 <= snap["last_tick"] < MAX_TICKS
            or type(snap["updates"]) is not int
            or not 0 <= snap["updates"] <= snap["last_tick"] + 1
            or type(snap["table"]) is not dict
            or len(snap["table"]) > MAX_CONTEXTS):
            raise AffordanceError("invalid affordance snapshot")
        total = 0
        for key, actions in snap["table"].items():
            if (not _check_key(key) or type(actions) is not dict
                    or not actions or not set(actions).issubset(ACTIONS)):
                raise AffordanceError("invalid affordance context")
            for record in actions.values():
                if (type(record) is not dict
                        or set(record) != {"samples", "blocked"}
                        or type(record["samples"]) is not int
                        or not 1 <= record["samples"] <= MAX_TICKS
                        or type(record["blocked"]) is not int
                        or not 0 <= record["blocked"] <= record["samples"]):
                    raise AffordanceError("invalid empirical affordance counts")
                total += record["samples"]
        if total != snap["updates"]:
            raise AffordanceError("impossible affordance evidence totals")
        m = cls()
        m.table = copy.deepcopy(snap["table"])
        m.last_tick = snap["last_tick"]
        m.updates = snap["updates"]
        return m

    def choose(self, sensor, pulse, *, enabled=True):
        """Propose a bounded world-fixture displacement without mutating state."""
        context = _cue(sensor, pulse)
        tick = sensor["tick"]
        if tick != self.last_tick + 1:
            raise AffordanceError("discontinuous developmental tick")
        if type(enabled) is not bool:
            raise AffordanceError("enabled must be bool")
        action = "rest"
        if enabled:
            # Learnt feasibility, not reward or fixed destination:
            # one observed unblocked motion is tentatively preferred,
            # a blocked motion is suppressed, unseen alternatives remain open.
            records = self.table.get(context, {})
            scores = {}
            for name in ACTIONS:
                row = records.get(name)
                if row is None:
                    scores[name] = 0.0
                else:
                    good = row["samples"] - row["blocked"]
                    scores[name] = (good - row["blocked"]) / (row["samples"] + 1)
            # Rotate only ties. An experienced successful action wins over
            # unseen; an observed blocked action loses to unseen.
            rotation = tick % len(ACTIONS)
            ranked = ACTIONS[rotation:] + ACTIONS[:rotation]
            action = max(ranked, key=lambda name: scores[name])
        offsets = {"rest": (0.0, 0.0), "x+": (DELTA, 0.0),
                   "x-": (-DELTA, 0.0), "y+": (0.0, DELTA),
                   "y-": (0.0, -DELTA)}
        dx, dy = offsets[action]
        return {"schema": INTENT_SCHEMA, "tick": tick,
                "world_revision": sensor["world_revision"],
                "action": action, "dx": dx, "dy": dy,
                "capability": "SYNTHETIC_POINT_ROOM_ONLY",
                "basis": "numeric_prior_blockage_evidence_not_external_goal",
                "identity_verified": False}

    def record(self, sensor, pulse, intent, result):
        """Accept one observed world outcome after caller-controlled CAS.

        Reject malformed, stale or unauthorized action/output before mutation.
        Caller must recompute the actual world transition independently; this
        model cannot authenticate arbitrary caller-supplied result booleans.
        """
        context = _cue(sensor, pulse)
        if not isinstance(intent, dict):
            raise AffordanceError("invalid synthetic movement intent")
        expected = self.choose(sensor, pulse,
                               enabled=(intent.get("action") != "rest"))
        if intent != expected:
            raise AffordanceError("untrusted or stale synthetic movement intent")
        _validate_result(result, sensor["tick"])
        if intent["action"] != "rest":
            if context not in self.table and len(self.table) >= MAX_CONTEXTS:
                raise AffordanceError("affordance context capacity exhausted")
            record = self.table.setdefault(context, {}).setdefault(
                intent["action"], {"samples": 0, "blocked": 0},
            )
            record["samples"] += 1
            record["blocked"] += int(result["blocked"])
            self.updates += 1
        self.last_tick = sensor["tick"]
        return {"schema": SCHEMA, "tick": self.last_tick,
                "action": intent["action"], "blocked": result["blocked"],
                "empirical_updates": self.updates,
                "basis": "measured_synthetic_world_outcome_only"}
