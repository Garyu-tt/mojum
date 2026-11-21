"""R0.13 opt-in adapter: sensory R0.9 -> canonical Aquarium event stream.

The learner gets a strict numeric near-field cue and only receives a material
response *after* requesting a synthetic probe. Toy labels/true softness remain
experimenter-only. No real camera, robotics or new SQLite journal.
"""
from __future__ import annotations

import math

from .sensory_play import ContactAssociations, ToyScene
from .sim import digest
from .world_room import Room

SCHEMA = "mojum.aquarium.toy-contact/1"
OCCLUSION_SCHEMA = "mojum.sensor.center-ray-wall-mask/1"
PROBE_CAPABILITY = "SYNTHETIC_READ_ONLY_CONTACT"
REPROBE_PERIOD_TICKS = 7


class SensoryBridgeError(ValueError):
    """Incompatible fixture or inconsistent replayed sensory evidence."""


class ToyContactBridge:
    """Evaluate a probe and its proposed update without mutating live state."""

    def __init__(self, scene: ToyScene, *, motor_room: Room):
        self.scene = validate_scene(motor_room, scene)
        self.motor_room = motor_room

    def sense(self, world):
        return self.scene.sense(world, occluding_obstacles=self.motor_room.obstacles)

    def evaluate(self, world, model: ContactAssociations, *, cue=None):
        """Return the pre-movement sensor frame and candidate association state."""
        if cue is None:
            cue = self.sense(world)
        prediction = model.predict(cue)
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
        candidate = ContactAssociations.from_snapshot(model.snapshot())
        measurement = (self.scene.probe(
            world, cue, occluding_obstacles=self.motor_room.obstacles,
        ) if requested else None)
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


def validate_scene(room, scene):
    """The same 2D room coordinates must be used by motor and sensor fixtures."""
    if type(scene) is not ToyScene:
        raise SensoryBridgeError("invalid synthetic sensory scene")
    if (scene.room.width != room.width or scene.room.height != room.height):
        raise SensoryBridgeError("sensory and motor room dimensions differ")
    # A fixture toy may not occupy the same solid space as a motor obstacle.
    # Test the toy disc against each closed obstacle AABB; checking only the
    # center would admit a visually plausible but physically impossible toy.
    # This is an occupancy check, NOT a line-of-sight/occlusion simulation.
    for toy in scene.toys:
        for left, bottom, width, height in room.obstacles:
            nearest_x = max(left, min(toy.x, left + width))
            nearest_y = max(bottom, min(toy.y, bottom + height))
            if math.hypot(toy.x - nearest_x, toy.y - nearest_y) <= toy.radius_m:
                raise SensoryBridgeError("synthetic toy intersects motor-room obstacle")
    return scene
