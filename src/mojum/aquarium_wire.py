"""Identify a stored Aquarium policy from a read-only, chain-checked SQLite journal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .aquarium import MANIFEST_SCHEMA, MAX_REPLAY_EVENTS, SCHEMA as FRAME_SCHEMA
from .developmental import SCHEMA as MOTOR_PLAY
from .noosphere import NoosphereError, STORE_SCHEMA, _open_readonly, _text, _verify_chain


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

    temporal = {key: manifest[key] for key in _TEMPORAL_FIELDS.keys() & manifest.keys()}
    if temporal and (temporal != _TEMPORAL_FIELDS or policy_id != "motor_play_v1"
                     or manifest.get("sensory_mode") != "toy_probe"):
        raise NoosphereError("ambiguous or unknown stored temporal policy")
    return policy_id, frame_contract, temporal.get("temporal_schema")


def _object_field(event, name):
    value = event.get(name)
    if type(value) is not dict:
        raise NoosphereError("frame disagrees with its pinned policy schema")
    return value


def _check_policy_frame(frame, contract, temporal_schema):
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
    if temporal_schema is not None:
        if _object_field(frame, "range_guidance").get("schema") != _GUIDANCE:
            raise NoosphereError("frame disagrees with its pinned temporal schema")
    elif "range_guidance" in frame:
        raise NoosphereError("unexpected temporal guidance frame")


def inspect_journal(path, *, external_expected_head=None):
    """Return policy and integrity metadata without exporting event content.

    This identifies the wire contract and verifies the Noosphere chain.
    Deterministic replay remains the responsibility of the matching runtime.
    """
    if external_expected_head is not None and (
        type(external_expected_head) is not str or _HEAD.fullmatch(external_expected_head) is None
    ):
        raise NoosphereError("expected head must be a lowercase SHA-256")
    policy = contract = temporal = None
    frames = 0

    def inspect_event(seq, event):
        nonlocal policy, contract, temporal, frames
        if seq > MAX_REPLAY_EVENTS:
            raise NoosphereError("Aquarium inspection exceeds its event limit")
        if event["source"] != "mojum.aquarium.fixture" or event["visibility"] != "PRIVATE":
            raise NoosphereError("journal contains a non-Aquarium event")
        topic = event["topic"]
        if seq == 1:
            if topic != "aquarium.bootstrap" or event["event_id"] != "aquarium-bootstrap":
                raise NoosphereError("first event must bootstrap Aquarium")
            manifest = event["payload"]
            policy, contract, temporal = _stored_policy(manifest)
            if manifest.get("agent_id") != event["agent_id"]:
                raise NoosphereError("Aquarium bootstrap owner mismatch")
        elif topic == "aquarium.frame":
            frame = event["payload"]
            if type(frame) is not dict or type(frame.get("tick")) is not int or frame["tick"] != frames:
                raise NoosphereError("invalid Aquarium frame sequence")
            _check_policy_frame(frame, contract, temporal)
            frames += 1
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
    return {
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
