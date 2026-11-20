"""Read-only, source-linked developmental diary from synthetic infant observations.

Reports *what happened*, not what an organism thinks, feels, or "is".
All sources are PRIVATE Noosphere events or explicitly supplied JSONL.
Neither reward nor policy is modified by computing this projection.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .infant_play import ACTIONS, SCHEMA as INFANT_SCHEMA
from .noosphere import LocalNoosphere


SCHEMA = "mojum.development.diary/1"
TRACE_SCHEMA = "mojum.observation.trace/1"
KINDS = (
    "first_observation", "first_rest", "first_successful_motion",
    "first_blocked_motion", "first_repeated_action",
    "first_confirmed_prediction", "first_prediction_surprise",
)
MAX_FRAMES = 9999


class DiaryError(ValueError):
    """Invalid trace, continuity, ownership or local report request."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _check_frame(frame, previous):
    if not isinstance(frame, dict) or frame.get("schema") != TRACE_SCHEMA:
        raise DiaryError("unsupported private observer trace")
    if frame.get("source") != "DETERMINISTIC_SYNTHETIC_TRACE_NOT_CONSCIOUS_EXPERIENCE":
        raise DiaryError("unexpected observation provenance")
    tick = frame.get("tick")
    if type(tick) is not int or tick < 0 or (previous is not None and tick != previous + 1):
        raise DiaryError("noncontiguous developmental trace")
    e, outcome, memory = frame.get("experiment"), frame.get("outcome"), frame.get("memory")
    assessment = frame.get("assessment")
    scene = frame.get("spectator_scene")
    if not all(isinstance(x, dict) for x in (e, outcome, memory, assessment, scene)):
        raise DiaryError("missing evidence fields")
    if scene.get("available_to_agent") is not False:
        raise DiaryError("viewer-only scene must not become a claimed sense")
    action = e.get("action")
    effect = outcome.get("actual_effect")
    expectation = e.get("expected_effect")
    error = memory.get("prediction_error")
    learned = memory.get("predicted_effect_after")
    score = assessment.get("score_0_to_5")
    tried = memory.get("times_tried")
    updates = memory.get("total_updates")
    if (
        action not in ACTIONS
        or type(e.get("novel_attempt")) is not bool
        or type(outcome.get("blocked")) is not bool
        or type(tried) is not int or not 1 <= tried <= MAX_FRAMES
        or type(updates) is not int or updates != tick + 1
        or not all(_finite(v) for v in (effect, expectation, error, learned))
        or not (0 <= effect <= 1 and 0 <= expectation <= 1 and 0 <= learned <= 1)
        or abs(error - (effect - expectation)) > 1e-9
        or (e["novel_attempt"] != (tried == 1))
        or assessment.get("metric") != "local_movement_effectiveness_not_personality_score"
        or assessment.get("grade_of") != "single_action_result_not_the_organism"
    ):
        raise DiaryError("inconsistent developmental evidence")
    if action == "rest":
        if effect != 0 or score is not None or outcome["blocked"]:
            raise DiaryError("rest is not a scored movement")
    elif not _finite(score) or not 0 <= score <= 5 or abs(score - round(effect * 5, 3)) > 1e-8:
        raise DiaryError("movement effect score mismatch")
    if outcome["blocked"] and effect != 0:
        raise DiaryError("blocked move cannot be effective")
    if (not isinstance(frame.get("inference"), dict)
            or frame["inference"].get("not") != "thought_or_feeling_or_general_reasoning"):
        raise DiaryError("unsupported inner-life inference")
    return tick


def summarize(frames, *, event_hashes=None, source_integrity="unverified_local_trace"):
    """Derive a deterministic compact report and evidence pointers.

    A verified SQLite caller passes committed per-event hashes; a raw JSONL
    trace never inherits journal integrity and must be labeled unverified.
    """
    if (not isinstance(frames, list) or not frames or len(frames) > MAX_FRAMES
            or source_integrity not in ("unverified_local_trace", "local_chain_verified_not_authenticated")):
        raise DiaryError("invalid or empty developmental episode window")
    if event_hashes is not None and (
        not isinstance(event_hashes, list) or len(event_hashes) != len(frames)
        or not all(isinstance(x, str) and len(x) == 64
                   and all(c in "0123456789abcdef" for c in x) for x in event_hashes)
    ):
        raise DiaryError("invalid event evidence hashes")
    if source_integrity == "local_chain_verified_not_authenticated" and event_hashes is None:
        raise DiaryError("verified report requires linked event hashes")
    first = None
    milestones = []
    counts = {
        action: {"attempts": 0, "blocked": 0, "successful_movements": 0,
                 "mean_measured_effect": 0.0, "prediction_absolute_error_sum": 0.0}
        for action in ACTIONS
    }
    seen = set()

    def emit(kind, index, frame):
        if kind in seen:
            return
        seen.add(kind)
        tick = frame["tick"]
        milestones.append({
            "kind": kind, "tick": tick,
            "event_id": f"infant-frame-{tick:012d}",
            "event_hash": event_hashes[index] if event_hashes is not None else None,
            "action": frame["experiment"]["action"],
            "evidence": {
                "observed_effect": frame["outcome"]["actual_effect"],
                "prediction_error": frame["memory"]["prediction_error"],
                "attempt_number": frame["memory"]["times_tried"],
                "blocked": frame["outcome"]["blocked"],
            },
        })

    for index, frame in enumerate(frames):
        tick = _check_frame(frame, first)
        first = tick
        action, outcome, memory = frame["experiment"]["action"], frame["outcome"], frame["memory"]
        c = counts[action]
        c["attempts"] += 1
        c["blocked"] += int(outcome["blocked"])
        c["successful_movements"] += int(action != "rest" and outcome["actual_effect"] > 0)
        c["mean_measured_effect"] += outcome["actual_effect"]
        c["prediction_absolute_error_sum"] += abs(memory["prediction_error"])
        if index == 0:
            emit("first_observation", index, frame)
        if action == "rest":
            emit("first_rest", index, frame)
        if action != "rest" and outcome["actual_effect"] > 0:
            emit("first_successful_motion", index, frame)
        if outcome["blocked"]:
            emit("first_blocked_motion", index, frame)
        if memory["times_tried"] > 1:
            emit("first_repeated_action", index, frame)
            if action != "rest" and abs(memory["prediction_error"]) <= 0.05:
                emit("first_confirmed_prediction", index, frame)
            if abs(memory["prediction_error"]) >= 0.25:
                emit("first_prediction_surprise", index, frame)
    for record in counts.values():
        n = record["attempts"]
        record["mean_measured_effect"] = round(record["mean_measured_effect"] / n, 6) if n else None
        record["mean_absolute_prediction_error"] = round(
            record.pop("prediction_absolute_error_sum") / n, 6
        ) if n else None
    return {
        "schema": SCHEMA,
        "status": "OBSERVABLE_SYNTHETIC_DEVELOPMENT_NOT_PERSONALITY_MEASUREMENT",
        "first_tick": frames[0]["tick"], "last_tick": frames[-1]["tick"],
        "frames_analyzed": len(frames),
        "source_integrity": source_integrity,
        "milestones": milestones,
        "action_experience": counts,
        "unseen_milestones": [kind for kind in KINDS if kind not in seen],
        "inference_scope": "causal_local_action_effect_only_not_thoughts",
    }


def from_sqlite(db, agent_id="dante-fixture-001"):
    """Validate a local Noosphere chain; read PRIVATE events only by explicit CLI request."""
    path = Path(db)
    if not path.is_file() or path.is_symlink():
        raise DiaryError("existing regular SQLite journal required")
    with LocalNoosphere(path, agent_id) as store:
        verdict = store.verify_chain()
        if not 1 <= verdict["count"] <= MAX_FRAMES + 1:
            raise DiaryError("journal outside infant replay budget")
        data = store.dataset(include_nonpublic=True, limit=MAX_FRAMES + 1)["projection"]["items"]
        if len(data) != verdict["count"]:
            raise DiaryError("truncated SQLite projection")
        bootstrap = data[0]["event"]
        if (
            bootstrap.get("topic") != "infant.bootstrap"
            or bootstrap.get("agent_id") != agent_id
            or bootstrap.get("visibility") != "PRIVATE"
            or bootstrap.get("payload", {}).get("schema") != INFANT_SCHEMA
            or bootstrap.get("source") != "mojum.infant.synthetic"
        ):
            raise DiaryError("journal is not an infant organism")
        if len(data) == 1:
            raise DiaryError("no committed developmental events")
        frames, hashes = [], []
        for row in data[1:]:
            event = row["event"]
            tick = len(frames)
            if (
                event.get("topic") != "infant.frame"
                or event.get("visibility") != "PRIVATE"
                or event.get("source") != "mojum.infant.synthetic"
                or event.get("event_id") != f"infant-frame-{tick:012d}"
                or event.get("agent_id") != agent_id
                or not isinstance(event.get("payload"), dict)
                or event["payload"].get("viewer", {}).get("tick") != tick
            ):
                raise DiaryError("unexpected event in private infant memory")
            frames.append(event["payload"]["viewer"])
            hashes.append(row["event_hash"])
        report = summarize(
            frames, event_hashes=hashes,
            source_integrity="local_chain_verified_not_authenticated",
        )
        report["journal_head"] = verdict["head"]
        report["agent_id"] = agent_id
        return report


def main():
    parser = argparse.ArgumentParser(description="Private, read-only developmental milestones report")
    parser.add_argument("--db", required=True, help="existing, nonempty private infant SQLite database")
    parser.add_argument("--agent-id", default="dante-fixture-001")
    args = parser.parse_args()
    print(json.dumps(from_sqlite(args.db, args.agent_id), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
