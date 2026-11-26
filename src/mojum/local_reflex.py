"""Opt-in deterministic local joint reflexes for a PUBLIC_SYNTHETIC CPU fixture.

Positions and velocities use the existing virtual-physics observation contract.
Torque, load, support and hazard are caller-supplied proxies, not physical truth.
Every returned position target passes the supplied MotorGate. There is no
actuator, neural/spinal scheduler, MuJoCo driver or calibrated physiology here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
import re

from .motor_gate import MotorGate, MotorIntent
from .physics_observation import validate_virtual_physics_observation

OBSERVATION_SCHEMA = "mojum.local-reflex.observation/1"
INTENT_SCHEMA = "mojum.local-reflex.intent/1"
FRAME_SCHEMA = "mojum.local-reflex.frame/1"
SNAPSHOT_SCHEMA = "mojum.local-reflex.snapshot/1"
MAX_JOINTS = 64
MAX_CONTACTS = 256
MAX_COUNTER = 2**63 - 1
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class ReflexError(ValueError):
    """Malformed, stale or incompatible synthetic controller input."""


def _keys(value, expected, name):
    # Check cardinality and exact key types before hashing/comparing user keys.
    if (type(value) is not dict or len(value) != len(expected)
            or any(type(key) is not str for key in value)
            or set(value) != expected):
        raise ReflexError(f"invalid {name} fields")


def _name(value, label):
    if type(value) is not str or len(value) > 64 or not _NAME.fullmatch(value):
        raise ReflexError(f"invalid {label}")
    return value


def _int(value, label, low=0, high=MAX_COUNTER):
    if type(value) is not int or not low <= value <= high:
        raise ReflexError(f"invalid {label}")
    return value


def _number(value, label, low=-1_000_000.0, high=1_000_000.0):
    if type(value) not in (int, float):
        raise ReflexError(f"invalid {label}")
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite or not low <= value <= high:
        raise ReflexError(f"invalid {label}")
    return float(value)


def _bool(value, label):
    if type(value) is not bool:
        raise ReflexError(f"invalid {label}")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class JointReflex:
    """Engineering fixture settings; positive target direction is flexion.

    ``partner`` is a symmetric contralateral link. Its support response moves
    toward this joint's neutral pose, only while contact AND support are true.
    Step limits are radians per accepted observation, not actuator speed limits.
    """

    joint: str
    neutral_rad: float = 0.0
    withdrawal_direction: int = 1
    partner: str | None = None
    max_step_rad: float = 0.05
    withdrawal_step_rad: float = 0.05
    stretch_band_rad: float = 0.4
    velocity_threshold_rad_s: float = 1.0
    damping_s: float = 0.05
    torque_limit_nm: float = 10.0
    load_limit_n: float = 100.0


class LocalReflexController:
    """Single-threaded local arbitration; rejected steps do not advance state.

    A missing cognitive packet retains the last accepted intention until its
    exclusive expiry. Local circuits never require a cognitive refresh. Invalid
    observations produce no commands; callers must not reuse an old frame.
    """

    def __init__(self, gate: MotorGate, *, body_id: str,
                 joints: tuple[JointReflex, ...],
                 max_observation_age_ns: int = 50_000_000,
                 max_intent_ttl_ns: int = 500_000_000):
        if type(gate) is not MotorGate:
            raise ReflexError("an existing MotorGate is required")
        self._gate = gate
        self._body_id = _name(body_id, "body id")
        self._revision = _int(gate.revision, "gate revision")
        self._max_age = _int(max_observation_age_ns, "observation age budget", 1, 10**9)
        self._max_ttl = _int(max_intent_ttl_ns, "intent TTL budget", 1, 10**9)
        if type(joints) is not tuple or not 1 <= len(joints) <= MAX_JOINTS:
            raise ReflexError("joints must be a bounded nonempty tuple")
        checked = {}
        for config in joints:
            if type(config) is not JointReflex:
                raise ReflexError("JointReflex settings required")
            try:
                raw = {field.name: getattr(config, field.name) for field in fields(config)}
            except AttributeError as exc:
                raise ReflexError("incomplete joint settings") from exc
            joint = _name(raw["joint"], "joint id")
            if joint in checked or joint not in gate.limits:
                raise ReflexError("duplicate or unknown configured joint")
            lo, hi = gate.limits[joint]
            _number(lo, "joint lower limit")
            _number(hi, "joint upper limit")
            raw["neutral_rad"] = _number(raw["neutral_rad"], "neutral position", lo, hi)
            if type(raw["withdrawal_direction"]) is not int or raw["withdrawal_direction"] not in (-1, 1):
                raise ReflexError("withdrawal direction must be -1 or 1")
            if raw["partner"] is not None:
                _name(raw["partner"], "partner")
            for key in ("max_step_rad", "withdrawal_step_rad", "stretch_band_rad",
                        "velocity_threshold_rad_s", "damping_s", "torque_limit_nm", "load_limit_n"):
                raw[key] = _number(raw[key], key, 0.0)
                if raw[key] <= 0:
                    raise ReflexError(f"{key} must be positive")
            if (raw["withdrawal_step_rad"] > raw["max_step_rad"]
                    or raw["max_step_rad"] > hi - lo
                    or raw["stretch_band_rad"] > hi - lo or raw["damping_s"] > 1):
                raise ReflexError("inconsistent reflex bounds")
            checked[joint] = JointReflex(**raw)
        for joint, config in checked.items():
            if config.partner is not None and (config.partner == joint
                    or config.partner not in checked or checked[config.partner].partner != joint):
                raise ReflexError("contralateral partners must be distinct and symmetric")
        self._joints = dict(sorted(checked.items()))
        self._profile_sha256 = _digest({
            "body_id": self._body_id, "revision": self._revision,
            "max_observation_age_ns": self._max_age, "max_intent_ttl_ns": self._max_ttl,
            "joints": [asdict(c) for c in self._joints.values()],
            "limits": {joint: list(gate.limits[joint]) for joint in self._joints},
        })
        self._next_tick = 0
        self._last_sample_ns = None
        self._last_evaluated_ns = None
        self._last_intent_sequence = -1
        self._intent = None

    def _check_gate(self):
        if self._gate.revision != self._revision:
            raise ReflexError("gate revision changed; construct a new compatible controller")

    def _observation(self, value, now):
        _keys(value, {"schema", "body_id", "revision", "physics", "proprioception"}, "observation")
        if type(value["schema"]) is not str or value["schema"] != OBSERVATION_SCHEMA:
            raise ReflexError("unsupported observation schema")
        if _name(value["body_id"], "body id") != self._body_id:
            raise ReflexError("foreign body observation")
        if _int(value["revision"], "observation revision") != self._revision:
            raise ReflexError("stale observation revision")
        physics = value["physics"]
        _keys(physics, {"schema", "tick", "time_ns", "backend", "synthetic",
                       "physical_contact", "physical_actuation", "joints", "virtual_contacts"}, "physics")
        if type(physics["joints"]) is not list or len(physics["joints"]) != len(self._joints):
            raise ReflexError("all controlled joint measurements are required")
        if type(physics["virtual_contacts"]) is not list or len(physics["virtual_contacts"]) > MAX_CONTACTS:
            raise ReflexError("too many virtual contacts")
        for item in physics["joints"]:
            _keys(item, {"id", "position_rad", "velocity_rad_s"}, "physics joint")
        for item in physics["virtual_contacts"]:
            _keys(item, {"geom_a", "geom_b", "normal_force_n"}, "virtual contact")
        try:
            physics = validate_virtual_physics_observation(physics)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ReflexError("invalid virtual physics observation") from exc
        tick, sample_ns = physics["tick"], physics["time_ns"]
        if tick != self._next_tick or tick == MAX_COUNTER:
            raise ReflexError("observation tick must be the next bounded tick")
        if self._last_sample_ns is not None and sample_ns <= self._last_sample_ns:
            raise ReflexError("observation timestamps must increase")
        if not sample_ns <= now <= sample_ns + self._max_age:
            raise ReflexError("stale or future observation")
        if self._last_evaluated_ns is not None and now < self._last_evaluated_ns:
            raise ReflexError("evaluation time cannot move backwards")
        measured = {item["id"]: item for item in physics["joints"]}
        if set(measured) != set(self._joints):
            raise ReflexError("observation joint set mismatch")
        for joint, item in measured.items():
            lo, hi = self._gate.limits[joint]
            _number(item["position_rad"], "measured position", lo, hi)
        proprio = value["proprioception"]
        if type(proprio) is not list or len(proprio) != len(self._joints):
            raise ReflexError("all proprioceptive samples are required")
        checked = {}
        for item in proprio:
            _keys(item, {"joint", "torque_nm", "load_n", "contact", "support", "hazard"}, "proprioception")
            joint = _name(item["joint"], "proprioceptive joint")
            if joint not in self._joints or joint in checked:
                raise ReflexError("duplicate or unknown proprioceptive joint")
            support, contact = _bool(item["support"], "support"), _bool(item["contact"], "contact")
            if support and not contact:
                raise ReflexError("support requires contact")
            checked[joint] = {
                **measured[joint], "torque_nm": _number(item["torque_nm"], "torque"),
                "load_n": _number(item["load_n"], "load", 0.0),
                "contact": contact, "support": support,
                "hazard": _bool(item["hazard"], "hazard"),
            }
        return tick, sample_ns, checked

    def _admit_intent(self, value):
        _keys(value, {"schema", "body_id", "revision", "sequence", "issued_at_ns",
                      "expires_at_ns", "targets"}, "intent")
        if type(value["schema"]) is not str or value["schema"] != INTENT_SCHEMA:
            raise ReflexError("unsupported intent schema")
        if _name(value["body_id"], "intent body id") != self._body_id:
            raise ReflexError("foreign body intention")
        revision = _int(value["revision"], "intent revision")
        sequence = _int(value["sequence"], "intent sequence")
        issued = _int(value["issued_at_ns"], "intent issue time")
        expires = _int(value["expires_at_ns"], "intent expiry")
        if not 0 < expires - issued <= self._max_ttl:
            raise ReflexError("invalid intent lifetime")
        targets = value["targets"]
        if type(targets) is not dict or not 1 <= len(targets) <= len(self._joints):
            raise ReflexError("intent targets must be a bounded nonempty mapping")
        checked = {}
        for joint, target in targets.items():
            _name(joint, "intent joint")
            if joint not in self._joints:
                raise ReflexError("unknown intent joint")
            lo, hi = self._gate.limits[joint]
            checked[joint] = _number(target, "intent target", lo, hi)
        return {"schema": INTENT_SCHEMA, "body_id": self._body_id,
                "revision": revision, "sequence": sequence, "issued_at_ns": issued,
                "expires_at_ns": expires, "targets": dict(sorted(checked.items()))}

    def step(self, observation, intent=None, *, evaluated_at_ns: int):
        """Arbitrate one observation atomically and return detached proposals.

        Expired/stale-revision/replayed well-formed cognitive packets are ignored
        and cannot block local reflexes. Malformed packets reject the whole step.
        Intent expiry is exclusive; observation age uses an inclusive budget.
        """
        self._check_gate()
        now = _int(evaluated_at_ns, "evaluation time")
        tick, sample_ns, samples = self._observation(observation, now)
        active = self._intent
        sequence = self._last_intent_sequence
        intent_status = "absent" if active is None else "retained"
        if active is not None and now >= active["expires_at_ns"]:
            active, intent_status = None, "expired"
        if intent is not None:
            packet = self._admit_intent(intent)
            if packet["issued_at_ns"] > now:
                raise ReflexError("future cognitive intention")
            if packet["revision"] != self._revision:
                intent_status = "stale_revision"
            elif packet["sequence"] <= sequence:
                intent_status = "replayed"
            else:
                sequence = packet["sequence"]
                if now >= packet["expires_at_ns"]:
                    # A delayed packet is not an implicit cancellation of an
                    # already accepted, still-live intention. Remember its
                    # sequence so it cannot later be replayed as a refresh.
                    intent_status = "expired"
                else:
                    active, intent_status = packet, "accepted"
        commands = []
        estop = self._gate.estop_latched
        if not estop:
            for joint, config in self._joints.items():
                sample = samples[joint]
                position, velocity = sample["position_rad"], sample["velocity_rad_s"]
                target, source = position, "hold"
                if (abs(sample["torque_nm"]) >= config.torque_limit_nm
                        or sample["load_n"] >= config.load_limit_n):
                    source = "force_limit"
                elif sample["hazard"]:
                    target = position + config.withdrawal_direction * config.withdrawal_step_rad
                    source = "withdrawal"
                elif (config.partner is not None and samples[config.partner]["hazard"]
                        and sample["contact"] and sample["support"]):
                    target, source = config.neutral_rad, "crossed_support"
                elif abs(position - config.neutral_rad) > config.stretch_band_rad:
                    target, source = config.neutral_rad, "stretch"
                elif abs(velocity) > config.velocity_threshold_rad_s:
                    target, source = position - velocity * config.damping_s, "perturbation"
                elif active is not None and joint in active["targets"]:
                    target, source = active["targets"][joint], "cognitive"
                # Local proposals may saturate at a configured position limit;
                # external targets were strictly checked before arbitration.
                lo, hi = self._gate.limits[joint]
                target = min(hi, max(lo, min(position + config.max_step_rad,
                                           max(position - config.max_step_rad, target))))
                target = self._gate.validate(MotorIntent(joint, target, self._revision))
                delta = target - position
                activation = min(1.0, abs(delta) / config.max_step_rad)
                commands.append({
                    "joint": joint, "target_rad": target, "revision": self._revision,
                    "source": source,
                    "flexor": activation if delta > 0 else 0.0,
                    "extensor": activation if delta < 0 else 0.0,
                })
        # A gate rejection or revision change returns no partial frame and
        # commits none of the controller's pending clock/intent updates.
        self._check_gate()
        if self._gate.estop_latched != estop:
            raise PermissionError("e-stop changed during arbitration")
        frame = {"schema": FRAME_SCHEMA, "body_id": self._body_id,
                 "revision": self._revision, "tick": tick, "sample_time_ns": sample_ns,
                 "evaluated_at_ns": now, "visibility": "PUBLIC_SYNTHETIC",
                 "physical_actuation": False, "estop_latched": estop,
                 "intent_status": intent_status, "commands": commands}
        self._next_tick = tick + 1
        self._last_sample_ns, self._last_evaluated_ns = sample_ns, now
        self._intent, self._last_intent_sequence = active, sequence
        return frame

    def snapshot(self):
        """Detached checkpoint with a consistency digest, not authentication."""
        self._check_gate()
        result = {"schema": SNAPSHOT_SCHEMA, "body_id": self._body_id,
                  "revision": self._revision, "profile_sha256": self._profile_sha256,
                  "next_tick": self._next_tick, "last_sample_ns": self._last_sample_ns,
                  "last_evaluated_ns": self._last_evaluated_ns,
                  "last_intent_sequence": self._last_intent_sequence,
                  "intent": self._intent, "estop_latched": self._gate.estop_latched}
        result["sha256"] = _digest(result)
        return json.loads(json.dumps(result, allow_nan=False))

    @classmethod
    def from_snapshot(cls, gate, *, body_id, joints, snapshot, expected_sha256,
                      max_observation_age_ns=50_000_000, max_intent_ttl_ns=500_000_000):
        """Restore against caller-pinned digest/configuration and current gate.

        Restoring cannot clear a live gate's emergency stop. The caller owns
        snapshot durability, trust and anti-rollback selection; this is not a
        journal or proof of physical continuity.
        """
        if (type(expected_sha256) is not str or len(expected_sha256) != 64
                or not _SHA.fullmatch(expected_sha256)):
            raise ReflexError("invalid externally pinned snapshot digest")
        _keys(snapshot, {"schema", "body_id", "revision", "profile_sha256", "next_tick",
                         "last_sample_ns", "last_evaluated_ns", "last_intent_sequence",
                         "intent", "estop_latched", "sha256"}, "snapshot")
        result = cls(gate, body_id=body_id, joints=joints,
                     max_observation_age_ns=max_observation_age_ns,
                     max_intent_ttl_ns=max_intent_ttl_ns)
        if type(snapshot["schema"]) is not str or snapshot["schema"] != SNAPSHOT_SCHEMA:
            raise ReflexError("unsupported snapshot schema")
        if _name(snapshot["body_id"], "snapshot body") != result._body_id:
            raise ReflexError("foreign snapshot body")
        if _int(snapshot["revision"], "snapshot revision") != result._revision:
            raise ReflexError("incompatible snapshot revision")
        if (type(snapshot["profile_sha256"]) is not str
                or snapshot["profile_sha256"] != result._profile_sha256):
            raise ReflexError("snapshot profile mismatch")
        next_tick = _int(snapshot["next_tick"], "snapshot tick")
        sequence = _int(snapshot["last_intent_sequence"], "snapshot intent sequence", -1)
        latched = _bool(snapshot["estop_latched"], "snapshot emergency stop")
        sample_ns, evaluated_ns = snapshot["last_sample_ns"], snapshot["last_evaluated_ns"]
        active = None if snapshot["intent"] is None else result._admit_intent(snapshot["intent"])
        if next_tick == 0:
            if sample_ns is not None or evaluated_ns is not None or sequence != -1 or active is not None:
                raise ReflexError("nonempty initial snapshot")
        else:
            _int(sample_ns, "snapshot sample time")
            _int(evaluated_ns, "snapshot evaluation time")
            if not sample_ns <= evaluated_ns <= sample_ns + result._max_age:
                raise ReflexError("inconsistent snapshot clocks")
        if active is not None and (active["revision"] != result._revision
                or active["sequence"] > sequence
                or not active["issued_at_ns"] <= evaluated_ns < active["expires_at_ns"]):
            raise ReflexError("inconsistent snapshot intention")
        # Everything hashed below has bounded, admitted primitive shape.
        canonical = {"schema": SNAPSHOT_SCHEMA, "body_id": result._body_id,
                     "revision": result._revision, "profile_sha256": result._profile_sha256,
                     "next_tick": next_tick, "last_sample_ns": sample_ns,
                     "last_evaluated_ns": evaluated_ns, "last_intent_sequence": sequence,
                     "intent": active, "estop_latched": latched}
        digest = _digest(canonical)
        if (type(snapshot["sha256"]) is not str or snapshot["sha256"] != digest
                or expected_sha256 != digest):
            raise ReflexError("snapshot digest mismatch")
        result._next_tick, result._last_sample_ns = next_tick, sample_ns
        result._last_evaluated_ns, result._last_intent_sequence = evaluated_ns, sequence
        result._intent = active
        if latched:
            gate.trip_estop()
        return result
