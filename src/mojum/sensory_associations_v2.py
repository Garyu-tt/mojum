"""Versioned bounded retention for synthetic contact associations.

The original ``association/1`` model stops after 4,000 measured contacts so
that every persisted snapshot remains loadable.  This module is an explicitly
opt-in ``/2`` contract: old snapshots are rejected, new snapshots retain at
most 128 numeric cues, and deterministic least-recently-measured eviction
keeps a long-running fixture available without silently changing legacy
history.

This is still a synthetic numeric probe model.  It has no object identity,
material semantics, reward, affect, camera, physical actuator, or claim of a
trained brain.
"""

from __future__ import annotations

import copy
import math
import re

from .sensory_play import ContactAssociations, CuriosityError


SCHEMA = "mojum.sensory-curiosity.association/2"
LEARNING_SCHEMA = "mojum.sensory-curiosity.learning/2"
MAX_CUES = 128
MAX_UPDATES = 100_000
MAX_TICK = 2**63 - 1
_CUE = re.compile(
    r"(?:0|[1-9][0-9]{0,2}):(?:0|[1-9][0-9]{0,2}):(?:0|[1-9][0-9]{0,2})\Z",
    re.ASCII,
)


class SensoryV2Error(CuriosityError):
    """Invalid empirical contact, retention, or serialized state."""


def _valid_deflection(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except (OverflowError, TypeError, ValueError):
        return False


class ContactAssociationsV2(ContactAssociations):
    """Bounded empirical cue-to-measurement memory with deterministic LRU.

    Only an actual measured contact increments ``updates``.  A new cue is
    admitted by evicting the least-recently measured retained cue; ties use
    the canonical cue string.  Forgotten evidence is accounted for rather
    than silently reset.
    """

    def __init__(self):
        super().__init__()
        self.last_seen_at = {}
        self.last_contact_tick = -1
        self.evictions = 0
        self.forgotten_observations = 0

    def snapshot(self):
        return {
            "schema": SCHEMA,
            "table": copy.deepcopy(self.table),
            "updates": self.updates,
            "last_seen_at": dict(self.last_seen_at),
            "last_contact_tick": self.last_contact_tick,
            "evictions": self.evictions,
            "forgotten_observations": self.forgotten_observations,
        }

    @classmethod
    def from_snapshot(cls, data):
        expected = {
            "schema", "table", "updates", "last_seen_at",
            "last_contact_tick", "evictions", "forgotten_observations",
        }
        if (
            type(data) is not dict
            or set(data) != expected
            or data["schema"] != SCHEMA
            or type(data["table"]) is not dict
            or type(data["last_seen_at"]) is not dict
            or set(data["table"]) != set(data["last_seen_at"])
            or len(data["table"]) > MAX_CUES
            or type(data["updates"]) is not int
            or not 0 <= data["updates"] <= MAX_UPDATES
            or type(data["last_contact_tick"]) is not int
            or not -1 <= data["last_contact_tick"] <= MAX_TICK
            or (data["updates"] == 0) != (data["last_contact_tick"] == -1)
            or data["updates"] > data["last_contact_tick"] + 1
            or type(data["evictions"]) is not int
            or type(data["forgotten_observations"]) is not int
            or not 0 <= data["evictions"] <= data["forgotten_observations"] <= data["updates"]
            or data["evictions"] > max(0, data["updates"] - MAX_CUES)
        ):
            raise SensoryV2Error("invalid versioned association envelope")

        retained_observations = 0
        latest_seen = -1
        for key, entry in data["table"].items():
            if (
                type(key) is not str
                or _CUE.fullmatch(key) is None
                or any(int(part) > 255 for part in key.split(":"))
                or type(entry) is not dict
                or set(entry) != {"count", "mean_deflection"}
                or type(entry["count"]) is not int
                or not 1 <= entry["count"] <= MAX_UPDATES
                or not _valid_deflection(entry["mean_deflection"])
            ):
                raise SensoryV2Error("invalid retained numeric association")
            stamp = data["last_seen_at"][key]
            if type(stamp) is not int or not 0 <= stamp <= data["last_contact_tick"]:
                raise SensoryV2Error("invalid remembered contact time")
            if entry["count"] > stamp + 1:
                raise SensoryV2Error("impossible per-cue measurement history")
            retained_observations += entry["count"]
            latest_seen = max(latest_seen, stamp)

        if len(set(data["last_seen_at"].values())) != len(data["last_seen_at"]):
            raise SensoryV2Error("multiple cues cannot be measured on one tick")

        if (
            retained_observations + data["forgotten_observations"] != data["updates"]
            or (data["updates"] and latest_seen != data["last_contact_tick"])
            or (data["evictions"] == 0 and data["forgotten_observations"] != 0)
            or (data["updates"] and not data["table"])
        ):
            raise SensoryV2Error("contact memory evidence accounting mismatch")

        instance = cls()
        instance.table = copy.deepcopy(data["table"])
        instance.updates = data["updates"]
        instance.last_seen_at = dict(data["last_seen_at"])
        instance.last_contact_tick = data["last_contact_tick"]
        instance.evictions = data["evictions"]
        instance.forgotten_observations = data["forgotten_observations"]
        return instance

    def learn(self, sensor, measurement, prediction):
        """Prepare one measured update, preserving state on every rejection."""
        if prediction != self.predict(sensor):
            raise SensoryV2Error("stale measured-cue prediction")
        if (
            type(measurement) is not dict
            or set(measurement) != {
                "contact", "measured_deflection", "instrument", "source_tick",
            }
            or type(measurement.get("contact")) is not bool
            or measurement.get("instrument") != "synthetic_probe_not_material_classifier"
            or type(measurement.get("source_tick")) is not int
            or not 0 <= measurement["source_tick"] <= MAX_TICK
            or measurement["source_tick"] != sensor["tick"]
        ):
            raise SensoryV2Error("invalid measured contact envelope")

        if not measurement["contact"]:
            if measurement["measured_deflection"] is not None:
                raise SensoryV2Error("unmeasured contact response was supplied")
            return {
                "schema": LEARNING_SCHEMA,
                "learned": False,
                "observed_deflection": None,
                "prediction_error": None,
                "new_prediction": prediction["predicted_deflection"],
                "evidence_count": prediction["observations"],
                "updates": self.updates,
                "evicted_cue": None,
                "evicted_observations": 0,
                "total_evictions": self.evictions,
                "total_forgotten_observations": self.forgotten_observations,
                "status": "no_contact_observation",
            }

        value = measurement["measured_deflection"]
        key = prediction["cue"]
        tick = measurement["source_tick"]
        if (
            not _valid_deflection(value)
            or key is None
            or tick <= self.last_contact_tick
            or self.updates >= MAX_UPDATES
        ):
            raise SensoryV2Error("invalid, stale, or exhausted measured contact")

        evicted, forgotten = None, 0
        if key not in self.table and len(self.table) >= MAX_CUES:
            evicted = min(self.last_seen_at, key=lambda cue: (self.last_seen_at[cue], cue))
            forgotten = self.table[evicted]["count"]
            del self.table[evicted]
            del self.last_seen_at[evicted]
            self.evictions += 1
            self.forgotten_observations += forgotten

        row = self.table.setdefault(key, {"count": 0, "mean_deflection": 0.0})
        row["count"] += 1
        row["mean_deflection"] += (value - row["mean_deflection"]) / row["count"]
        self.updates += 1
        self.last_contact_tick = tick
        self.last_seen_at[key] = tick
        return {
            "schema": LEARNING_SCHEMA,
            "learned": True,
            "observed_deflection": value,
            "prediction_error": (
                None if prediction["predicted_deflection"] is None
                else value - prediction["predicted_deflection"]
            ),
            "new_prediction": row["mean_deflection"],
            "evidence_count": row["count"],
            "updates": self.updates,
            "evicted_cue": evicted,
            "evicted_observations": forgotten,
            "total_evictions": self.evictions,
            "total_forgotten_observations": self.forgotten_observations,
            "status": "empirical_measurement_not_object_semantics",
        }
