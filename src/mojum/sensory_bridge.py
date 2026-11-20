"""R0.13 opt-in adapter: sensory R0.9 -> canonical Aquarium event stream.

The learner gets a strict numeric near-field cue and only receives a material
response *after* requesting a synthetic probe. Toy labels/true softness remain
experimenter-only. No real camera, robotics or new SQLite journal.
"""
from __future__ import annotations

from .sensory_play import ContactAssociations, ToyScene, SENSE_SCHEMA, MODEL_SCHEMA
from .sim import digest

SCHEMA = "mojum.aquarium.toy-contact/1"
PROBE_CAPABILITY = "SYNTHETIC_READ_ONLY_CONTACT"
REPROBE_PERIOD_TICKS = 7


class SensoryBridgeError(ValueError):
    """Incompatible fixture or inconsistent replayed sensory evidence."""


class ToyContactBridge:
    """Pure evaluation followed by an explicit after-CAS model adoption."""

    def __init__(self, scene: ToyScene):
        if not isinstance(scene, ToyScene):
            raise SensoryBridgeError("ToyScene fixture required")
        self.scene = scene
        self.model = ContactAssociations()

    def evaluate(self, world):
        """Return independent proposed frame and candidate association state.

        No mutation of self.model or world occurs here. The existing local
        Aquarium decides whether the proposed frame is actually committed.
        """
        cue = self.scene.sense(world)
        prediction = self.model.predict(cue)
        near = cue["visible"] and cue["range_bucket_10cm"] <= 2
        first = prediction["observations"] == 0
        periodic = world.tick % REPROBE_PERIOD_TICKS == 0
        requested = bool(near and (first or periodic))
        reason = ("first_unmeasured_near_field_cue" if requested and first
                  else "periodic_empirical_resample" if requested
                  else "no_probe_needed")
        probe = {
            "requested": requested, "tick": world.tick,
            "source_revision": world.revision,
            "reason": reason, "capability": PROBE_CAPABILITY,
            "external_actuator": None,
        }
        candidate = ContactAssociations.from_snapshot(self.model.snapshot())
        measurement = self.scene.probe(world, cue) if requested else None
        learned = (candidate.learn(cue, measurement, prediction)
                   if measurement is not None else None)
        frame = {
            "schema": SCHEMA,
            "observation": cue,
            "prediction_before": prediction,
            "probe_intent": probe,
            "measured_contact": measurement,
            "learning": learned,
            "association_state_sha256": digest(candidate.snapshot()),
            "interpretation": "numeric_cue_to_observed_probe_response_not_object_identity",
        }
        return frame, candidate

    def adopt(self, world, committed):
        """Recompute the proposed evidence before adopting its learned state."""
        expected, candidate = self.evaluate(world)
        if committed != expected:
            raise SensoryBridgeError("non-causal or forged sensory frame")
        self.model = candidate


def validate_scene(room, scene):
    """The same 2D room coordinates must be used by motor and sensor fixtures."""
    if not isinstance(scene, ToyScene):
        raise SensoryBridgeError("invalid synthetic sensory scene")
    if (scene.room.width != room.width or scene.room.height != room.height):
        raise SensoryBridgeError("sensory and motor room dimensions differ")
    return scene
