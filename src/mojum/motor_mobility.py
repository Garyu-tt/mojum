"""Read-only audit of *measured* point-room mobility in an Aquarium journal.

The canonical developmental fixture reports blocked moves inside its frames.
A blocked-only view can call a policy "safe" while it stops moving or keeps
re-entering the same few positions. This module re-reads the committed PRIVATE
journal without a writable SQLite handle and reports attempts, successful
movement, rest/idle behaviour, visited positions and path length.

This is a bounded heuristic report over synthetic point-room frames. It is NOT
a navigation score, an intelligence/emotion claim, a physics/MuJoCo result or
evidence about real actuation. Gate thresholds are versioned below so a future
policy contract can report different limits instead of reinterpreting history.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .aquarium import MANIFEST_SCHEMA, MAX_REPLAY_EVENTS, SCHEMA as FRAME_SCHEMA
from .noosphere import NoosphereError, STORE_SCHEMA, _open_readonly, _text, _verify_chain

SCHEMA = "mojum.aquarium.mobility-audit/1"
GATE_SCHEMA = "mojum.aquarium.mobility-gates/1"
MOTOR_SCHEMA_PREFIX = "mojum.developmental.motor-play/"
MOVE_KINDS = frozenset(("east", "north", "west", "south"))
REST_KIND = "rest"

# Versioned, deliberately conservative thresholds. They flag *silent collapse*,
# never a policy's right to rest, and are not a quality score.
MIN_REST_STREAK = 10
REST_STREAK_FRACTION = 10  # streak >= max(MIN_REST_STREAK, frames // 10)
IDLE_FRACTION = 0.5
MIN_MOVES_FOR_REVISITS = 20
MIN_REVISITS_PER_POSITION = 4

_FRAME_TOPICS = frozenset((
    "aquarium.frame", "genesis.acquisition", "development.advance",
    "identity.assigned",
))
_REQUIRED_FRAME_FIELDS = frozenset((
    "schema", "tick", "time_ns", "genome_revision", "phenotype",
    "graph_execution_sha256", "interoception", "brain", "learning_observer",
    "world", "movement_request", "physical_model", "developmental_controller",
))
# Optional fields belong to other opt-in modes; they are not audit inputs.
_OPTIONAL_FRAME_FIELDS = frozenset(("toy_contact", "range_guidance"))


class MobilityAuditError(ValueError):
    """Reject an unreadable, non-developmental or noncanonical journal."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _position(value):
    if type(value) not in (list, tuple) or len(value) != 2:
        raise MobilityAuditError("invalid committed world position")
    if not all(_finite(v) for v in value):
        raise MobilityAuditError("nonfinite committed world position")
    return float(value[0]), float(value[1])


def _action_fields(frame, policy_schema):
    if type(frame) is not dict or not _REQUIRED_FRAME_FIELDS <= frame.keys() \
            or not frame.keys() <= _REQUIRED_FRAME_FIELDS | _OPTIONAL_FRAME_FIELDS:
        raise MobilityAuditError("invalid developmental Aquarium frame")
    if frame["schema"] != FRAME_SCHEMA:
        raise MobilityAuditError("unknown Aquarium frame schema")
    controller = frame["developmental_controller"]
    if (type(controller) is not dict
            or controller.get("schema") != policy_schema
            or controller.get("action_source") != "internal_committed_prior_outcomes_only"
            or controller.get("real_actuation") is not False):
        raise MobilityAuditError("frame is not a goal-free committed motor frame")
    intent, outcome = controller.get("intent"), controller.get("outcome")
    if type(intent) is not dict or type(outcome) is not dict:
        raise MobilityAuditError("frame is missing measured motor intent/outcome")
    kind = intent.get("kind")
    if type(kind) is not str or kind not in MOVE_KINDS | {REST_KIND}:
        raise MobilityAuditError("unknown committed motor action")
    blocked = outcome.get("observed_blocked")
    if type(blocked) is not bool:
        raise MobilityAuditError("committed motor outcome is not boolean")
    if kind == REST_KIND and blocked:
        raise MobilityAuditError("rest cannot be blocked in the point-room fixture")
    world = frame.get("world")
    if type(world) is not dict or type(world.get("blocked")) is not bool:
        raise MobilityAuditError("frame is missing a measured world outcome")
    if world["blocked"] != blocked:
        raise MobilityAuditError("motor and world outcomes disagree")
    return kind, blocked, _position(world.get("position"))


def _manifest_policy(manifest):
    if type(manifest) is not dict or manifest.get("schema") != MANIFEST_SCHEMA:
        raise MobilityAuditError("unknown Aquarium bootstrap schema")
    if (manifest.get("motor_mode") != "developmental_fixture"
            or type(manifest.get("motor_policy_schema")) is not str
            or not manifest["motor_policy_schema"].startswith(MOTOR_SCHEMA_PREFIX)):
        raise MobilityAuditError("journal has no measured developmental motor policy")
    return {
        "motor_mode": manifest["motor_mode"],
        "motor_policy_schema": manifest["motor_policy_schema"],
        "temporal_schema": manifest.get("temporal_schema"),
    }


def summarize(manifest, frames):
    """Compute bounded mobility metrics from ordered committed frame payloads."""
    policy = _manifest_policy(manifest)
    if type(frames) not in (list, tuple):
        raise MobilityAuditError("frames must be an ordered sequence")
    if len(frames) > MAX_REPLAY_EVENTS:
        raise MobilityAuditError("mobility audit exceeds its frame limit")

    rest_actions = move_actions = blocked_attempts = successful_moves = 0
    longest_rest_streak = streak = 0
    positions = set()
    path_length = 0.0
    previous = None
    for expected_tick, frame in enumerate(frames):
        if type(frame) is not dict or type(frame.get("tick")) is not int:
            raise MobilityAuditError("invalid frame tick")
        if frame["tick"] != expected_tick:
            raise MobilityAuditError("frame tick discontinuity")
        kind, blocked, position = _action_fields(frame, policy["motor_policy_schema"])
        if kind == REST_KIND:
            rest_actions += 1
            streak += 1
            longest_rest_streak = max(longest_rest_streak, streak)
        else:
            move_actions += 1
            streak = 0
            if blocked:
                blocked_attempts += 1
            else:
                successful_moves += 1
        positions.add(position)
        if previous is not None:
            path_length += math.hypot(position[0] - previous[0], position[1] - previous[1])
        previous = position

    frames_count = len(frames)
    idle_fraction = rest_actions / frames_count if frames_count else 0.0
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    coverage_span = [max(xs) - min(xs), max(ys) - min(ys)] if positions else [0.0, 0.0]
    rest_gate = bool(
        frames_count
        and idle_fraction >= IDLE_FRACTION
        and longest_rest_streak >= max(MIN_REST_STREAK, frames_count // REST_STREAK_FRACTION)
    )
    revisit_gate = bool(
        move_actions >= MIN_MOVES_FOR_REVISITS
        and positions
        and len(positions) * MIN_REVISITS_PER_POSITION <= successful_moves
    )
    if frames_count == 0:
        verdict = "NO_FRAMES"
    elif rest_gate:
        verdict = "REST_COLLAPSE_SUSPECTED"
    elif revisit_gate:
        verdict = "REVISIT_CONCENTRATION_SUSPECTED"
    else:
        verdict = "MOBILITY_OBSERVED"

    return {
        "schema": SCHEMA,
        **policy,
        "frames": frames_count,
        "rest_actions": rest_actions,
        "move_actions": move_actions,
        "blocked_attempts": blocked_attempts,
        "successful_moves": successful_moves,
        "idle_fraction": idle_fraction,
        "longest_rest_streak": longest_rest_streak,
        "distinct_positions": len(positions),
        "coverage_span_m": coverage_span,
        "path_length_m": path_length,
        "gates": {"rest_collapse": rest_gate, "revisit_concentration": revisit_gate},
        "gate_schema": GATE_SCHEMA,
        "limits": {
            "min_rest_streak": MIN_REST_STREAK,
            "rest_streak_divisor": REST_STREAK_FRACTION,
            "idle_fraction": IDLE_FRACTION,
            "min_moves_for_revisits": MIN_MOVES_FOR_REVISITS,
            "min_revisits_per_position": MIN_REVISITS_PER_POSITION,
        },
        "verdict": verdict,
        "source": "committed_private_point_room_frames_not_navigation_score",
    }


def audit_journal(path, *, external_expected_head=None):
    """Stream a read-only journal and summarize its committed motor frames."""
    manifest = None
    frames = []

    def inspect_event(seq, event):
        nonlocal manifest
        if seq > MAX_REPLAY_EVENTS:
            raise NoosphereError("mobility audit exceeds its event limit")
        if event["source"] != "mojum.aquarium.fixture" or event["visibility"] != "PRIVATE":
            raise NoosphereError("journal contains a non-Aquarium event")
        topic = event["topic"]
        if seq == 1:
            if topic != "aquarium.bootstrap" or event["event_id"] != "aquarium-bootstrap":
                raise NoosphereError("first event must bootstrap Aquarium")
            manifest = event["payload"]
        elif topic not in _FRAME_TOPICS:
            raise NoosphereError("unknown Aquarium event topic")
        elif topic == "aquarium.frame":
            frame = event["payload"]
            if type(frame) is not dict or frame.get("schema") != FRAME_SCHEMA:
                raise NoosphereError("unknown Aquarium frame schema")
            if type(frame.get("tick")) is not int or frame["tick"] != len(frames):
                raise NoosphereError("invalid Aquarium frame sequence")
            frames.append(frame)

    if external_expected_head is not None and (
        type(external_expected_head) is not str
        or len(external_expected_head) != 64
        or any(c not in "0123456789abcdef" for c in external_expected_head)
    ):
        raise MobilityAuditError("expected head must be a lowercase SHA-256")
    with _open_readonly(path) as db:
        db.execute("BEGIN")
        metadata = dict(db.execute("SELECT k,v FROM metadata"))
        if set(metadata) != {"schema", "owner"} or metadata["schema"] != STORE_SCHEMA:
            raise MobilityAuditError("not a recognized Noosphere database")
        owner = _text(metadata["owner"], "owner")
        verified = _verify_chain(
            db, owner, external_expected_head=external_expected_head,
            strict_wire=True, inspect_event=inspect_event,
        )
    if manifest is None:
        raise MobilityAuditError("no committed Aquarium bootstrap")
    report = summarize(manifest, frames)
    report.update({
        "events": verified["count"],
        "journal_head": verified["head"],
        "integrity": ("chain-and-independent-head-checked"
                      if external_expected_head is not None
                      else "chain-checked-not-authenticated"),
    })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    try:
        report = audit_journal(args.db, external_expected_head=args.expected_head)
    except (MobilityAuditError, NoosphereError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
