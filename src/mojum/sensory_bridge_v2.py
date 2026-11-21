"""Opt-in v2 synthetic contact bridge, preserving v1 event semantics.

The new 2-tick re-probe cadence is intentionally explicit and pinned in
the NEW bootstrap; old R0.13 bridge and association replay remain intact.
"""
from __future__ import annotations

from .sensory_associations_v2 import ContactAssociationsV2
from .sensory_bridge import (
    PROBE_CAPABILITY, SensoryBridgeError, ToyContactBridge,
)
from .sim import digest

SCHEMA = "mojum.aquarium.toy-contact/2"
REPROBE_PERIOD_TICKS = 2


class ToyContactBridgeV2(ToyContactBridge):
    """Pure synthetic sensory evaluation; adopt only after a journal commit."""

    def __init__(self, scene, *, motor_room=None):
        super().__init__(scene, motor_room=motor_room)
        self.model = ContactAssociationsV2()

    def evaluate(self, world):
        cue = self.scene.sense(
            world, occluding_obstacles=self.motor_room.obstacles,
        )
        prediction = self.model.predict(cue)
        near = cue["visible"] and cue["range_bucket_10cm"] <= 2
        first = prediction["observations"] == 0
        periodic = world.tick % REPROBE_PERIOD_TICKS == 0
        requested = bool(near and (first or periodic))
        reason = (
            "first_unmeasured_near_field_cue"
            if requested and first else
            "periodic_empirical_resample" if requested else
            "no_probe_needed"
        )
        probe = {
            "requested": requested, "tick": world.tick,
            "source_revision": world.revision,
            "reason": reason, "capability": PROBE_CAPABILITY,
            "external_actuator": None,
        }
        candidate = ContactAssociationsV2.from_snapshot(self.model.snapshot())
        measurement = (
            self.scene.probe(
                world, cue, occluding_obstacles=self.motor_room.obstacles,
            ) if requested else None
        )
        learned = (
            candidate.learn(cue, measurement, prediction)
            if measurement is not None else None
        )
        frame = {
            "schema": SCHEMA,
            "observation": cue,
            "prediction_before": prediction,
            "probe_intent": probe,
            "measured_contact": measurement,
            "learning": learned,
            "association_state_sha256": digest(candidate.snapshot()),
            "interpretation":
                "numeric_cue_to_observed_probe_response_not_object_identity",
        }
        return frame, candidate

    def adopt(self, world, committed):
        expected, candidate = self.evaluate(world)
        if committed != expected:
            raise SensoryBridgeError("non-causal or forged v2 sensory frame")
        self.model = candidate
