"""An explicit synthetic controller boundary for the canonical Aquarium.

This is deliberately a controller, not a neural brain.  It adapts the
existing replayable :class:`MotorPlay` policy to a typed current-tick input
boundary and returns a bounded virtual intent.  The adapter contains no
hidden toy identity, material response, room geometry, future observation,
network call or physical actuator access.

The policy state remains the existing ``MotorPlay`` snapshot.  Keeping this
boundary stateless makes the committed motor snapshot the sole source of
truth, while the returned wire record makes the sensor -> intent -> safety
boundary explicit in developmental Aquarium traces.
"""

from __future__ import annotations

import copy
import math

from .developmental import MOVES, REST, STEP, DevelopmentalError, MotorPlay


SCHEMA = "mojum.neural.developmental-controller/1"
STATUS = "SYNTHETIC_DEVELOPMENTAL_CONTROLLER"
_ACTIONS = frozenset(move[0] for move in (*MOVES, REST))


class DevelopmentalBrainError(ValueError):
    """Reject malformed controller input before proposing a virtual intent."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _revision(value, field):
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise DevelopmentalBrainError(f"{field} must be a lowercase SHA-256 revision")
    return value


def _interoception(value, *, expected_time_ns):
    if (type(expected_time_ns) is not int or expected_time_ns < 0
            or not isinstance(value, dict)
            or set(value) != {"kind", "heartbeat", "observed_at_ns"}
            or value["kind"] != "interoception"
            or type(value["observed_at_ns"]) is not int
            or value["observed_at_ns"] != expected_time_ns):
        raise DevelopmentalBrainError("invalid interoception input")
    heartbeat = value["heartbeat"]
    if (not isinstance(heartbeat, dict)
            or set(heartbeat) != {"beat_index", "phase_milli", "pulse", "synthetic"}
            or type(heartbeat["beat_index"]) is not int
            or heartbeat["beat_index"] < 0
            or type(heartbeat["phase_milli"]) is not int
            or not 0 <= heartbeat["phase_milli"] < 1000
            or type(heartbeat["pulse"]) is not bool
            or heartbeat["synthetic"] is not True):
        raise DevelopmentalBrainError("invalid synthetic heartbeat input")
    return {
        "kind": "interoception",
        "heartbeat": copy.deepcopy(heartbeat),
        "observed_at_ns": value["observed_at_ns"],
    }


def _proprioception(value, tick):
    if (not isinstance(value, dict)
            or set(value) != {"tick", "position", "world_revision"}
            or type(value["tick"]) is not int
            or value["tick"] != tick
            or (not isinstance(value["position"], list)
                or len(value["position"]) != 2
                or not all(_finite(item) for item in value["position"]))):
        raise DevelopmentalBrainError("invalid current proprioception input")
    return {
        "tick": tick,
        "position": [float(item) for item in value["position"]],
        "world_revision": _revision(value["world_revision"], "world_revision"),
    }


def _near_field(value, *, tick, world_revision):
    """Copy only the numeric sensor contract; hidden toy metadata is rejected."""
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) != {
                "schema", "tick", "world_revision", "visible",
                "reflectance_rgb", "range_bucket_10cm", "source",
            }
            or value["schema"] != "mojum.sensor.synthetic-near-field/1"
            or type(value["tick"]) is not int or value["tick"] < 0
            or value["source"] != "synthetic_local_proximity_not_camera"
            or type(value["visible"]) is not bool):
        raise DevelopmentalBrainError("invalid numeric near-field input")
    _revision(value["world_revision"], "near-field world_revision")
    if value["tick"] != tick or value["world_revision"] != world_revision:
        raise DevelopmentalBrainError("near-field observation is stale, future or from another world revision")
    rgb, bucket = value["reflectance_rgb"], value["range_bucket_10cm"]
    if not value["visible"]:
        if rgb is not None or bucket is not None:
            raise DevelopmentalBrainError("invisible near-field input discloses an object")
    elif (not isinstance(rgb, list) or len(rgb) != 3
          or any(type(item) is not int or not 0 <= item <= 255 for item in rgb)
          or type(bucket) is not int or not 0 <= bucket <= 6):
        raise DevelopmentalBrainError("invalid visible near-field input")
    return {
        "schema": value["schema"],
        "tick": value["tick"],
        "world_revision": value["world_revision"],
        "visible": value["visible"],
        "reflectance_rgb": copy.deepcopy(rgb),
        "range_bucket_10cm": bucket,
        "source": value["source"],
    }


def _action_names(value, field):
    if type(value) not in (tuple, list):
        raise DevelopmentalBrainError(f"{field} must be a sequence of action names")
    if any(type(item) is not str or item not in _ACTIONS for item in value):
        raise DevelopmentalBrainError(f"{field} contains an unknown action")
    if len(set(value)) != len(value):
        raise DevelopmentalBrainError(f"{field} contains duplicate actions")
    return tuple(value)


class DevelopmentalBrain:
    """Stateless typed adapter over the committed synthetic motor policy.

    ``motor`` is a speculative clone created by ``Aquarium``.  ``choose``
    only reads it, so the caller can commit the resulting journal event before
    adopting the motor state and world in RAM.
    """

    def decide(
        self, motor: MotorPlay, *, tick, interoception, proprioception,
        near_field=None, avoid=(), prefer=(), dt_ns=10_000_000,
        expected_at_ns=None,
    ):
        if (type(tick) is not int or tick < 0 or type(motor) is not MotorPlay
                or type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000):
            raise DevelopmentalBrainError("invalid controller clock or policy")
        expected_time_ns = tick * dt_ns if expected_at_ns is None else expected_at_ns
        if (type(expected_time_ns) is not int or expected_time_ns != tick * dt_ns):
            raise DevelopmentalBrainError("invalid current virtual time")
        current_interoception = _interoception(
            interoception, expected_time_ns=expected_time_ns,
        )
        current_proprioception = _proprioception(proprioception, tick)
        current_near_field = _near_field(
            near_field, tick=tick, world_revision=current_proprioception["world_revision"]
        )
        avoid = _action_names(avoid, "avoid")
        prefer = _action_names(prefer, "prefer")
        try:
            intent = motor.choose(tick, avoid=avoid, prefer=prefer)
        except DevelopmentalError as exc:
            raise DevelopmentalBrainError("motor policy rejected controller input") from exc
        if (not isinstance(intent, dict)
                or intent.get("kind") not in _ACTIONS
                or type(intent.get("dx")) is not float
                or type(intent.get("dy")) is not float
                or not _finite(intent["dx"]) or not _finite(intent["dy"])
                or math.hypot(intent["dx"], intent["dy"]) > STEP):
            raise DevelopmentalBrainError("motor policy emitted an unsafe virtual intent")
        return {
            "schema": SCHEMA,
            "status": STATUS,
            "input": {
                "interoception": current_interoception,
                "proprioception": current_proprioception,
                "near_field": current_near_field,
                "numeric_guidance": {
                    "avoid": list(avoid),
                    "prefer": list(prefer),
                },
            },
            "action": copy.deepcopy(intent),
            "action_source": "committed_prior_outcomes_and_numeric_guidance",
            "safety": {
                "domain": "synthetic_point_room",
                "max_step_m": STEP,
                "external_override": False,
                "real_actuation": False,
            },
        }
