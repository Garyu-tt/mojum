"""CPU-only sensory curiosity lab: synthetic near-field cues and measured touch.

This is a developmental *fixture*: no preloaded object meanings, semantic
identity, simulated consciousness, camera vision, physics or real actuator.
An agent sees low-level numeric cues and slowly estimates their observed
contact response. Private SQLite events are the source of truth.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import json
import math
from pathlib import Path

from .graph import validate as validate_graph
from .noosphere import EVENT_SCHEMA, LocalNoosphere
from .sim import Simulation, digest
from .world_room import Room, _segment_hits_rect, advance, initial_state


SCHEMA = "mojum.sensory-curiosity.frame/1"
MODEL_SCHEMA = "mojum.sensory-curiosity.association/1"
SENSE_SCHEMA = "mojum.sensor.synthetic-near-field/1"
ACTIONS = ("rest", "x+", "x-", "y+", "y-", "probe")
SEED = 2029
MAX_EVENTS = 4000  # Standalone SensoryPlay journal remains independently bounded.
MAX_ASSOCIATION_UPDATES = 100000  # Canonical Aquarium may hold more history.
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


class ToyScene:
    def __init__(self, *, width=4.0, height=4.0, toys=DEFAULT_TOYS):
        if not all(_finite(v) and v > 0 for v in (width, height)):
            raise CuriosityError("invalid scene size")
        if (type(toys) is not tuple or len(toys) > 20
                or any(not isinstance(t, Toy) for t in toys)):
            raise CuriosityError("invalid toy collection")
        ids = [t.id for t in toys]
        if len(ids) != len(set(ids)):
            raise CuriosityError("duplicate spectator toy IDs")
        for t in toys:
            if not (t.radius_m < t.x < width - t.radius_m
                    and t.radius_m < t.y < height - t.radius_m):
                raise CuriosityError("toy outside point-room")
        self.room = Room(float(width), float(height))
        self.toys = toys

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
                or not 0 <= snapshot["updates"] <= MAX_ASSOCIATION_UPDATES
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


class SensoryPlay:
    """Synthetic object discovery with private committed and replayed events."""

    def __init__(self, db_path, graph, *, scene=None, start=(1.0, 2.0),
                 agent_id="dante-sensory-fixture-001", rng_seed=SEED,
                 dt_ns=10_000_000):
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise CuriosityError("invalid agent")
        if type(rng_seed) is not int or not 0 <= rng_seed < 2**32:
            raise CuriosityError("invalid exploration seed")
        if type(dt_ns) is not int or not 1000 <= dt_ns <= 1_000_000_000:
            raise CuriosityError("invalid virtual clock")
        validate_graph(graph)
        kinds = {n["kind"]: n["id"] for n in graph["nodes"]}
        if "sensory.interoception" not in kinds:
            raise CuriosityError("heartbeat sensory graph required")
        self.graph = copy.deepcopy(graph)
        self.graph_hash = digest(self.graph)
        self.sensory_id = kinds["sensory.interoception"]
        self.scene = scene if scene is not None else ToyScene()
        if not isinstance(self.scene, ToyScene) or len(start) != 2:
            raise CuriosityError("invalid scene or initial position")
        self.start = tuple(float(x) for x in start)
        self.world = initial_state(self.scene.room, *self.start)
        self.agent_id = agent_id
        self.dt_ns = dt_ns
        self.model = ContactAssociations()
        self.rng = rng_seed
        self.tick = 0
        self.manifest = {
            "schema": SCHEMA, "agent_id": self.agent_id,
            "graph_sha256": self.graph_hash, "scene": self.scene.manifest(),
            "start": list(self.start), "dt_ns": dt_ns, "rng_seed": rng_seed,
            "model_schema": MODEL_SCHEMA,
            "scope": "synthetic_near_field_motor_and_contact_only",
            "goal": None, "imported_object_knowledge": None,
        }
        self.store = LocalNoosphere(db_path, self.agent_id)
        try:
            if self.store.count() == 0:
                self._append("sensory.bootstrap", "sensory-bootstrap", self.manifest, 0)
            self._replay()
        except BaseException:
            self.store.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.store.close()

    def _event(self, topic, eid, payload, clock_ns):
        return {
            "schema": EVENT_SCHEMA, "event_id": eid, "agent_id": self.agent_id,
            "topic": topic, "observed_at_ns": clock_ns, "recorded_at_ns": clock_ns,
            "valid_from_ns": clock_ns, "valid_to_ns": None,
            "visibility": "PRIVATE", "source": "mojum.sensory-play.fixture",
            "source_revision": SCHEMA, "payload": payload,
        }

    def _append(self, topic, eid, payload, clock_ns):
        if self.store.count() >= MAX_EVENTS:
            raise CuriosityError("synthetic sensory journal budget exhausted")
        self.store.append(self._event(topic, eid, payload, clock_ns),
                          expected_head=self.store.head())

    def _calculate(self):
        runtime = Simulation(self.graph, dt_ns=self.dt_ns)
        runtime.tick = self.tick
        heartbeat = runtime.step()[self.sensory_id]["sensory"]
        if heartbeat["observed_at_ns"] != self.tick * self.dt_ns:
            raise CuriosityError("inconsistent virtual heartbeat")
        sense = self.scene.sense(self.world)
        clone = ContactAssociations.from_snapshot(self.model.snapshot())
        prediction = clone.predict(sense)
        next_rng = (1664525 * self.rng + 1013904223) & 0xffffffff
        # Basic innate exploration repertoire, not a target-conditioned planner.
        options = ACTIONS if sense["visible"] else ACTIONS[:-1]
        action = options[next_rng % len(options)]
        amount = 0.02 if heartbeat["heartbeat"]["pulse"] else 0.04
        dx = amount if action == "x+" else -amount if action == "x-" else 0.0
        dy = amount if action == "y+" else -amount if action == "y-" else 0.0
        updated, world = advance(
            self.scene.room, self.world, tick=self.tick + 1,
            dx=dx, dy=dy, expected_revision=self.world.revision,
        )
        touch = self.scene.probe(self.world, sense) if action == "probe" else None
        learned = clone.learn(sense, touch, prediction) if touch is not None else {
            "learned": False, "observed_deflection": None,
            "prediction_error": None,
            "new_prediction": prediction["predicted_deflection"],
            "evidence_count": prediction["observations"],
            "updates": clone.updates,
            "status": "not_probed",
        }
        payload = {
            "schema": SCHEMA, "tick": self.tick,
            "synthetic_heartbeat": heartbeat,
            "sensor": sense,
            "experiment": {
                "action": action, "dx": dx, "dy": dy,
                "motor_command": None, "capability": "SYNTHETIC_POINT_ROOM_ONLY",
                "goal": None,
            },
            "prediction_before": prediction, "contact_result": touch,
            "world": world, "learning": learned,
            "spectator_scene": self.scene.manifest(),
            "inference_scope": "observed_numeric_cue_contact_association_only",
            "brain_status": "NOT_A_NEURAL_BRAIN",
        }
        return payload, updated, clone, next_rng

    def step(self):
        payload, next_world, next_model, next_rng = self._calculate()
        self._append("sensory.frame", f"sensory-frame-{self.tick:012d}",
                     payload, self.tick * self.dt_ns)
        self.world, self.model, self.rng = next_world, next_model, next_rng
        self.tick += 1
        return copy.deepcopy(payload)

    def _replay(self):
        self.store.verify_chain()
        n = self.store.count()
        if n > MAX_EVENTS:
            raise CuriosityError("oversized sensory replay journal")
        rows = self.store.dataset(include_nonpublic=True, limit=MAX_EVENTS)["projection"]["items"]
        if len(rows) != n or not rows:
            raise CuriosityError("incomplete sensory journal")
        if rows[0]["event"] != self._event(
            "sensory.bootstrap", "sensory-bootstrap", self.manifest, 0,
        ):
            raise CuriosityError("incompatible sensory play bootstrap")
        for row in rows[1:]:
            payload, world, model, rng = self._calculate()
            expected = self._event(
                "sensory.frame", f"sensory-frame-{self.tick:012d}",
                payload, self.tick * self.dt_ns,
            )
            if row["event"] != expected:
                raise CuriosityError("sensory experience replay mismatch")
            self.world, self.model, self.rng = world, model, rng
            self.tick += 1

    def checkpoint(self):
        return {
            "schema": "mojum.sensory-curiosity.checkpoint/1",
            "agent_id": self.agent_id, "tick": self.tick,
            "world_revision": self.world.revision,
            "association_model": self.model.snapshot(),
            "rng": self.rng, "head": self.store.head(),
            "integrity": "local_checksum_not_authenticated",
        }


def main():
    p = argparse.ArgumentParser(description="CPU-only numeric sensory curiosity fixture")
    root = Path(__file__).resolve().parents[2]
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    p.add_argument("--ticks", type=int, default=120)
    p.add_argument("--watch", action="store_true",
                   help="emit synthetic PRIVATE numeric evidence to local stdout")
    args = p.parse_args()
    if not 0 <= args.ticks <= 1000:
        p.error("--ticks must be within 0..1000")
    with SensoryPlay(args.db, json.loads(args.graph.read_text(encoding="utf-8"))) as baby:
        for _ in range(args.ticks):
            row = baby.step()
            if args.watch:
                print(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False),
                      flush=True)
        print(json.dumps({"checkpoint": baby.checkpoint(),
                          "private_events": baby.store.count()},
                         ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
