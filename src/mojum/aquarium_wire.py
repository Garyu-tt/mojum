"""Identify a stored Aquarium policy from a read-only, chain-checked SQLite journal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .aquarium import (
    EPOCH_EVENT_SCHEMA,
    MANIFEST_SCHEMA,
    MAX_ABSOLUTE_TICKS,
    MAX_REPLAY_EVENTS,
    SCHEMA as FRAME_SCHEMA,
)
from .cell_policy_v4 import SCHEMA as V4_SCHEMA
from .developmental import SCHEMA as MOTOR_PLAY
from .epoch_sense_v2 import EPOCH_TICKS, SCHEMA as EPOCH_SENSE_SCHEMA
from .noosphere import NoosphereError, STORE_SCHEMA, _open_readonly, _text, _verify_chain
from .policy_identity import (
    MOTOR_SELECTOR_KEYS, POLICIES, SCHEMA as IDENTITY_SCHEMA,
    PolicyIdentityError, as_public_record, by_selector,
)
from .sensory_associations_v2 import ContactAssociationsV2
from .sensory_associations_v2 import SCHEMA as SENSORY_MODEL_V2
from .sensory_bridge import OCCLUSION_SCHEMA, PROBE_CAPABILITY
from .sensory_bridge import SCHEMA as SENSORY_BRIDGE_V1
from .sensory_bridge_v2 import REPROBE_PERIOD_TICKS as SENSORY_REPROBE_PERIOD
from .sensory_bridge_v2 import SCHEMA as SENSORY_BRIDGE_V2
from .sensory_play import CuriosityError, SENSE_SCHEMA
from .sim import canonical, digest


_GUIDANCE = "mojum.developmental.range-guidance/1"
_HEAD = re.compile(r"[0-9a-f]{64}\Z")

# The two draft runtimes both used the spelling local_cell_v2. Their
# persisted selector sets differ, as do their intent and outcome contracts;
# both facts are declared once in mojum.policy_identity so this reader and
# the CLI cannot drift apart. Never widen one identity to cover the other.
_MOTOR_KEYS = MOTOR_SELECTOR_KEYS
_TEMPORAL_FIELDS = {
    "temporal_mode": "range_guided",
    "temporal_schema": "mojum.temporal.motor-play-range/1",
    "motor_guidance_schema": _GUIDANCE,
}

# A valid chain only proves the bytes were appended by this owner.  The
# versioned sensory frame additionally pins its own clock and evidence
# accounting so a forged or drifted payload cannot be certified as
# replayable.
_SENSE_KEYS = frozenset({
    "schema", "tick", "world_revision", "visible",
    "reflectance_rgb", "range_bucket_10cm", "source",
})
_PREDICTION_KEYS = frozenset({
    "cue", "predicted_deflection", "observations", "model_updates",
})
_PROBE_KEYS = frozenset({
    "requested", "tick", "source_revision", "reason", "capability",
    "external_actuator",
})
_CONTACT_KEYS = frozenset({
    "contact", "measured_deflection", "instrument", "source_tick",
})
_SENSORY_KEYS = frozenset({
    "schema", "observation", "prediction_before", "probe_intent",
    "measured_contact", "learning", "association_state_sha256",
    "interpretation",
})
_PROBE_REASONS = frozenset({
    "first_unmeasured_near_field_cue",
    "periodic_empirical_resample",
    "no_probe_needed",
})
_SENSE_SOURCE = "synthetic_local_proximity_not_camera"
_PROBE_INSTRUMENT = "synthetic_probe_not_material_classifier"
_SENSORY_INTERPRETATION = (
    "numeric_cue_to_observed_probe_response_not_object_identity"
)


def _hex64(value):
    return type(value) is str and _HEAD.fullmatch(value) is not None


def _same_wire(left, right):
    """Compare JSON values without Python's bool/int equality alias."""
    try:
        return canonical(left) == canonical(right)
    except (TypeError, ValueError):
        return False


def _check_sensory_v2_frame(contact, tick, model):
    """Replay one versioned sensory transition and return its next model."""
    if (type(contact) is not dict or set(contact) != _SENSORY_KEYS
            or contact["schema"] != SENSORY_BRIDGE_V2
            or contact["interpretation"] != _SENSORY_INTERPRETATION
            or not _hex64(contact["association_state_sha256"])):
        raise NoosphereError("frame disagrees with its pinned sensory schema")

    observation = contact["observation"]
    if (type(observation) is not dict or set(observation) != _SENSE_KEYS
            or observation["schema"] != SENSE_SCHEMA
            or observation["source"] != _SENSE_SOURCE
            or type(observation["tick"]) is not int
            or observation["tick"] != tick
            or type(observation["visible"]) is not bool
            or not _hex64(observation["world_revision"])):
        raise NoosphereError("sensory observation disagrees with the frame tick")

    intent = contact["probe_intent"]
    if (type(intent) is not dict or set(intent) != _PROBE_KEYS
            or type(intent["tick"]) is not int
            or intent["tick"] != tick
            or type(intent["requested"]) is not bool
            or intent["reason"] not in _PROBE_REASONS
            or intent["capability"] != PROBE_CAPABILITY
            or intent["external_actuator"] is not None
            or not _hex64(intent["source_revision"])
            or intent["source_revision"] != observation["world_revision"]):
        raise NoosphereError("sensory probe intent disagrees with the frame")

    if model is None:
        model = ContactAssociationsV2()
    prediction = contact["prediction_before"]
    try:
        expected_prediction = model.predict(observation)
    except CuriosityError:
        raise NoosphereError("sensory observation is not replayable") from None
    if (type(prediction) is not dict or set(prediction) != _PREDICTION_KEYS
            or not _same_wire(prediction, expected_prediction)):
        raise NoosphereError("sensory prediction disagrees with the frame")

    measurement = contact["measured_contact"]
    learning = contact["learning"]
    if (measurement is None) != (learning is None):
        raise NoosphereError("sensory measurement and learning evidence disagree")
    if (measurement is None) != (not intent["requested"]):
        raise NoosphereError("sensory probe request and measurement disagree")
    near = observation["visible"] and observation["range_bucket_10cm"] <= 2
    first = expected_prediction["observations"] == 0
    periodic = tick % SENSORY_REPROBE_PERIOD == 0
    expected_requested = bool(near and (first or periodic))
    expected_reason = (
        "first_unmeasured_near_field_cue"
        if expected_requested and first
        else "periodic_empirical_resample"
        if expected_requested
        else "no_probe_needed"
    )
    if (intent["requested"] != expected_requested
            or intent["reason"] != expected_reason):
        raise NoosphereError("sensory probe intent is not replayable")
    if measurement is None:
        candidate = model
        expected_learning = None
    else:
        if (type(measurement) is not dict or set(measurement) != _CONTACT_KEYS
                or type(measurement["contact"]) is not bool
                or measurement["instrument"] != _PROBE_INSTRUMENT
                or type(measurement["source_tick"]) is not int
                or measurement["source_tick"] != tick):
            raise NoosphereError("sensory measurement disagrees with the frame tick")
        candidate = ContactAssociationsV2.from_snapshot(model.snapshot())
        try:
            expected_learning = candidate.learn(
                observation, measurement, expected_prediction,
            )
        except CuriosityError:
            raise NoosphereError("sensory measurement is not replayable") from None
    if not _same_wire(learning, expected_learning):
        raise NoosphereError("sensory learning evidence is not replayable")
    if contact["association_state_sha256"] != digest(candidate.snapshot()):
        raise NoosphereError("sensory association state digest disagrees with replay")
    return candidate


def _stored_policy(manifest):
    if type(manifest) is not dict or manifest.get("schema") != MANIFEST_SCHEMA:
        raise NoosphereError("unknown Aquarium bootstrap schema")
    selectors = {key: manifest[key] for key in _MOTOR_KEYS & manifest.keys()}
    try:
        identity = by_selector(selectors)
    except PolicyIdentityError as error:
        raise NoosphereError(str(error)) from None
    policy_id, frame_contract = identity.inspection_id, identity.frame_contract

    sensory_mode = manifest.get("sensory_mode")
    if sensory_mode not in (None, "toy_probe", "toy_probe_v2"):
        raise NoosphereError("ambiguous or unknown stored sensory policy")
    # The legacy /1 contact table is capped at 4,000 updates and is not a
    # valid continuation of the v4 long-run contract.  The explicit /2
    # sensory selector is versioned and may be combined with v4 below.
    if policy_id == "local_cell_v4" and sensory_mode == "toy_probe":
        raise NoosphereError("local_cell_v4 journals require sensory_mode none or toy_probe_v2")
    if sensory_mode == "toy_probe_v2":
        if {
            "sensory_mode": sensory_mode,
            "sensory_schema": manifest.get("sensory_schema"),
            "sensory_model_schema": manifest.get("sensory_model_schema"),
            "contact_bridge_schema": manifest.get("contact_bridge_schema"),
            "occlusion_schema": manifest.get("occlusion_schema"),
            "sensory_reprobe_period_ticks": manifest.get("sensory_reprobe_period_ticks"),
        } != {
            "sensory_mode": "toy_probe_v2",
            "sensory_schema": SENSE_SCHEMA,
            "sensory_model_schema": SENSORY_MODEL_V2,
            "contact_bridge_schema": SENSORY_BRIDGE_V2,
            "occlusion_schema": OCCLUSION_SCHEMA,
            "sensory_reprobe_period_ticks": 2,
        }:
            raise NoosphereError("ambiguous or unknown stored sensory policy")
        policy_id += "+toy_probe_v2"

    temporal = {key: manifest[key] for key in _TEMPORAL_FIELDS.keys() & manifest.keys()}
    if temporal and (temporal != _TEMPORAL_FIELDS or policy_id != "motor_play_v1"
                     or manifest.get("sensory_mode") != "toy_probe"):
        raise NoosphereError("ambiguous or unknown stored temporal policy")
    return policy_id, frame_contract, temporal.get("temporal_schema"), sensory_mode


def _object_field(event, name):
    value = event.get(name)
    if type(value) is not dict:
        raise NoosphereError("frame disagrees with its pinned policy schema")
    return value


def _check_policy_frame(
        frame, contract, temporal_schema, sensory_mode=None,
        sensory_model=None):
    if frame.get("schema") != FRAME_SCHEMA:
        raise NoosphereError("unknown Aquarium frame schema")
    if contract is None:
        if "developmental" in frame or "developmental_controller" in frame:
            raise NoosphereError("unexpected developmental frame in scripted journal")
    else:
        state_schema, learning_schema, action_source = contract
        if state_schema == MOTOR_PLAY:
            motor = _object_field(frame, "developmental_controller")
            intent = _object_field(motor, "intent")
            if ("developmental" in frame or motor.get("schema") != state_schema
                    or intent.get("schema") != state_schema):
                raise NoosphereError("frame disagrees with its pinned policy schema")
        else:
            motor = _object_field(frame, "developmental")
            if ("developmental_controller" in frame or motor.get("schema") != state_schema
                    or _object_field(motor, "intent").get("source") != action_source
                    or _object_field(motor, "learning").get("schema") != learning_schema):
                raise NoosphereError("frame disagrees with its pinned policy schema")
            if state_schema == V4_SCHEMA:
                observation = _object_field(motor, "observation")
                intent = _object_field(motor, "intent")
                learning = _object_field(motor, "learning")
                tick = frame["tick"]  # Verified as exact int by inspect_event.
                expected_epoch = {
                    "epoch_index": tick // EPOCH_TICKS,
                    "epoch_tick": tick % EPOCH_TICKS,
                }
                def wrong_clock(fields, expected):
                    # Python True == 1, but JSON bool is not a wire integer.
                    return any(type(fields.get(key)) is not int or
                               fields[key] != value for key, value in expected.items())
                if (observation.get("schema") != EPOCH_SENSE_SCHEMA
                        or wrong_clock(observation, {"absolute_tick": tick, **expected_epoch})
                        or wrong_clock(intent, {"tick": tick, **expected_epoch})
                        or wrong_clock(learning, expected_epoch)):
                    raise NoosphereError("frame disagrees with its pinned epoch schema")
    if temporal_schema is not None:
        if _object_field(frame, "range_guidance").get("schema") != _GUIDANCE:
            raise NoosphereError("frame disagrees with its pinned temporal schema")
    elif "range_guidance" in frame:
        raise NoosphereError("unexpected temporal guidance frame")
    if sensory_mode is None and "toy_contact" in frame:
        # A valid event hash proves integrity, not permission to introduce
        # sensory observations absent from the pinned bootstrap selector.
        raise NoosphereError("unexpected sensory frame without a pinned sensory policy")
    if sensory_mode == "toy_probe_v2":
        contact = _object_field(frame, "toy_contact")
        sensory_model = _check_sensory_v2_frame(
            contact, frame["tick"], sensory_model)
    elif sensory_mode == "toy_probe":
        contact = _object_field(frame, "toy_contact")
        if contact.get("schema") != SENSORY_BRIDGE_V1:
            raise NoosphereError("frame disagrees with its pinned sensory schema")
    return sensory_model


def inspect_journal(path, *, external_expected_head=None):
    """Return policy and integrity metadata without exporting event content.

    This identifies the wire contract and verifies the Noosphere chain.
    Deterministic replay remains the responsibility of the matching runtime.
    """
    if external_expected_head is not None and (
        type(external_expected_head) is not str or _HEAD.fullmatch(external_expected_head) is None
    ):
        raise NoosphereError("expected head must be a lowercase SHA-256")
    policy = contract = temporal = sensory_mode = None
    epoch_index = 0
    dt_ns = None
    frames = 0
    sensory_model = None

    def inspect_event(seq, event):
        nonlocal policy, contract, temporal, sensory_mode, frames, epoch_index, dt_ns
        nonlocal sensory_model
        if seq > MAX_REPLAY_EVENTS:
            raise NoosphereError("Aquarium inspection exceeds its event limit")
        if event["source"] != "mojum.aquarium.fixture" or event["visibility"] != "PRIVATE":
            raise NoosphereError("journal contains a non-Aquarium event")
        topic = event["topic"]
        if seq == 1:
            if topic != "aquarium.bootstrap" or event["event_id"] != "aquarium-bootstrap":
                raise NoosphereError("first event must bootstrap Aquarium")
            manifest = event["payload"]
            policy, contract, temporal, sensory_mode = _stored_policy(manifest)
            dt_ns = manifest.get("dt_ns")
            # Canonical v4 and its versioned sensory composition share the
            # same strict bootstrap clock contract as runtime Aquarium.
            if policy in ("local_cell_v4", "local_cell_v4+toy_probe_v2") and (
                    type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000):
                raise NoosphereError("invalid v4 bootstrap dt_ns")
            if manifest.get("agent_id") != event["agent_id"]:
                raise NoosphereError("Aquarium bootstrap owner mismatch")
        elif topic == "aquarium.frame":
            frame = event["payload"]
            if type(frame) is not dict or type(frame.get("tick")) is not int or frame["tick"] != frames:
                raise NoosphereError("invalid Aquarium frame sequence")
            if policy in ("local_cell_v4", "local_cell_v4+toy_probe_v2"):
                if (event["event_id"] != f"frame-{frames:012d}"
                        or type(dt_ns) is not int
                        or type(event.get("observed_at_ns")) is not int
                        or event["observed_at_ns"] != frames * dt_ns):
                    raise NoosphereError("noncanonical v4 frame envelope")
                if frames // EPOCH_TICKS != epoch_index:
                    raise NoosphereError("missing durable v4 epoch marker before frame")
                # The manifest pins max_absolute_ticks.  The event ceiling
                # normally runs out first, but inspection must fail closed on
                # a frame at or beyond the pinned cap instead of certifying a
                # tick the runtime can never write.
                if frames >= MAX_ABSOLUTE_TICKS:
                    raise NoosphereError("v4 frame exceeds the pinned absolute-tick cap")
            sensory_model = _check_policy_frame(
                frame, contract, temporal, sensory_mode, sensory_model)
            frames += 1
        elif topic == "aquarium.epoch":
            if policy not in ("local_cell_v4", "local_cell_v4+toy_probe_v2"):
                raise NoosphereError("epoch marker is not allowed by stored policy")
            payload = event["payload"]
            expected_index = epoch_index + 1
            if (frames == 0 or frames % EPOCH_TICKS != 0
                    or expected_index != frames // EPOCH_TICKS
                    or not isinstance(payload, dict)
                    or set(payload) != {
                        "schema", "absolute_tick", "epoch_index",
                        "previous_epoch_index", "epoch_tick", "source",
                    }
                    or payload["schema"] != EPOCH_EVENT_SCHEMA
                    or any(type(payload.get(field)) is not int for field in (
                        "absolute_tick", "epoch_index",
                        "previous_epoch_index", "epoch_tick"))
                    or payload["absolute_tick"] != frames
                    or payload["epoch_index"] != expected_index
                    or payload["previous_epoch_index"] != epoch_index
                    or payload["epoch_tick"] != 0
                    or payload["source"] != "explicit_bounded_epoch_rollover_without_reset"
                    or event["event_id"] != f"epoch-{expected_index:08d}"
                    or dt_ns is None
                    or event["observed_at_ns"] != frames * dt_ns):
                raise NoosphereError("invalid or noncanonical epoch marker")
            epoch_index = expected_index
        elif policy in ("local_cell_v4", "local_cell_v4+toy_probe_v2"):
            # V4 Aquarium replay accepts only frames and durable epoch markers.
            # A valid SQLite hash chain is not proof of a valid motor-wire ABI.
            raise NoosphereError("unsupported local-cell v4 journal topic")
        elif topic not in {"genesis.acquisition", "development.advance", "identity.assigned"}:
            raise NoosphereError("unknown Aquarium event topic")

    with _open_readonly(path) as db:
        db.execute("BEGIN")
        metadata = dict(db.execute("SELECT k,v FROM metadata"))
        if set(metadata) != {"schema", "owner"} or metadata["schema"] != STORE_SCHEMA:
            raise NoosphereError("not a recognized Noosphere database")
        owner = _text(metadata["owner"], "owner")
        verified = _verify_chain(
            db, owner, external_expected_head=external_expected_head,
            strict_wire=True, inspect_event=inspect_event,
        )
    if policy is None:
        raise NoosphereError("no committed Aquarium bootstrap")
    result = {
        "schema": "mojum.aquarium.wire-inspection/1",
        "format": policy,
        "state_schema": contract[0] if contract else None,
        "temporal_schema": temporal,
        "frames": frames,
        "events": verified["count"],
        "journal_head": verified["head"],
        "integrity": ("chain-and-independent-head-checked" if external_expected_head is not None
                      else "chain-checked-not-authenticated"),
    }
    if sensory_mode == "toy_probe_v2":
        result["sensory_schema"] = SENSORY_MODEL_V2
    if policy in ("local_cell_v4", "local_cell_v4+toy_probe_v2"):
        result["epoch_schema"] = EPOCH_SENSE_SCHEMA
        result["epoch_ticks"] = EPOCH_TICKS
        result["max_absolute_ticks"] = MAX_ABSOLUTE_TICKS
    return result


def list_policies():
    """Describe every distinct persisted identity without opening a journal."""
    return {
        "schema": IDENTITY_SCHEMA,
        "identities": [as_public_record(item) for item in POLICIES],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--expected-head")
    parser.add_argument(
        "--list-policies", action="store_true",
        help="print every distinct persisted policy identity and exit",
    )
    args = parser.parse_args()
    if args.list_policies:
        print(json.dumps(list_policies(), sort_keys=True))
        return
    if args.db is None:
        parser.error("--db is required unless --list-policies is used")
    try:
        info = inspect_journal(args.db, external_expected_head=args.expected_head)
    except NoosphereError as error:
        parser.error(str(error))
    print(json.dumps(info, sort_keys=True))


if __name__ == "__main__":
    main()
