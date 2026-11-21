"""Isolated, versioned point-room policy that cannot collapse into permanent rest.

The canonical ``mojum.developmental.motor-play/1`` fixture learns one global
blocked/total ratio per cardinal action. Issue #106 shows the failure mode of
coarse action suppression in a zoned explorer: once every direction has once
been blocked, a policy can settle on ``rest`` and look collision-free only
because it stopped trying anything.

This module is a **new, opt-in profile**, not a reinterpretation of the
canonical schema. It changes three things:

* Blocked evidence is stored per 0.5 m zone, so one blocked ``east`` in one
  cell cannot suppress ``east`` everywhere else.
* External ``avoid`` hints are soft: they reorder the regular choice for the
  current measured tick and never delete an action from the policy.
* Rest is capped by a short streak limit, and every action is force-probed
  after a bounded number of decisions. A policy therefore cannot be starved
  into an all-rest trace even when every move has been blocked so far.

The body is still a synthetic point-room fixture. Nothing here is a brain,
reward, goal, navigation score, MuJoCo result or real actuation, and obstacle
geometry, object identities and future measurements are never policy inputs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re

SCHEMA = "mojum.developmental.zoned-escape/1"
STEP = 0.125
MAX_OBSERVATIONS = 9_999
ZONE_M = 0.5
MAX_ZONES = 128
MAX_POSITION = 1_000_000.0
MAX_CONSECUTIVE_RESTS = 3
FORCED_EXPLORATION_PERIOD = 16
MAX_ZONE_INDEX = 2_000_000

MOVES = (
    ("east", STEP, 0.0),
    ("north", 0.0, STEP),
    ("west", -STEP, 0.0),
    ("south", 0.0, -STEP),
)
REST = ("rest", 0.0, 0.0)
MOVE_NAMES = tuple(name for name, _, _ in MOVES)
ACTION_NAMES = (*MOVE_NAMES, REST[0])
_ZONE_KEY = re.compile(r"-?[0-9]{1,7}:-?[0-9]{1,7}\Z")


class EscapePolicyError(ValueError):
    """Reject malformed state or an out-of-contract decision."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _position(value):
    if (type(value) not in (list, tuple) or len(value) != 2
            or not all(_finite(v) and abs(v) <= MAX_POSITION for v in value)):
        raise EscapePolicyError("invalid measured point-room position")
    return float(value[0]), float(value[1])


def _zone(position):
    x, y = position
    return (f"{math.floor(x / ZONE_M)}:{math.floor(y / ZONE_M)}")


def _zero_row():
    return {"trials": 0, "blocked": 0}


def _digest(state):
    value = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ZonedEscapePlay:
    """Deterministic, bounded, zone-local motor policy with an anti-rest cap."""

    def __init__(self):
        self.zones = {}
        self.rest = _zero_row()
        self.last_attempt = {name: -1 for name in ACTION_NAMES}
        self.observations = 0
        self.evicted_trials = 0
        self.rest_streak = 0

    # -- state -----------------------------------------------------------
    def snapshot(self):
        return {
            "schema": SCHEMA,
            "observations": self.observations,
            "evicted_trials": self.evicted_trials,
            "rest_streak": self.rest_streak,
            "rest": dict(self.rest),
            "last_attempt": dict(self.last_attempt),
            "zones": copy.deepcopy(self.zones),
        }

    @classmethod
    def from_snapshot(cls, raw):
        if (type(raw) is not dict or set(raw) != {
                "schema", "observations", "evicted_trials", "rest_streak",
                "rest", "last_attempt", "zones",
            }
                or raw["schema"] != SCHEMA
                or type(raw["observations"]) is not int
                or not 0 <= raw["observations"] <= MAX_OBSERVATIONS
                or type(raw["evicted_trials"]) is not int
                or not 0 <= raw["evicted_trials"] <= MAX_OBSERVATIONS
                or type(raw["rest_streak"]) is not int
                or not 0 <= raw["rest_streak"] <= MAX_CONSECUTIVE_RESTS):
            raise EscapePolicyError("invalid zoned-escape snapshot header")
        instance = cls()
        instance._check_row(raw["rest"], raw["observations"])
        if (raw["rest"]["blocked"] != 0
                or raw["rest_streak"] > raw["rest"]["trials"]):
            raise EscapePolicyError("invalid rest evidence or streak")
        last = raw["last_attempt"]
        if (type(last) is not dict or set(last) != set(ACTION_NAMES)
                or any(type(v) is not int or not -1 <= v < raw["observations"]
                       for v in last.values())):
            raise EscapePolicyError("invalid measured attempt history")
        zones = raw["zones"]
        if type(zones) is not dict or len(zones) > MAX_ZONES:
            raise EscapePolicyError("invalid bounded zone table")
        total = raw["rest"]["trials"] + raw["evicted_trials"]
        for key, zone in zones.items():
            if (type(key) is not str or _ZONE_KEY.fullmatch(key) is None
                    or any(abs(int(part)) > MAX_ZONE_INDEX for part in key.split(":"))):
                raise EscapePolicyError("invalid zone key")
            if (type(zone) is not dict or set(zone) != {"last_touch", "rows"}
                    or type(zone["last_touch"]) is not int
                    or not 0 <= zone["last_touch"] < raw["observations"]
                    or type(zone["rows"]) is not dict
                    or set(zone["rows"]) != set(MOVE_NAMES)):
                raise EscapePolicyError("invalid zone record")
            for name, row in zone["rows"].items():
                instance._check_row(row, raw["observations"])
                total += row["trials"]
        if total > raw["observations"]:
            raise EscapePolicyError("zone evidence exceeds observation budget")
        instance.zones = copy.deepcopy(zones)
        instance.rest = dict(raw["rest"])
        instance.last_attempt = dict(last)
        instance.observations = raw["observations"]
        instance.evicted_trials = raw["evicted_trials"]
        instance.rest_streak = raw["rest_streak"]
        return instance

    @staticmethod
    def _check_row(row, limit):
        if (type(row) is not dict or set(row) != {"trials", "blocked"}
                or type(row["trials"]) is not int or type(row["blocked"]) is not int
                or not 0 <= row["blocked"] <= row["trials"] <= limit):
            raise EscapePolicyError("invalid measured action counts")

    # -- decision --------------------------------------------------------
    def choose(self, tick, position, *, avoid=(), prefer=()):
        """Select one bounded action from measured evidence only."""
        if (type(tick) is not int or not 0 <= tick < MAX_OBSERVATIONS
                or tick != self.observations):
            raise EscapePolicyError("stale zoned-escape decision tick")
        zone = _zone(_position(position))
        avoid = self._hints(avoid, "avoid")
        prefer = self._hints(prefer, "prefer")
        forced = None
        if self.rest_streak >= MAX_CONSECUTIVE_RESTS:
            name = self._stalest_move()
            forced = "rest_cap"
        elif self.observations < len(MOVES):
            name = MOVE_NAMES[self.observations]
            forced = "initial_sample"
        else:
            forced_name = self._forced_probe()
            if forced_name is not None:
                name = forced_name
                forced = "exploration"
            elif tick % 11 == 10:
                name = REST[0]
            else:
                name = self._regular_move(zone, avoid, prefer)
        dx, dy = dict((n, (x, y)) for n, x, y in (*MOVES, REST))[name]
        row = self.rest if name == REST[0] else self._row(zone, name)
        return {
            "schema": SCHEMA,
            "kind": name,
            "dx": dx,
            "dy": dy,
            "zone": zone,
            "expected_unblocked_probability":
                (row["trials"] - row["blocked"] + 1) / (row["trials"] + 2),
            "prior_trials": row["trials"],
            "rest_streak": self.rest_streak,
            "forced": forced,
            "interface": "bounded_point_room_fixture_only",
        }

    def observe(self, *, tick, position, kind, blocked):
        """Record only the measured outcome of the action just attempted."""
        if (type(tick) is not int or tick != self.observations
                or type(kind) is not str or kind not in ACTION_NAMES
                or type(blocked) is not bool
                or self.observations >= MAX_OBSERVATIONS):
            raise EscapePolicyError("invalid or stale measured motor outcome")
        zone = _zone(_position(position))
        if kind == REST[0] and self.rest_streak >= MAX_CONSECUTIVE_RESTS:
            raise EscapePolicyError("rest streak cap exceeded")
        if kind == REST[0]:
            if blocked:
                raise EscapePolicyError("rest cannot be blocked in the fixture")
            row = self.rest
            self.rest_streak += 1
        else:
            self._touch(zone, tick)
            row = self.zones[zone]["rows"][kind]
            self.rest_streak = 0
        row["trials"] += 1
        row["blocked"] += int(blocked)
        self.last_attempt[kind] = tick
        self.observations += 1
        return {
            "observed_blocked": blocked,
            "observations": self.observations,
            "model_sha256": _digest(self.snapshot()),
            "training_source": "own_measured_point_room_fixture_outcome",
        }

    # -- internals -------------------------------------------------------
    @staticmethod
    def _hints(values, label):
        if type(values) not in (tuple, list):
            raise EscapePolicyError(f"{label} hints must be a bounded sequence")
        if len(values) > len(MOVE_NAMES):
            raise EscapePolicyError(f"{label} hints must be unique and bounded")
        if any(type(v) is not str or v not in MOVE_NAMES for v in values):
            raise EscapePolicyError(f"unknown {label} hint")
        if len(set(values)) != len(values):
            raise EscapePolicyError(f"{label} hints must be unique and bounded")
        return tuple(values)

    def _row(self, zone, name):
        if zone not in self.zones:
            return {"trials": 0, "blocked": 0}
        return self.zones[zone]["rows"][name]

    def _touch(self, zone, tick):
        if zone in self.zones:
            self.zones[zone]["last_touch"] = tick
            return
        if len(self.zones) >= MAX_ZONES:
            victim = min(self.zones, key=lambda k: (self.zones[k]["last_touch"], k))
            self.evicted_trials += sum(
                row["trials"] for row in self.zones[victim]["rows"].values()
            )
            del self.zones[victim]
        self.zones[zone] = {
            "last_touch": tick,
            "rows": {name: _zero_row() for name in MOVE_NAMES},
        }

    def _stalest_move(self):
        best_name, best_age = MOVE_NAMES[0], -1
        for name in MOVE_NAMES:
            age = self.observations - self.last_attempt[name]
            if age > best_age:
                best_name, best_age = name, age
        return best_name

    def _forced_probe(self):
        name = self._stalest_move()
        age = self.observations - self.last_attempt[name]
        return name if age >= FORCED_EXPLORATION_PERIOD else None

    def _regular_move(self, zone, avoid, prefer):
        def score(name):
            row = self._row(zone, name)
            return (row["trials"] - row["blocked"] + 1) / (row["trials"] + 2)

        eligible = tuple(name for name in MOVE_NAMES if name not in avoid) or MOVE_NAMES
        preferred = tuple(name for name in eligible if name in prefer)
        candidates = preferred or eligible
        # Deterministic tie-break: highest smoothed rate, then canonical order.
        return max(candidates, key=lambda name: (score(name), -MOVE_NAMES.index(name)))
