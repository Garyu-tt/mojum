"""Bounded numeric near-field sensing and empirical virtual contact learning."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math

from .world_room import Room, _segment_hits_rect


MODEL_SCHEMA = "mojum.sensory-curiosity.association/1"
SENSE_SCHEMA = "mojum.sensor.synthetic-near-field/1"
MAX_EVENTS = 4000
MAX_CUES = 128


class CuriosityError(ValueError):
    """Unsafe fixture, untrusted observation or inconsistent replay."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


@dataclass(frozen=True)
class Toy:
    """Experimenter-defined *hidden* response; semantics are not agent input."""
    id: str
    x: float
    y: float
    radius_m: float
    rgb: tuple[int, int, int]
    softness: float

    def __post_init__(self):
        if not isinstance(self.id, str) or not 1 <= len(self.id) <= 80:
            raise CuriosityError("invalid spectator toy ID")
        try:
            self.id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CuriosityError("spectator toy ID must be valid UTF-8") from exc
        if not all(_finite(v) for v in (self.x, self.y, self.radius_m, self.softness)):
            raise CuriosityError("nonfinite toy parameters")
        if not 0.01 <= self.radius_m <= 0.5 or not 0 <= self.softness <= 1:
            raise CuriosityError("invalid toy response or radius")
        if (type(self.rgb) is not tuple or len(self.rgb) != 3
                or any(type(c) is not int or not 0 <= c <= 255 for c in self.rgb)):
            raise CuriosityError("RGB must be three integers 0..255")


DEFAULT_TOYS = (
    Toy("spectator.toy.a", 1.20, 2.00, 0.15, (220, 70, 30), 0.75),
    Toy("spectator.toy.b", 2.60, 2.60, 0.18, (30, 130, 220), 0.20),
)


@dataclass(frozen=True, init=False)
class ToyScene:
    room: Room
    toys: tuple[Toy, ...]

    def __init__(self, *, width=4.0, height=4.0, toys=DEFAULT_TOYS):
        if not all(_finite(v) and v > 0 for v in (width, height)):
            raise CuriosityError("invalid scene size")
        if (type(toys) is not tuple or len(toys) > 20
                or any(type(t) is not Toy for t in toys)):
            raise CuriosityError("invalid toy collection")
        ids = [t.id for t in toys]
        if len(ids) != len(set(ids)):
            raise CuriosityError("duplicate spectator toy IDs")
        for t in toys:
            if not (t.radius_m < t.x < width - t.radius_m
                    and t.radius_m < t.y < height - t.radius_m):
                raise CuriosityError("toy outside point-room")
        object.__setattr__(self, "room", Room(float(width), float(height)))
        object.__setattr__(self, "toys", toys)

    def manifest(self):
        return {
            "width": self.room.width, "height": self.room.height,
            "toys": [
                {"id": t.id, "x": t.x, "y": t.y, "radius_m": t.radius_m,
                 "rgb": list(t.rgb), "softness": t.softness}
                for t in self.toys
            ],
            "spectator_only_toy_identity_and_softness": True,
        }

    def _nearest(self, state, occluding_obstacles=()):
        # A bounded center ray blocked by a solid room AABB cannot expose a
        # toy's numeric appearance/response. This is a coarse 2D visibility
        # fixture, not optical ray tracing or a physical contact model.
        # Ties use public geometry/reflectance, never hidden toy IDs/softness.
        available = (
            t for t in self.toys
            if not any(
                _segment_hits_rect(state.x, state.y, t.x, t.y, rect)
                for rect in occluding_obstacles
            )
        )
        return min(
            available,
            key=lambda t: (
                math.hypot(state.x - t.x, state.y - t.y),
                t.x, t.y, t.radius_m, t.rgb,
            ),
            default=None,
        )

    def sense(self, state, *, occluding_obstacles=()):
        """Numeric near-field cue, with optional canonical solid-wall masking."""
        t = self._nearest(state, occluding_obstacles)
        distance = math.hypot(state.x - t.x, state.y - t.y) if t else math.inf
        visible = t is not None and distance <= 0.6
        # Deliberately quantized, noisy-world claims are out of scope.
        return {
            "schema": SENSE_SCHEMA, "tick": state.tick,
            "world_revision": state.revision, "visible": visible,
            "reflectance_rgb": list(t.rgb) if visible else None,
            "range_bucket_10cm": int(distance / 0.1) if visible else None,
            "source": "synthetic_local_proximity_not_camera",
        }

    def probe(self, state, sensor, *, occluding_obstacles=()):
        """One bounded virtual touch; respect the same wall mask as sensing."""
        if (not isinstance(sensor, dict) or sensor != self.sense(
                state, occluding_obstacles=occluding_obstacles)):
            raise CuriosityError("stale or forged near-field frame")
        t = self._nearest(state, occluding_obstacles)
        distance = math.hypot(state.x - t.x, state.y - t.y) if t else math.inf
        contact = bool(sensor["visible"] and distance <= t.radius_m + 0.08)
        return {
            "contact": contact, "measured_deflection": t.softness if contact else None,
            "instrument": "synthetic_probe_not_material_classifier",
            "source_tick": state.tick,
        }


class ContactAssociations:
    """Empirical cue -> measured deflection table; blank at birth."""

    def __init__(self):
        self.table = {}
        self.updates = 0

    @staticmethod
    def _key(sensor):
        if (not isinstance(sensor, dict) or set(sensor) != {
                "schema", "tick", "world_revision", "visible",
                "reflectance_rgb", "range_bucket_10cm", "source",
            } or sensor.get("schema") != SENSE_SCHEMA
                or sensor.get("source") != "synthetic_local_proximity_not_camera"
                or type(sensor.get("tick")) is not int or sensor["tick"] < 0
                or not isinstance(sensor.get("world_revision"), str)
                or len(sensor["world_revision"]) != 64
                or any(c not in "0123456789abcdef" for c in sensor["world_revision"])
                or type(sensor.get("visible")) is not bool):
            raise CuriosityError("invalid near-field observation")
        if not sensor["visible"]:
            if sensor.get("reflectance_rgb") is not None or sensor.get("range_bucket_10cm") is not None:
                raise CuriosityError("invisible cue unexpectedly discloses object")
            return None
        rgb = sensor.get("reflectance_rgb")
        rng = sensor.get("range_bucket_10cm")
        if (not isinstance(rgb, list) or len(rgb) != 3
                or any(type(v) is not int or not 0 <= v <= 255 for v in rgb)
                or type(rng) is not int or not 0 <= rng <= 6):
            raise CuriosityError("invalid sensor signal")
        return ":".join(str(x) for x in rgb)

    def snapshot(self):
        return {
            "schema": MODEL_SCHEMA, "table": copy.deepcopy(self.table),
            "updates": self.updates,
        }

    @classmethod
    def from_snapshot(cls, snapshot):
        if (not isinstance(snapshot, dict)
                or set(snapshot) != {"schema", "table", "updates"}
                or snapshot["schema"] != MODEL_SCHEMA
                or type(snapshot["updates"]) is not int
                or not 0 <= snapshot["updates"] <= MAX_EVENTS
                or not isinstance(snapshot["table"], dict)
                or len(snapshot["table"]) > MAX_CUES):
            raise CuriosityError("invalid developmental model snapshot")
        total = 0
        for key, entry in snapshot["table"].items():
            # RGB cue keys are at most "255:255:255" (11 characters).
            # Bound untrusted strings before splitting or converting to int:
            # otherwise oversized decimal segments can raise raw ValueError.
            if not isinstance(key, str) or len(key) > 11:
                raise CuriosityError("invalid numeric cue")
            parts = key.split(":")
            if (len(parts) != 3 or any(not v.isdecimal() or str(int(v)) != v
                                        or not 0 <= int(v) <= 255 for v in parts)):
                raise CuriosityError("invalid numeric cue")
            if (not isinstance(entry, dict) or set(entry) != {"count", "mean_deflection"}
                    or type(entry["count"]) is not int
                    or not 1 <= entry["count"] <= snapshot["updates"]
                    or not _finite(entry["mean_deflection"])
                    or not 0 <= entry["mean_deflection"] <= 1):
                raise CuriosityError("invalid learned association")
            total += entry["count"]
        if total != snapshot["updates"]:
            raise CuriosityError("inconsistent association count")
        m = cls()
        m.table, m.updates = copy.deepcopy(snapshot["table"]), snapshot["updates"]
        return m

    def predict(self, sensor):
        key = self._key(sensor)
        entry = self.table.get(key) if key is not None else None
        return {
            "cue": key, "predicted_deflection": entry["mean_deflection"] if entry else None,
            "observations": entry["count"] if entry else 0,
            "model_updates": self.updates,
        }

    def learn(self, sensor, measurement, prediction):
        if prediction != self.predict(sensor):
            raise CuriosityError("stale association prediction")
        if (not isinstance(measurement, dict) or type(measurement.get("contact")) is not bool
                or measurement.get("instrument") != "synthetic_probe_not_material_classifier"
                or type(measurement.get("source_tick")) is not int
                or measurement["source_tick"] != sensor["tick"]):
            raise CuriosityError("invalid measured probe feedback")
        if measurement["contact"]:
            value = measurement.get("measured_deflection")
            if not _finite(value) or not 0 <= value <= 1 or prediction["cue"] is None:
                raise CuriosityError("invalid or unsupported contact response")
            key = prediction["cue"]
            if key not in self.table:
                if len(self.table) >= MAX_CUES:
                    raise CuriosityError("sensory association memory exhausted")
                self.table[key] = {"count": 0, "mean_deflection": 0.0}
            entry = self.table[key]
            old = entry["mean_deflection"]
            entry["count"] += 1
            entry["mean_deflection"] += (value - entry["mean_deflection"]) / entry["count"]
            self.updates += 1
            return {
                "learned": True, "observed_deflection": value,
                "prediction_error": None if prediction["predicted_deflection"] is None
                else value - prediction["predicted_deflection"],
                "new_prediction": entry["mean_deflection"],
                "evidence_count": entry["count"], "updates": self.updates,
                "status": "empirical_measurement_not_object_semantics",
            }
        if measurement.get("measured_deflection") is not None:
            raise CuriosityError("no contact but response was supplied")
        return {
            "learned": False, "observed_deflection": None, "prediction_error": None,
            "new_prediction": prediction["predicted_deflection"],
            "evidence_count": prediction["observations"],
            "updates": self.updates, "status": "no_contact_observation",
        }
