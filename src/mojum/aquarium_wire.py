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
from .cell_policy_v3_escape import (
    MAX_CONSECUTIVE_RESTS as V3E_MAX_CONSECUTIVE_RESTS,
    REPROBE_BLOCKED_BEFORE_REST as V3E_REPROBE_BLOCKED_BEFORE_REST,
    SCHEMA as V3_ESCAPE_SCHEMA,
)
from .cell_policy_v4 import SCHEMA as V4_SCHEMA
from .cell_policy_v5 import SCHEMA as V5_SCHEMA
from .developmental import SCHEMA as MOTOR_PLAY
from .epoch_sense_v2 import EPOCH_TICKS, SCHEMA as EPOCH_SENSE_SCHEMA
from .local_cell_sense import ACTIONS as CELL_ACTIONS, MAX_EPISODE_TICKS
from .noosphere import NoosphereError, STORE_SCHEMA, _open_readonly, _text, _verify_chain
from .policy_identity import (
    MOTOR_SELECTOR_KEYS, POLICIES, SCHEMA as IDENTITY_SCHEMA,
    PolicyIdentityError, as_public_record, by_selector,
)
from .sensory_associations_v2 import SCHEMA as SENSORY_MODEL_V2, LEARNING_SCHEMA as SENSORY_LEARNING_V2
from .sensory_bridge import OCCLUSION_SCHEMA
from .sensory_bridge_v2 import SCHEMA as SENSORY_BRIDGE_V2
from .sensory_play import SENSE_SCHEMA


_GUIDANCE = "mojum.developmental.range-guidance/1"
_HEAD = re.compile(r"[0-9a-f]{64}\Z")
# Epoch-scoped identities share the explicit marker and frame-envelope ABI.
_EPOCH_POLICIES = ("local_cell_v4", "local_cell_v4+toy_probe_v2", "local_cell_v5")


def _epoch_tag(policy):
    return "v5" if policy == "local_cell_v5" else "v4"

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
_SENSORY_FIELDS = {
    "sensory_mode", "sensory_schema", "sensory_model_schema", "sensory_scene",
    "contact_bridge_schema", "occlusion_schema", "sensory_reprobe_period_ticks",
}


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
    # Preserve the already-shipped canonical v4 + versioned sensory-v2 ABI.
    # Legacy toy_probe is incompatible with epoch motors; v5 is sensorless.
    if policy_id == "local_cell_v4" and sensory_mode == "toy_probe":
        raise NoosphereError("local_cell_v4 requires sensory_mode none or toy_probe_v2")
    if policy_id == "local_cell_v5" and _SENSORY_FIELDS.intersection(manifest):
        raise NoosphereError("unexpected sensory fields in local_cell_v5 bootstrap")
    if sensory_mode == "toy_probe_v2":
        if policy_id not in ("motor_play_v1", "local_cell_v4"):
            raise NoosphereError(f"toy_probe_v2 is not allowed by stored policy {policy_id}")
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


def _check_escape_frame(frame, motor):
    """Reject an escape frame the runtime could never have committed.

    A valid hash chain proves integrity, not that the frame obeys the
    pinned ``Local Cell/3-escape`` contract.  These are the counters the
    policy must keep bounded; accepting an impossible frame here would
    certify a journal the runtime could never have written.
    """
    intent = _object_field(motor, "intent")
    learning = _object_field(motor, "learning")
    tick = frame["tick"]  # Verified as exact int by inspect_event.
    action = learning.get("action")
    measured = learning.get("measured_blocked")
    observed = learning.get("observed_outcomes")
    blocked = learning.get("blocked_outcomes")
    probe = learning.get("escape_probe")
    rests = learning.get("consecutive_rests")
    probes = learning.get("consecutive_blocked_probes")
    updates = learning.get("total_updates")
    evictions = learning.get("total_evictions")
    forgotten = learning.get("total_forgotten_observations")
    if (type(action) is not str or action not in CELL_ACTIONS
            or intent.get("action") != action
            or type(intent.get("tick")) is not int or intent["tick"] != tick
            or type(measured) is not bool
            or (action == "rest" and measured)
            or type(observed) is not int
            or not 1 <= observed <= MAX_EPISODE_TICKS
            or type(blocked) is not int or not 0 <= blocked <= observed
            # Rest is never a collision measurement in this profile.
            or (action == "rest" and blocked)
            # A re-probe can only be flagged when the action already has
            # measured blocked evidence.
            or type(probe) is not bool
            or (probe and (action == "rest" or blocked < 1))
            or type(rests) is not int
            or not 0 <= rests <= V3E_MAX_CONSECUTIVE_RESTS
            or type(probes) is not int
            or not 0 <= probes <= V3E_REPROBE_BLOCKED_BEFORE_REST
            or type(updates) is not int or updates != tick + 1
            or type(forgotten) is not int
            or not 0 <= forgotten <= updates
            or type(evictions) is not int
            or not 0 <= evictions <= forgotten):
        raise NoosphereError("frame disagrees with its pinned escape schema")
    evicted = learning.get("evicted_cell")
    dropped = learning.get("evicted_observations")
    if type(dropped) is not int:
        raise NoosphereError("frame disagrees with its pinned escape schema")
    if evicted is None:
        if dropped != 0:
            raise NoosphereError(
                "frame disagrees with its pinned escape schema")
    elif type(evicted) is not str or not evicted or dropped < 1:
        raise NoosphereError("frame disagrees with its pinned escape schema")


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
            if ("developmental_controller" in frame or motor.get("schema") != state_schema
                    or _object_field(motor, "intent").get("source") != action_source
                    or _object_field(motor, "learning").get("schema") != learning_schema):
                raise NoosphereError("frame disagrees with its pinned policy schema")
            if state_schema in (V4_SCHEMA, V5_SCHEMA):
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
            elif state_schema == V3_ESCAPE_SCHEMA:
                _check_escape_frame(frame, motor)
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
    epoch_index = 0
    dt_ns = None
    frames = 0

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
            dt_ns = manifest.get("dt_ns")
            # Runtime creation accepts only this bounded virtual-clock interval.
            # A read-only wire pass must not certify a bootstrap that cold
            # Aquarium replay rejects for a forged or missing clock period.
            if policy in _EPOCH_POLICIES and (
                    type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000):
                raise NoosphereError(
                    "invalid %s bootstrap dt_ns" % _epoch_tag(policy))
            if manifest.get("agent_id") != event["agent_id"]:
                raise NoosphereError("Aquarium bootstrap owner mismatch")
        elif topic == "aquarium.frame":
            frame = event["payload"]
            if type(frame) is not dict or type(frame.get("tick")) is not int or frame["tick"] != frames:
                raise NoosphereError("invalid Aquarium frame sequence")
            if policy in _EPOCH_POLICIES:
                if (event["event_id"] != f"frame-{frames:012d}"
                        or type(dt_ns) is not int
                        or type(event.get("observed_at_ns")) is not int
                        or event["observed_at_ns"] != frames * dt_ns):
                    raise NoosphereError(
                        "noncanonical %s frame envelope" % _epoch_tag(policy))
                if frames // EPOCH_TICKS != epoch_index:
                    raise NoosphereError(
                        "missing durable %s epoch marker before frame"
                        % _epoch_tag(policy))
                # The manifest pins max_absolute_ticks.  The event ceiling
                # normally runs out first, but inspection must fail closed on
                # a frame at or beyond the pinned cap instead of certifying a
                # tick the runtime can never write.
                if frames >= MAX_ABSOLUTE_TICKS:
                    raise NoosphereError(
                        "%s frame exceeds the pinned absolute-tick cap"
                        % _epoch_tag(policy))
            _check_policy_frame(frame, contract, temporal, sensory_mode)
            frames += 1
        elif topic == "aquarium.epoch":
            if policy not in _EPOCH_POLICIES:
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
        elif policy in _EPOCH_POLICIES:
            # Epoch Aquarium replay accepts only frames and durable markers.
            # A valid SQLite hash chain is not proof of a valid motor-wire ABI.
            raise NoosphereError(
                "unsupported local-cell %s journal topic" % _epoch_tag(policy))
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
    if policy in _EPOCH_POLICIES:
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
