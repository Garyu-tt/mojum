"""Identify a stored Aquarium policy from a read-only, chain-checked SQLite journal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .aquarium import (
    EPOCH_EVENT_SCHEMA, MANIFEST_SCHEMA, MAX_REPLAY_EVENTS, MAX_V4_FRAMES,
    SCHEMA as FRAME_SCHEMA,
)
from .cell_policy_v4 import LEARNING_SCHEMA as V4_OUTCOME, SCHEMA as V4_SCHEMA
from .epoch_sense_v2 import (
    EPOCH_TICKS, SCHEMA as EPOCH_SENSE_SCHEMA,
)
from .developmental import SCHEMA as MOTOR_PLAY
from .noosphere import NoosphereError, STORE_SCHEMA, _open_readonly, _text, _verify_chain
from .sensory_associations_v2 import SCHEMA as SENSORY_MODEL_V2, LEARNING_SCHEMA as SENSORY_LEARNING_V2
from .sensory_bridge import OCCLUSION_SCHEMA
from .sensory_bridge_v2 import SCHEMA as SENSORY_BRIDGE_V2
from .sensory_play import SENSE_SCHEMA


_COARSE_V1 = "mojum.developmental.explorer/1"
_LOCAL_CELL_A = "mojum.developmental.explorer.local-cell/2"
_LOCAL_CELL_B = "mojum.developmental.local-cell/2"
_V2B_OUTCOME = "mojum.developmental.local-cell.outcome/2"
_GUIDANCE = "mojum.developmental.range-guidance/1"
_HEAD = re.compile(r"[0-9a-f]{64}\Z")

# The two draft runtimes both used the spelling local_cell_v2. Their
# persisted selector sets differ, as do their intent and outcome contracts.
_MOTOR_KEYS = {
    "exploration_mode", "developmental_schema", "developmental_policy",
    "motor_policy", "motor_mode", "motor_policy_schema",
    "epoch_schema", "epoch_ticks",
}
_POLICY_SELECTORS = (
    ({}, "scripted_v1", None),
    ({"motor_mode": "developmental_fixture", "motor_policy_schema": MOTOR_PLAY},
     "motor_play_v1", (MOTOR_PLAY, None, None)),
    ({"exploration_mode": "goal_free", "developmental_schema": _COARSE_V1},
     "legacy_coarse_v1", (_COARSE_V1, _COARSE_V1, "bounded_goal_free_motor_play")),
    ({"exploration_mode": "goal_free", "developmental_schema": _LOCAL_CELL_A,
      "motor_policy": "local_cell_v2", "motor_policy_schema": _LOCAL_CELL_A},
     "local_cell_v2_a_unreleased",
     (_LOCAL_CELL_A, _LOCAL_CELL_A, "bounded_local_cell_motor_play_v2")),
    ({"exploration_mode": "goal_free", "developmental_schema": _LOCAL_CELL_B,
      "developmental_policy": "local_cell_v2"},
     "local_cell_v2_b_unreleased",
     (_LOCAL_CELL_B, _V2B_OUTCOME, "bounded_goal_free_local_cell_motor_play_v2")),
    ({"exploration_mode": "goal_free", "developmental_schema": "mojum.developmental.local-cell/3",
      "developmental_policy": "local_cell_v3"}, "local_cell_v3",
     ("mojum.developmental.local-cell/3", "mojum.developmental.local-cell.outcome/3",
      "bounded_goal_free_local_cell_motor_play_v3")),
    ({"exploration_mode": "goal_free", "developmental_schema": V4_SCHEMA,
      "developmental_policy": "local_cell_v4", "epoch_schema": EPOCH_SENSE_SCHEMA,
      "epoch_ticks": EPOCH_TICKS}, "local_cell_v4",
     (V4_SCHEMA, V4_OUTCOME, "bounded_goal_free_local_cell_motor_play_v4")),
)
_TEMPORAL_FIELDS = {
    "temporal_mode": "range_guided",
    "temporal_schema": "mojum.temporal.motor-play-range/1",
    "motor_guidance_schema": _GUIDANCE,
}


def _stored_policy(manifest):
    if type(manifest) is not dict or manifest.get("schema") != MANIFEST_SCHEMA:
        raise NoosphereError("unknown Aquarium bootstrap schema")
    selectors = {key: manifest[key] for key in _MOTOR_KEYS & manifest.keys()}
    for known, policy_id, frame_contract in _POLICY_SELECTORS:
        if selectors == known:
            break
    else:
        raise NoosphereError("ambiguous or unknown stored Aquarium policy")

    sensory_mode = manifest.get("sensory_mode")
    if sensory_mode not in (None, "toy_probe", "toy_probe_v2"):
        raise NoosphereError("ambiguous or unknown stored sensory policy")
    if policy_id == "local_cell_v4" and sensory_mode is not None:
        raise NoosphereError("local_cell_v4 cannot carry a sensory policy")
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


def _check_policy_frame(frame, contract, temporal_schema, sensory_mode=None):
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
            intent = _object_field(motor, "intent")
            learning = _object_field(motor, "learning")
            if ("developmental_controller" in frame or motor.get("schema") != state_schema
                    or intent.get("source") != action_source
                    or learning.get("schema") != learning_schema):
                raise NoosphereError("frame disagrees with its pinned policy schema")
            if state_schema == V4_SCHEMA:
                observation = _object_field(motor, "observation")
                frame_tick = frame.get("tick")
                if (
                    observation.get("schema") != EPOCH_SENSE_SCHEMA
                    or type(observation.get("absolute_tick")) is not int
                    or observation["absolute_tick"] != frame_tick
                    or type(observation.get("epoch_index")) is not int
                    or type(observation.get("epoch_tick")) is not int
                    or observation["epoch_index"] != frame_tick // EPOCH_TICKS
                    or observation["epoch_tick"] != frame_tick % EPOCH_TICKS
                    or intent.get("epoch_index") != observation["epoch_index"]
                    or intent.get("epoch_tick") != observation["epoch_tick"]
                    or learning.get("epoch_index") != observation["epoch_index"]
                    or learning.get("epoch_tick") != observation["epoch_tick"]
                ):
                    raise NoosphereError("frame disagrees with its pinned epoch schema")
    if temporal_schema is not None:
        if _object_field(frame, "range_guidance").get("schema") != _GUIDANCE:
            raise NoosphereError("frame disagrees with its pinned temporal schema")
    elif "range_guidance" in frame:
        raise NoosphereError("unexpected temporal guidance frame")
    if sensory_mode == "toy_probe_v2":
        contact = _object_field(frame, "toy_contact")
        if contact.get("schema") != SENSORY_BRIDGE_V2:
            raise NoosphereError("frame disagrees with its pinned sensory schema")
        learning = contact.get("learning")
        if learning is not None and (type(learning) is not dict or
                                    learning.get("schema") != SENSORY_LEARNING_V2):
            raise NoosphereError("frame disagrees with its pinned sensory schema")
    elif sensory_mode == "toy_probe" and "toy_contact" not in frame:
        raise NoosphereError("frame disagrees with its pinned sensory schema")


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
    frames = 0
    epoch_index = 0
    dt_ns = None

    def inspect_event(seq, event):
        nonlocal policy, contract, temporal, sensory_mode, frames, epoch_index, dt_ns
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
            if manifest.get("agent_id") != event["agent_id"]:
                raise NoosphereError("Aquarium bootstrap owner mismatch")
            dt_ns = manifest.get("dt_ns")
            if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
                raise NoosphereError("invalid Aquarium virtual clock")
        elif topic == "aquarium.frame":
            frame = event["payload"]
            if type(frame) is not dict or type(frame.get("tick")) is not int or frame["tick"] != frames:
                raise NoosphereError("invalid Aquarium frame sequence")
            if policy == "local_cell_v4":
                if frames >= MAX_V4_FRAMES:
                    raise NoosphereError("local_cell_v4 absolute tick budget exceeded")
                if frames // EPOCH_TICKS != epoch_index:
                    raise NoosphereError("missing durable epoch marker before v4 frame")
            if event["observed_at_ns"] != frames * dt_ns:
                raise NoosphereError("Aquarium frame time is not synchronized")
            _check_policy_frame(frame, contract, temporal, sensory_mode)
            frames += 1
        elif topic == "aquarium.epoch":
            if policy != "local_cell_v4":
                raise NoosphereError("unknown Aquarium epoch event")
            payload = event.get("payload")
            expected_epoch = frames // EPOCH_TICKS
            if (frames == 0 or frames % EPOCH_TICKS != 0
                    or type(payload) is not dict
                    or set(payload) != {"schema", "absolute_tick", "from_epoch",
                                         "to_epoch", "epoch_tick", "source"}
                    or payload["schema"] != EPOCH_EVENT_SCHEMA
                    or type(payload["absolute_tick"]) is not int
                    or type(payload["from_epoch"]) is not int
                    or type(payload["to_epoch"]) is not int
                    or type(payload["epoch_tick"]) is not int
                    or payload["absolute_tick"] != frames
                    or payload["from_epoch"] != expected_epoch - 1
                    or payload["to_epoch"] != expected_epoch
                    or expected_epoch != epoch_index + 1
                    or payload["epoch_tick"] != 0
                    or payload["source"] != "explicit_monotonic_synthetic_epoch_boundary"
                    or event["event_id"] != f"epoch-{expected_epoch:08d}"
                    or event["observed_at_ns"] != frames * dt_ns
                    ):
                raise NoosphereError("invalid Aquarium epoch boundary")
            epoch_index = expected_epoch
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
    if policy == "local_cell_v4":
        result["epoch_transition_schema"] = EPOCH_EVENT_SCHEMA
        result["epoch_index"] = epoch_index
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    try:
        info = inspect_journal(args.db, external_expected_head=args.expected_head)
    except NoosphereError as error:
        parser.error(str(error))
    print(json.dumps(info, sort_keys=True))


if __name__ == "__main__":
    main()
