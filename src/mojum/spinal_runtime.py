"""Opt-in deterministic Brain–Spine CPU fixture, separate from Aquarium.

Integer logical time drives routing and motor validation without invoking an
inference backend. Other named rates are scheduling signals, not implemented
CPG/reflex/homeostasis controllers. Targets are synthetic receipts only; every
target passes the existing MotorGate at emission. No actuator or I/O is exposed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType

from .motor_gate import MotorGate, MotorIntent
from .spinal_graph import (
    SEGMENTS, SpinalError, build_spinal_graph, validate_spinal_graph,
    require_identifier, require_int, require_number,
)

STATE_SCHEMA = "mojum.spinal-state/1"
SENSORY_SCHEMA = "mojum.spinal-sensory/1"
INTENTION_SCHEMA = "mojum.spinal-intention/1"
MAX_TICK = 2**53 - 1
MAX_QUEUE = 256
MAX_JOINTS = 64
DEFAULT_RATES = MappingProxyType({
    "sensory": 240, "motor": 120, "cpg": 100, "coordination": 60,
    "intention": 20, "homeostasis": 5, "cognition": 2,
})


def _record(value, fields, name):
    if (type(value) is not dict or len(value) != len(fields)
            or any(type(key) is not str or len(key) > 64 for key in value)
            or set(value) != set(fields)):
        raise SpinalError(f"invalid {name} fields")
    return value


def _sha(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _digest(value, name):
    if (type(value) is not str or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise SpinalError(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True)
class SensoryFrame:
    segment: str
    channel: str
    value: float
    revision: int
    tick: int
    destination: str = "brain"


@dataclass(frozen=True, slots=True)
class SpinalIntent:
    segment: str
    joint: str
    target: float
    revision: int
    issued_tick: int
    expires_tick: int


@dataclass(frozen=True, slots=True)
class MotorReceipt:
    segment: str
    joint: str
    target: float | None
    revision: int
    tick: int
    expires_tick: int | None
    mode: str


@dataclass(frozen=True, slots=True)
class StepResult:
    tick: int
    due: tuple[str, ...]
    relayed: tuple[SensoryFrame, ...]
    motors: tuple[MotorReceipt, ...]
    expired_intents: int
    dropped_sensory: int
    dropped_ascending: int
    inhibit_reason: str | None


def _sensory_wire(frame):
    return {"schema": SENSORY_SCHEMA, "segment": frame.segment,
            "channel": frame.channel, "value": frame.value,
            "revision": frame.revision, "tick": frame.tick,
            "destination": frame.destination}


def _intent_wire(intent):
    return {"schema": INTENTION_SCHEMA, "segment": intent.segment,
            "joint": intent.joint, "target": intent.target,
            "revision": intent.revision, "issued_tick": intent.issued_tick,
            "expires_tick": intent.expires_tick}


class SpinalRuntime:
    """Bounded single-threaded runtime with explicit caller-driven inputs.

    Call ``step`` independently of cognition; ``read_ascending`` is optional.
    Ingress overflow is rejected atomically. A stalled brain's telemetry mailbox
    drops its oldest frame and counts the loss, so it cannot stop motor expiry.
    One sensory packet makes at most one graph-authorized adjacent hop.
    """

    def __init__(self, gate, joint_segments, *, graph=None, base_hz=1200,
                 rates=None, queue_capacity=64, max_intent_age_ticks=600,
                 sensory_max_age_ticks=1200):
        if type(gate) is not MotorGate:
            raise SpinalError("an existing MotorGate is required")
        self._graph = validate_spinal_graph(
            build_spinal_graph(gate.revision) if graph is None else graph)
        self._revision = self._graph["revision"]
        if gate.revision != self._revision:
            raise SpinalError("graph and MotorGate revision differ")
        if (type(joint_segments) is not dict or not joint_segments
                or len(joint_segments) > MAX_JOINTS):
            raise SpinalError("joint bindings must be a bounded native mapping")
        bindings = {}
        for joint, segment in joint_segments.items():
            require_identifier(joint, "joint")
            self._segment(segment)
            bindings[joint] = segment
        if set(bindings) != set(gate.limits):
            raise SpinalError("every MotorGate joint needs exactly one segment")
        self._bindings = dict(sorted(bindings.items()))
        self._gate = gate
        self._base_hz = require_int(base_hz, "base_hz", 1, 12000)
        self._capacity = require_int(queue_capacity, "queue_capacity", 1, MAX_QUEUE)
        self._max_intent_age = require_int(
            max_intent_age_ticks, "max_intent_age_ticks", 1, 120000)
        self._sensory_max_age = require_int(
            sensory_max_age_ticks, "sensory_max_age_ticks", 1, 120000)
        if rates is None:
            rates = dict(DEFAULT_RATES)
        _record(rates, DEFAULT_RATES, "rates")
        self._rates = {}
        for name in sorted(rates):
            rate = require_int(rates[name], f"{name} rate", 1, self._base_hz)
            if self._base_hz % rate:
                raise SpinalError("rates must divide the integer base_hz exactly")
            self._rates[name] = rate
        self._periods = {name: self._base_hz // rate
                         for name, rate in self._rates.items()}
        # Derive dispatch from the validated topology, never a second registry.
        self._routes = frozenset((edge["from"]["segment"], edge["to"]["segment"])
                                 for edge in self._graph["edges"])
        self._ascending_sources = frozenset(
            item["id"] for item in self._graph["segments"]
            if item["outputs"].get("ascending") == SENSORY_SCHEMA)
        self._tick = 0
        self._sensory = deque()
        self._intentions = deque()
        self._ascending = deque()
        self._active = {}
        self._dropped_sensory = 0
        self._dropped_ascending = 0
        self._profile_sha256 = _sha({
            "graph": self._graph, "joint_segments": self._bindings,
            "limits": {key: list(gate.limits[key]) for key in self._bindings},
            "base_hz": self._base_hz, "rates": self._rates,
            "queue_capacity": self._capacity,
            "max_intent_age_ticks": self._max_intent_age,
            "sensory_max_age_ticks": self._sensory_max_age,
        })

    @staticmethod
    def _segment(value):
        require_identifier(value, "segment")
        if value not in SEGMENTS:
            raise SpinalError("unknown spinal segment")
        return value

    @property
    def tick(self):
        return self._tick

    @property
    def profile_sha256(self):
        return self._profile_sha256

    @property
    def pending_counts(self):
        return {"sensory": len(self._sensory), "intentions": len(self._intentions),
                "ascending": len(self._ascending), "active": len(self._active)}

    def _sensory_checked(self, frame, now):
        if type(frame) is not SensoryFrame:
            raise SpinalError("SensoryFrame required")
        segment = self._segment(frame.segment)
        channel = require_identifier(frame.channel, "channel")
        value = require_number(frame.value, "sensory value")
        revision = require_int(frame.revision, "revision", 0, 2**31 - 1)
        tick = require_int(frame.tick, "sensory tick", 0, MAX_TICK)
        require_identifier(frame.destination, "destination")
        if revision != self._revision:
            raise SpinalError("stale sensory revision")
        if tick > now or now - tick > self._sensory_max_age:
            raise SpinalError("future or stale sensory timestamp")
        if frame.destination == "brain":
            if segment not in self._ascending_sources:
                raise SpinalError("segment has no ascending sensory port")
        elif (segment, frame.destination) not in self._routes:
            raise SpinalError("sensory route is not in the validated graph")
        return SensoryFrame(segment, channel, value, revision, tick, frame.destination)

    def _intent_checked(self, intent, now):
        if type(intent) is not SpinalIntent:
            raise SpinalError("SpinalIntent required")
        segment = self._segment(intent.segment)
        joint = require_identifier(intent.joint, "joint")
        target = require_number(intent.target, "target")
        revision = require_int(intent.revision, "revision", 0, 2**31 - 1)
        issued = require_int(intent.issued_tick, "issued_tick", 0, MAX_TICK)
        expiry = require_int(intent.expires_tick, "expires_tick", 0, MAX_TICK)
        if revision != self._revision:
            raise SpinalError("stale intention revision")
        if self._bindings.get(joint) != segment:
            raise SpinalError("joint is not bound to the addressed segment")
        if issued > now or expiry <= now or not 0 < expiry - issued <= self._max_intent_age:
            raise SpinalError("future, expired or overlong intention")
        lo, hi = self._gate.limits[joint]
        if not lo <= target <= hi:
            raise SpinalError("target outside MotorGate bounds")
        return SpinalIntent(segment, joint, target, revision, issued, expiry)

    def submit_sensory(self, frame):
        checked = self._sensory_checked(frame, self._tick)
        if len(self._sensory) >= self._capacity:
            raise SpinalError("sensory ingress queue is full")
        self._sensory.append(checked)

    def submit_intent(self, intent):
        checked = self._intent_checked(intent, self._tick)
        self._gate.validate(MotorIntent(checked.joint, checked.target, checked.revision))
        if len(self._intentions) >= self._capacity:
            raise SpinalError("intention ingress queue is full")
        previous = [item.issued_tick for item in self._intentions
                    if item.joint == checked.joint]
        if checked.joint in self._active:
            previous.append(self._active[checked.joint].issued_tick)
        if previous and checked.issued_tick < max(previous):
            raise SpinalError("older intention would overwrite a newer intention")
        self._intentions.append(checked)

    def read_ascending(self, limit=None):
        if limit is None:
            limit = self._capacity
        require_int(limit, "ascending read limit", 0, self._capacity)
        return tuple(self._ascending.popleft()
                     for _ in range(min(limit, len(self._ascending))))

    def step(self):
        if self._tick >= MAX_TICK:
            raise SpinalError("logical tick budget exhausted")
        self._tick += 1
        now = self._tick
        due = tuple(name for name, period in self._periods.items() if now % period == 0)
        expired = sum(item.expires_tick <= now for item in self._intentions)
        self._intentions = deque(item for item in self._intentions if item.expires_tick > now)
        expired_active = [key for key, item in self._active.items() if item.expires_tick <= now]
        for key in expired_active:
            del self._active[key]
        expired += len(expired_active)
        dropped_sensory = 0
        for name in ("_sensory", "_ascending"):
            queue = getattr(self, name)
            fresh = deque(item for item in queue if now - item.tick <= self._sensory_max_age)
            dropped_sensory += len(queue) - len(fresh)
            setattr(self, name, fresh)
        self._dropped_sensory += dropped_sensory
        inhibit = ("estop" if self._gate.estop_latched else
                   "revision_changed" if self._gate.revision != self._revision else None)
        if inhibit:
            self._intentions.clear()
            self._active.clear()
        elif "intention" in due:
            while self._intentions:
                intent = self._intentions.popleft()
                self._active[intent.joint] = intent
        relayed = []
        dropped_ascending = 0
        if "sensory" in due:
            while self._sensory:
                frame = self._sensory.popleft()
                if frame.destination == "brain":
                    if len(self._ascending) == self._capacity:
                        self._ascending.popleft()
                        dropped_ascending += 1
                    self._ascending.append(frame)
                else:
                    relayed.append(frame)
        self._dropped_ascending += dropped_ascending
        motors = []
        # Expiry and inhibition are observed immediately, including between the
        # slower motor schedule boundaries. A rest receipt grants no target.
        if "motor" in due or expired_active or inhibit:
            for joint, segment in self._bindings.items():
                intent = self._active.get(joint)
                if inhibit or intent is None:
                    motors.append(MotorReceipt(segment, joint, None, self._revision,
                                               now, None, "inhibited" if inhibit else "rest"))
                else:
                    target = self._gate.validate(MotorIntent(joint, intent.target, intent.revision))
                    motors.append(MotorReceipt(segment, joint, target, self._revision,
                                               now, intent.expires_tick, "intent"))
        return StepResult(now, due, tuple(relayed), tuple(motors), expired,
                          dropped_sensory, dropped_ascending, inhibit)

    def snapshot(self):
        if self._gate.revision != self._revision:
            raise SpinalError("MotorGate revision changed; create a new runtime")
        body = {
            "schema": STATE_SCHEMA, "profile_sha256": self._profile_sha256,
            "tick": self._tick, "sensory": [_sensory_wire(x) for x in self._sensory],
            "intentions": [_intent_wire(x) for x in self._intentions],
            "ascending": [_sensory_wire(x) for x in self._ascending],
            "active": [_intent_wire(self._active[key]) for key in sorted(self._active)],
            "dropped_sensory": self._dropped_sensory,
            "dropped_ascending": self._dropped_ascending,
            "estop_latched": self._gate.estop_latched,
        }
        return {**body, "sha256": _sha(body)}

    def _restore_queue(self, values, kind, now, maximum):
        if type(values) is not list or len(values) > maximum:
            raise SpinalError("checkpoint queue exceeds capacity")
        checked = []
        for value in values:
            if kind == "sensory":
                _record(value, ("schema", "segment", "channel", "value", "revision", "tick", "destination"),
                        "checkpoint sensory")
                if type(value["schema"]) is not str or value["schema"] != SENSORY_SCHEMA:
                    raise SpinalError("invalid sensory schema")
                frame = SensoryFrame(value["segment"], value["channel"], value["value"],
                                     value["revision"], value["tick"], value["destination"])
                checked.append(self._sensory_checked(frame, now))
            else:
                _record(value, ("schema", "segment", "joint", "target", "revision", "issued_tick", "expires_tick"),
                        "checkpoint intention")
                if type(value["schema"]) is not str or value["schema"] != INTENTION_SCHEMA:
                    raise SpinalError("invalid intention schema")
                intent = SpinalIntent(value["segment"], value["joint"], value["target"],
                                      value["revision"], value["issued_tick"], value["expires_tick"])
                checked.append(self._intent_checked(intent, now))
        return checked

    def restore(self, snapshot):
        """Validate entirely before commit; never rewind or clear a live e-stop.

        For cold continuation construct an identical runtime with its existing
        process-local MotorGate, then restore. Hashes detect corruption, not
        authenticity. Durable journal integration is outside this fixture.
        """
        fields = ("schema", "profile_sha256", "tick", "sensory", "intentions", "ascending",
                  "active", "dropped_sensory", "dropped_ascending", "estop_latched", "sha256")
        _record(snapshot, fields, "checkpoint")
        if type(snapshot["schema"]) is not str or snapshot["schema"] != STATE_SCHEMA:
            raise SpinalError("unsupported spinal state schema")
        if _digest(snapshot["profile_sha256"], "profile hash") != self._profile_sha256:
            raise SpinalError("checkpoint graph/config/limits profile mismatch")
        if self._gate.revision != self._revision:
            raise SpinalError("MotorGate revision changed")
        now = require_int(snapshot["tick"], "checkpoint tick", self._tick, MAX_TICK)
        sensory = self._restore_queue(snapshot["sensory"], "sensory", now, self._capacity)
        intentions = self._restore_queue(snapshot["intentions"], "intent", now, self._capacity)
        ascending = self._restore_queue(snapshot["ascending"], "sensory", now, self._capacity)
        active_items = self._restore_queue(snapshot["active"], "intent", now, len(self._bindings))
        if any(item.destination != "brain" for item in ascending):
            raise SpinalError("checkpoint ascending queue contains a local route")
        last_sensory = now - now % self._periods["sensory"]
        if any(item.tick >= last_sensory for item in ascending):
            raise SpinalError("checkpoint sensory precedes its routing phase")
        active = {}
        last_issued = {}
        last_intention = now - now % self._periods["intention"]
        for item in active_items:
            if item.joint in active:
                raise SpinalError("checkpoint duplicates active joint")
            if item.issued_tick >= last_intention:
                raise SpinalError("checkpoint active intention precedes its scheduling phase")
            active[item.joint] = item
            last_issued[item.joint] = item.issued_tick
        for item in intentions:
            if item.issued_tick < last_issued.get(item.joint, 0):
                raise SpinalError("checkpoint intention ordering is stale")
            last_issued[item.joint] = item.issued_tick
        dropped_sensory = require_int(snapshot["dropped_sensory"], "dropped_sensory", 0, MAX_TICK * MAX_QUEUE * 2)
        dropped_ascending = require_int(snapshot["dropped_ascending"], "dropped_ascending", 0, MAX_TICK * MAX_QUEUE)
        if type(snapshot["estop_latched"]) is not bool:
            raise SpinalError("checkpoint e-stop must be a native boolean")
        _digest(snapshot["sha256"], "checkpoint checksum")
        # All values have bounded native shape before any serialization.
        body = {key: snapshot[key] for key in fields if key != "sha256"}
        if _sha(body) != snapshot["sha256"]:
            raise SpinalError("spinal checkpoint checksum mismatch")
        self._tick = now
        self._sensory = deque(sensory)
        self._intentions = deque(intentions)
        self._ascending = deque(ascending)
        self._active = active
        self._dropped_sensory = dropped_sensory
        self._dropped_ascending = dropped_ascending
        if snapshot["estop_latched"]:
            self._gate.trip_estop()
