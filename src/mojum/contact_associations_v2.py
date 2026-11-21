"""Separately versioned, bounded-retention contact associations (issue #133).

``mojum.sensory-curiosity.association/2-windowed`` is a separate experimental
wire identity from canonical ``mojum.sensory-curiosity.association/2``.
It never converts or reinterprets
``/1`` snapshots: ``/1`` snapshots are rejected here and ``/2`` snapshots are
rejected by ``ContactAssociations.from_snapshot``.

Retention is deterministic and bounded:
* per-cue mean uses step ``1/min(count, WINDOW)``; after 64 contacts
  this is exponential smoothing, not an exact 64-contact sliding window;
* when the cue table is full, the least-recently-updated cue is evicted
  (ties: smallest key) and its observations are accounted as forgotten;
* invariant: ``sum(entry.total) + evicted_observations == updates``.
"""

from __future__ import annotations

import copy

from .sensory_play import (
    CuriosityError, ContactAssociations, MAX_CUES, _finite,
)

MODEL_SCHEMA_V2 = "mojum.sensory-curiosity.association/2-windowed"
WINDOW = 64
MAX_UPDATES_V2 = 10**9


class ContactAssociationsV2(ContactAssociations):
    def __init__(self):
        super().__init__()
        self.evicted_cues = 0
        self.evicted_observations = 0

    def snapshot(self):
        return {
            "schema": MODEL_SCHEMA_V2, "table": copy.deepcopy(self.table),
            "updates": self.updates, "evicted_cues": self.evicted_cues,
            "evicted_observations": self.evicted_observations,
        }

    @classmethod
    def from_snapshot(cls, snapshot):
        if (not isinstance(snapshot, dict)
                or set(snapshot) != {"schema", "table", "updates", "evicted_cues",
                                     "evicted_observations"}
                or snapshot["schema"] != MODEL_SCHEMA_V2
                or any(type(snapshot[k]) is not int or snapshot[k] < 0
                       for k in ("updates", "evicted_cues", "evicted_observations"))
                or snapshot["updates"] > MAX_UPDATES_V2
                or not isinstance(snapshot["table"], dict)
                or len(snapshot["table"]) > MAX_CUES):
            raise CuriosityError("invalid developmental model snapshot")
        # Each eviction requires a full MAX_CUES table and an observation
        # of a new cue. The table stays full after every real eviction.
        if (snapshot["evicted_cues"] > max(0, snapshot["updates"] - MAX_CUES)
                or (snapshot["evicted_cues"] and len(snapshot["table"]) != MAX_CUES)):
            raise CuriosityError("impossible eviction history")
        if snapshot["updates"] and not snapshot["table"]:
            raise CuriosityError("learned observations without retained cues")
        total = snapshot["evicted_observations"]
        seen_last_updates = set()
        if (snapshot["evicted_cues"] > snapshot["evicted_observations"]):
            raise CuriosityError("inconsistent eviction accounting")
        # Every eviction requires a fully populated MAX_CUES table and one
        # later contact with a new cue. A real update always retains its cue.
        if (snapshot["updates"] and not snapshot["table"]
                or snapshot["evicted_cues"] > max(0, snapshot["updates"] - MAX_CUES)
                or (snapshot["evicted_cues"] and len(snapshot["table"]) != MAX_CUES)):
            raise CuriosityError("impossible association retention history")
        for key, entry in snapshot["table"].items():
            if not isinstance(key, str) or len(key) > 11:
                raise CuriosityError("invalid numeric cue")
            parts = key.split(":")
            if (len(parts) != 3 or any(not v.isascii() or not v.isdecimal() or str(int(v)) != v
                                        or not 0 <= int(v) <= 255 for v in parts)):
                raise CuriosityError("invalid numeric cue")
            if (not isinstance(entry, dict)
                    or set(entry) != {"count", "total", "last_update", "mean_deflection"}
                    or any(type(entry[k]) is not int for k in ("count", "total", "last_update"))
                    or not 1 <= entry["count"] <= min(WINDOW, entry["total"])
                    or not entry["count"] == min(WINDOW, entry["total"])
                    or not 1 <= entry["last_update"] <= snapshot["updates"]
                    or entry["total"] > entry["last_update"]
                    or not _finite(entry["mean_deflection"])
                    or not 0 <= entry["mean_deflection"] <= 1):
                raise CuriosityError("invalid learned association")
            # An accepted measurement increments updates exactly once, so two
            # live cues can never share the same last-update counter.
            if entry["last_update"] in seen_last_updates:
                raise CuriosityError("duplicate association update timestamp")
            seen_last_updates.add(entry["last_update"])
            total += entry["total"]
        if snapshot["updates"] and max(seen_last_updates) != snapshot["updates"]:
            raise CuriosityError("latest association stamp does not match updates")
        if total != snapshot["updates"]:
            raise CuriosityError("inconsistent association count")
        # Every update stamps exactly one retained cue. The most recent
        # contact cannot have been evicted without a later replacement.
        if snapshot["updates"] and max(seen_last_updates) != snapshot["updates"]:
            raise CuriosityError("latest association update missing")
        m = cls()
        m.table = copy.deepcopy(snapshot["table"])
        m.updates = snapshot["updates"]
        m.evicted_cues = snapshot["evicted_cues"]
        m.evicted_observations = snapshot["evicted_observations"]
        return m

    def learn(self, sensor, measurement, prediction):
        if prediction != self.predict(sensor):
            raise CuriosityError("stale association prediction")
        if (type(measurement) is not dict
                or set(measurement) != {"contact", "instrument", "source_tick", "measured_deflection"}
                or type(measurement.get("contact")) is not bool
                or measurement.get("instrument") != "synthetic_probe_not_material_classifier"
                or type(measurement.get("source_tick")) is not int
                or measurement["source_tick"] != sensor["tick"]):
            raise CuriosityError("invalid measured probe feedback")
        if not measurement["contact"]:
            if measurement.get("measured_deflection") is not None:
                raise CuriosityError("no contact but response was supplied")
            return {
                "learned": False, "observed_deflection": None, "prediction_error": None,
                "new_prediction": prediction["predicted_deflection"],
                "evidence_count": prediction["observations"], "updates": self.updates,
                "status": "no_contact_observation",
            }
        value = measurement.get("measured_deflection")
        if not _finite(value) or not 0 <= value <= 1 or prediction["cue"] is None:
            raise CuriosityError("invalid or unsupported contact response")
        if self.updates >= MAX_UPDATES_V2:
            raise CuriosityError("sensory association update budget reached")
        key = prediction["cue"]
        if key not in self.table and len(self.table) >= MAX_CUES:
            victim = min(self.table, key=lambda k: (self.table[k]["last_update"], k))
            self.evicted_cues += 1
            self.evicted_observations += self.table.pop(victim)["total"]
        entry = self.table.setdefault(
            key, {"count": 0, "total": 0, "last_update": 0, "mean_deflection": 0.0})
        entry["count"] = min(WINDOW, entry["count"] + 1)
        entry["total"] += 1
        entry["mean_deflection"] += (value - entry["mean_deflection"]) / entry["count"]
        entry["mean_deflection"] = min(1.0, max(0.0, entry["mean_deflection"]))
        self.updates += 1
        entry["last_update"] = self.updates
        return {
            "learned": True, "observed_deflection": value,
            "prediction_error": None if prediction["predicted_deflection"] is None
            else value - prediction["predicted_deflection"],
            "new_prediction": entry["mean_deflection"],
            "evidence_count": entry["count"], "updates": self.updates,
            "status": "empirical_measurement_not_object_semantics",
        }
