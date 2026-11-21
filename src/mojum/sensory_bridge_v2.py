"""Opt-in versioned synthetic contact bridge for the canonical Aquarium.

The bridge keeps the legacy ``toy_probe`` path untouched.  ``toy_probe_v2``
uses a two-tick empirical re-probe cadence so the bounded association model
can be exercised beyond 4,000 measured contacts before the
``developmental_fixture`` motor-specific 10,000-event replay ceiling is
reached.  The global Aquarium journal ceiling is larger and is not changed by
this opt-in bridge.
"""

from __future__ import annotations

from .sensory_associations_v2 import ContactAssociationsV2
from .sensory_bridge import (
    PROBE_CAPABILITY,
    SensoryBridgeError,
    ToyContactBridge,
)
from .sim import digest


SCHEMA = "mojum.aquarium.toy-contact/2"
REPROBE_PERIOD_TICKS = 2


class ToyContactBridgeV2(ToyContactBridge):
    """Evaluate numeric contact evidence without mutating live model state."""

    def evaluate(self, world, model: ContactAssociationsV2, *, cue=None):
        if not isinstance(model, ContactAssociationsV2):
            raise SensoryBridgeError("toy_probe_v2 requires its versioned association model")
        if cue is None:
            cue = self.sense(world)
        prediction = model.predict(cue)
        near = cue["visible"] and cue["range_bucket_10cm"] <= 2
        first = prediction["observations"] == 0
        periodic = world.tick % REPROBE_PERIOD_TICKS == 0
        requested = bool(near and (first or periodic))
        reason = (
            "first_unmeasured_near_field_cue"
            if requested and first
            else "periodic_empirical_resample"
            if requested
            else "no_probe_needed"
        )
        probe = {
            "requested": requested,
            "tick": world.tick,
            "source_revision": world.revision,
            "reason": reason,
            "capability": PROBE_CAPABILITY,
            "external_actuator": None,
        }
        candidate = ContactAssociationsV2.from_snapshot(model.snapshot())
        measurement = (
            self.scene.probe(
                world,
                cue,
                occluding_obstacles=self.motor_room.obstacles,
            )
            if requested
            else None
        )
        learned = (
            candidate.learn(cue, measurement, prediction)
            if measurement is not None
            else None
        )
        return {
            "schema": SCHEMA,
            "observation": cue,
            "prediction_before": prediction,
            "probe_intent": probe,
            "measured_contact": measurement,
            "learning": learned,
            "association_state_sha256": digest(candidate.snapshot()),
            "interpretation": (
                "numeric_cue_to_observed_probe_response_not_object_identity"
            ),
        }, candidate
