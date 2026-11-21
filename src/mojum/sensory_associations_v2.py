"""Version-pinned synthetic contact-association memory with bounded LRU.

This is an opt-in research-only sensor model. Its independent /2 schema
does NOT reinterpret the legacy /1 association state or change the hidden
toy softness/identity isolation contract. Data eviction is not punishment.
"""
from __future__ import annotations

import copy
import math
import re

from .sensory_play import (
    ContactAssociations, CuriosityError, SENSE_SCHEMA,
)

SCHEMA = "mojum.sensory-curiosity.association/2"
LEARNING_SCHEMA = "mojum.sensory-curiosity.learning/2"
MAX_CUES = 128
MAX_UPDATES = 100_000
_CUE = re.compile(r"(?:0|[1-9][0-9]{0,2}):(?:0|[1-9][0-9]{0,2}):(?:0|[1-9][0-9]{0,2})\Z", re.ASCII)


class SensoryV2Error(CuriosityError):
    """Invalid empirical contact, retention or serialized state."""


def _valid_deflection(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except (OverflowError, ValueError, TypeError):
        return False


class ContactAssociationsV2(ContactAssociations):
    """Only successful measured contacts advance learned evidence.

    128 live RGB cue records, deterministic least-recently-measured
    replacement, and explicit forgotten-evidence accounting. The
    numeric cue remains the exact old sensory cue: no hidden toy ID.
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
        if (type(data) is not dict or set(data) != expected
                or data["schema"] != SCHEMA
                or type(data["table"]) is not dict
                or type(data["last_seen_at"]) is not dict
                or set(data["table"]) != set(data["last_seen_at"])
                or len(data["table"]) > MAX_CUES
                or type(data["updates"]) is not int
                or not 0 <= data["updates"] <= MAX_UPDATES
                or type(data["last_contact_tick"]) is not int
                or not -1 <= data["last_contact_tick"] < MAX_UPDATES
                or (data["updates"] == 0) != (data["last_contact_tick"] == -1)
                or type(data["evictions"]) is not int
                or type(data["forgotten_observations"]) is not int
                or not 0 <= data["evictions"] <=
                       data["forgotten_observations"] <= data["updates"]):
            raise SensoryV2Error("invalid versioned association envelope")
        kept = 0
        max_seen = -1
        for key, entry in data["table"].items():
            if (type(key) is not str or _CUE.fullmatch(key) is None
                    or any(int(k) > 255 for k in key.split(":"))
                    or type(entry) is not dict
                    or set(entry) != {"count", "mean_deflection"}
                    or type(entry["count"]) is not int
                    or not 1 <= entry["count"] <= MAX_UPDATES
                    or not _valid_deflection(entry["mean_deflection"])):
                raise SensoryV2Error("invalid retained numeric association")
            stamp = data["last_seen_at"][key]
            if (type(stamp) is not int
                    or not 0 <= stamp <= data["last_contact_tick"]):
                raise SensoryV2Error("invalid remembered contact time")
            kept += entry["count"]
            max_seen = max(max_seen, stamp)
        if (kept + data["forgotten_observations"] != data["updates"]
                or (data["updates"] and max_seen != data["last_contact_tick"])
                or (data["evictions"] == 0 and
                    data["forgotten_observations"] != 0)):
            raise SensoryV2Error("contact memory evidence accounting mismatch")
        obj = cls()
        obj.table = copy.deepcopy(data["table"])
        obj.updates = data["updates"]
        obj.last_seen_at = dict(data["last_seen_at"])
        obj.last_contact_tick = data["last_contact_tick"]
        obj.evictions = data["evictions"]
        obj.forgotten_observations = data["forgotten_observations"]
        return obj

    def learn(self, sensor, measurement, prediction):
        if prediction != self.predict(sensor):
            raise SensoryV2Error("stale measured-cue prediction")
        if (type(measurement) is not dict
                or set(measurement) != {
                    "contact", "measured_deflection",
                    "instrument", "source_tick",
                }
                or type(measurement.get("contact")) is not bool
                or measurement.get("instrument") !=
                    "synthetic_probe_not_material_classifier"
                or type(measurement.get("source_tick")) is not int
                or measurement["source_tick"] != sensor["tick"]):
            raise SensoryV2Error("invalid measured contact envelope")

        if not measurement["contact"]:
            if measurement.get("measured_deflection") is not None:
                raise SensoryV2Error("unmeasured contact response was supplied")
            return {
                "schema": LEARNING_SCHEMA,
                "learned": False, "observed_deflection": None,
                "prediction_error": None,
                "new_prediction": prediction["predicted_deflection"],
                "evidence_count": prediction["observations"],
                "updates": self.updates,
                "evicted_cue": None, "evicted_observations": 0,
                "total_evictions": self.evictions,
                "total_forgotten_observations": self.forgotten_observations,
                "status": "no_contact_observation",
            }

        value = measurement.get("measured_deflection")
        key = prediction["cue"]
        tick = sensor["tick"]
        if (not _valid_deflection(value)
                or key is None
                or tick <= self.last_contact_tick
                or self.updates >= MAX_UPDATES):
            raise SensoryV2Error("invalid, stale or exhausted measured contact")

        evicted, dropped = None, 0
        if key not in self.table and len(self.table) >= MAX_CUES:
            evicted = min(
                self.last_seen_at,
                key=lambda k: (self.last_seen_at[k], k),
            )
            dropped = self.table[evicted]["count"]
            del self.table[evicted]
            del self.last_seen_at[evicted]
            self.evictions += 1
            self.forgotten_observations += dropped

        row = self.table.setdefault(key, {"count": 0, "mean_deflection": 0.0})
        old = row["mean_deflection"]
        row["count"] += 1
        row["mean_deflection"] += (value - row["mean_deflection"]) / row["count"]
        self.updates += 1
        self.last_contact_tick = tick
        self.last_seen_at[key] = tick
        return {
            "schema": LEARNING_SCHEMA,
            "learned": True, "observed_deflection": value,
            "prediction_error": (
                None if prediction["predicted_deflection"] is None
                else value - prediction["predicted_deflection"]
            ),
            "new_prediction": row["mean_deflection"],
            "evidence_count": row["count"], "updates": self.updates,
            "evicted_cue": evicted, "evicted_observations": dropped,
            "total_evictions": self.evictions,
            "total_forgotten_observations": self.forgotten_observations,
            "status": "empirical_measurement_not_object_semantics",
        }
