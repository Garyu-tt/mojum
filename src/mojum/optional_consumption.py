"""Opt-in, non-metabolic food and water sensory experiences (synthetic fixture).

This is a standalone CPU/stdlib event ledger. It intentionally models neither
hunger, thirst, energy, dehydration, starvation, nor survival. Merely recording
an opt-in experience cannot claim that an autonomous creature chose it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

EVENT_SCHEMA = "mojum.sensory.optional-consumption-event/1"
LEDGER_SCHEMA = "mojum.sensory.optional-consumption-ledger/1"
FRAME_SCHEMA = "mojum.sensory.optional-consumption-frame/1"

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z", re.ASCII)
_EVENT_KEYS = frozenset({
    "schema", "event_id", "organism_id", "tick", "kind", "stimulus_id",
    "flavor", "temperature", "texture", "opt_in", "source",
})
_FLAVORS = frozenset({"neutral", "sweet", "sour", "salty", "bitter", "umami", "mixed"})
_TEMPERATURES = frozenset({"cold", "cool", "ambient", "warm", "hot"})
_TEXTURES = frozenset({"liquid", "smooth", "soft", "crunchy", "chewy", "other"})


class OptionalConsumptionError(ValueError):
    """Malformed or stale optional sensory experience."""


def _exact_dict(value, keys, name):
    if type(value) is not dict or set(value) != set(keys):
        raise OptionalConsumptionError(f"{name}: expected exact fields {sorted(keys)}")


def _id(value, name):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise OptionalConsumptionError(f"{name}: invalid bounded ASCII identifier")
    return value


def _tick(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise OptionalConsumptionError("tick: invalid nonnegative 64-bit integer")
    return value


def _choice(value, choices, name):
    if type(value) is not str or value not in choices:
        raise OptionalConsumptionError(f"{name}: unsupported value")
    return value


def _digest(organism_id, events):
    raw = json.dumps(
        {"organism_id": organism_id, "events": events},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def validate_event(value, organism_id):
    """Copy an untrusted event only after validating its complete wire shape."""
    _id(organism_id, "organism_id")
    _exact_dict(value, _EVENT_KEYS, "event")
    if value["schema"] != EVENT_SCHEMA:
        raise OptionalConsumptionError("unsupported optional consumption event schema")
    _id(value["event_id"], "event_id")
    if _id(value["organism_id"], "organism_id") != organism_id:
        raise OptionalConsumptionError("experience belongs to another organism")
    _tick(value["tick"])
    _choice(value["kind"], {"food_taste", "water_sip"}, "kind")
    _id(value["stimulus_id"], "stimulus_id")
    _choice(value["flavor"], _FLAVORS, "flavor")
    _choice(value["temperature"], _TEMPERATURES, "temperature")
    _choice(value["texture"], _TEXTURES, "texture")
    _choice(value["source"], {"scripted_fixture", "measured_adapter"}, "source")
    if value["opt_in"] is not True:
        raise OptionalConsumptionError("explicit opt_in=true is required")
    if value["kind"] == "water_sip" and (
        value["stimulus_id"] != "water" or value["texture"] != "liquid"
    ):
        raise OptionalConsumptionError("water_sip requires water and liquid texture")
    # No untrusted unknown fields may smuggle in calories, hunger, hydration,
    # death, or an actuation command under this exact versioned schema.
    return copy.deepcopy(value)


class OptionalConsumptionLedger:
    """Bounded, compare-and-append synthetic diary, not a survival system.

    Revisions are SHA-256 integrity checks, not authentication or anti-rollback.
    opt_in is a source assertion, not independently verified consent.
    """

    MAX_EVENTS = 512

    def __init__(self, organism_id):
        self.organism_id = _id(organism_id, "organism_id")
        self._events = []
        self._ids = set()

    @property
    def revision(self):
        return _digest(self.organism_id, self._events)

    def append(self, event, expected_revision):
        if type(expected_revision) is not str or expected_revision != self.revision:
            raise OptionalConsumptionError("stale or missing ledger revision (CAS failed)")
        checked = validate_event(event, self.organism_id)
        if len(self._events) >= self.MAX_EVENTS:
            raise OptionalConsumptionError("experience ledger event limit exceeded")
        if checked["event_id"] in self._ids:
            raise OptionalConsumptionError("duplicate experience event_id")
        if self._events and checked["tick"] < self._events[-1]["tick"]:
            raise OptionalConsumptionError("experience tick must be nondecreasing")
        self._events.append(checked)
        self._ids.add(checked["event_id"])
        return self.revision

    def frame(self, tick):
        """Read-only projection with no deprivation or metabolic state."""
        tick = _tick(tick)
        return {
            "schema": FRAME_SCHEMA,
            "organism_id": self.organism_id,
            "tick": tick,
            "events_this_tick": copy.deepcopy([e for e in self._events if e["tick"] == tick]),
            "food_required_for_survival": False,
            "water_required_for_survival": False,
            "deprivation_effect": None,
            "provenance": "optional_sensory_fixture_not_metabolism_or_verified_choice",
        }

    def snapshot(self):
        return {
            "schema": LEDGER_SCHEMA,
            "organism_id": self.organism_id,
            "events": copy.deepcopy(self._events),
            "revision": self.revision,
        }

    @classmethod
    def from_snapshot(cls, snapshot):
        _exact_dict(snapshot, {"schema", "organism_id", "events", "revision"}, "snapshot")
        if snapshot["schema"] != LEDGER_SCHEMA:
            raise OptionalConsumptionError("unsupported optional consumption ledger schema")
        events = snapshot["events"]
        if type(events) is not list or len(events) > cls.MAX_EVENTS:
            raise OptionalConsumptionError("snapshot events must be a bounded list")
        ledger = cls(snapshot["organism_id"])
        for event in events:
            ledger.append(event, ledger.revision)
        if type(snapshot["revision"]) is not str or snapshot["revision"] != ledger.revision:
            raise OptionalConsumptionError("ledger snapshot checksum mismatch")
        return ledger
