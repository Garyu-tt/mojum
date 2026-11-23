"""Offline delta debugging for PUBLIC_SYNTHETIC deterministic point-room traces.

This is a diagnostic utility, not a verifier, authority, or world solver. Every
candidate is replayed from scratch against the canonical ``world_room.advance``
contract with freshly chained tick and revision values.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .world_room import Room, advance, initial_state

SCHEMA = "mojum.world_trace_minimize/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_JSON_BYTES = 65536
MAX_OBSTACLES = 128
MAX_MOVES = 128
MAX_PROBES = 256
MAX_STEPS = 16384
MAX_COORDINATE_M = 1000


class TraceMinimizeError(ValueError):
    """Invalid case, unachievable witness, or unsafe input."""


def _keys(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if (type(value) is not dict
            or any(type(key) is not str for key in value)
            or not required.issubset(value)
            or set(value) - required - optional):
        raise TraceMinimizeError("invalid object fields")
    return value


def _number(value: object, *, nonnegative: bool = False) -> int | float:
    if type(value) not in (int, float):
        raise TraceMinimizeError("expected finite number (bool forbidden)")
    try:
        in_budget = math.isfinite(value) and abs(value) <= MAX_COORDINATE_M
    except (TypeError, OverflowError, ValueError) as exc:
        raise TraceMinimizeError("invalid numeric magnitude") from exc
    if not in_budget:
        raise TraceMinimizeError("number exceeds finite room budget")
    if nonnegative and value < 0:
        raise TraceMinimizeError("number must be nonnegative")
    return value


def _pair(value: object) -> tuple[int | float, int | float]:
    if type(value) is not list or len(value) != 2:
        raise TraceMinimizeError("expected two-element coordinate list")
    return _number(value[0]), _number(value[1])


def _canonical(value: Any) -> bytes:
    # No NaN/Infinity, no dependence on map insertion order or whitespace.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def loads_case(raw: str | bytes) -> dict:
    """Strict JSON decoder rejecting duplicate object keys and nonfinite constants."""
    if type(raw) not in (str, bytes):
        raise TraceMinimizeError("JSON must be string or bytes")
    try:
        encoded = raw.encode("utf-8") if type(raw) is str else raw
        if len(encoded) > MAX_JSON_BYTES:
            raise TraceMinimizeError("JSON byte budget exceeded")

        def object_pairs(pairs: list[tuple[str, Any]]) -> dict:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise TraceMinimizeError("duplicate JSON key")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise TraceMinimizeError("nonfinite JSON constant: " + value)

        value = json.loads(encoded, object_pairs_hook=object_pairs,
                           parse_constant=reject_constant)
    except TraceMinimizeError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise TraceMinimizeError("malformed JSON case") from exc
    if type(value) is not dict:
        raise TraceMinimizeError("case must be an object")
    return value


def _validate(case: object) -> tuple[Room, tuple, list[dict], dict, int]:
    d = _keys(case, {"schema", "data_classification", "room", "start", "moves", "witness"},
              {"max_probes"})
    if (type(d["schema"]) is not str or d["schema"] != SCHEMA
            or type(d["data_classification"]) is not str
            or d["data_classification"] != CLASSIFICATION):
        raise TraceMinimizeError("unsupported schema or non-public synthetic data")
    geometry = _keys(d["room"], {"width", "height", "obstacles"})
    width = _number(geometry["width"])
    height = _number(geometry["height"])
    obstacles = geometry["obstacles"]
    if type(obstacles) is not list or len(obstacles) > MAX_OBSTACLES:
        raise TraceMinimizeError("obstacle budget exceeded")
    frozen_obstacles = []
    for rect in obstacles:
        if type(rect) is not list or len(rect) != 4:
            raise TraceMinimizeError("invalid obstacle rectangle")
        frozen_obstacles.append(tuple(_number(x) for x in rect))
    try:
        room = Room(width, height, tuple(frozen_obstacles))
        start = _pair(d["start"])
        initial_state(room, *start)
    except (TypeError, OverflowError, ValueError) as exc:
        raise TraceMinimizeError("invalid room or initial position") from exc

    moves = d["moves"]
    if type(moves) is not list or len(moves) > MAX_MOVES:
        raise TraceMinimizeError("move budget exceeded")
    normalized = []
    for move in moves:
        _keys(move, {"dx", "dy"})
        dx, dy = _number(move["dx"]), _number(move["dy"])
        if math.hypot(dx, dy) > 1000:
            raise TraceMinimizeError("move exceeds world displacement budget")
        normalized.append({"dx": dx, "dy": dy})

    witness = _keys(d["witness"], {"kind"}, {"position"})
    if type(witness["kind"]) is not str or witness["kind"] not in (
            "blocked", "blocked_at_position", "end_position"):
        raise TraceMinimizeError("unsupported witness kind")
    if witness["kind"] == "blocked" and "position" in witness:
        raise TraceMinimizeError("blocked witness must not declare position")
    if witness["kind"] != "blocked" and "position" not in witness:
        raise TraceMinimizeError("missing witness position")
    witness_copy = {"kind": witness["kind"]}
    if "position" in witness:
        witness_copy["position"] = list(_pair(witness["position"]))

    probes = d.get("max_probes", 192)
    if type(probes) is not int or not 1 <= probes <= MAX_PROBES:
        raise TraceMinimizeError("max_probes must be an integer in 1..256")
    return room, start, normalized, witness_copy, probes


class _Oracle:
    def __init__(self, room: Room, start: tuple, witness: dict, max_probes: int):
        self.room = room
        self.start = start
        self.witness = witness
        self.max_probes = max_probes
        self.probes = 0
        self.steps = 0
        self.exhausted = False

    def evaluate(self, moves: list[tuple[int, dict]]) -> tuple[bool, tuple, int] | None:
        """Returns witness, final position and blocked events; None means budget exhausted."""
        if self.probes >= self.max_probes or self.steps + len(moves) > MAX_STEPS:
            self.exhausted = True
            return None
        self.probes += 1
        self.steps += len(moves)
        state = initial_state(self.room, *self.start)
        found_blocked = 0
        hit_at_position = False
        target = self.witness.get("position")
        for tick, (_, move) in enumerate(moves, start=1):
            state, observation = advance(
                self.room, state, tick=tick, dx=move["dx"], dy=move["dy"],
                expected_revision=state.revision)
            if observation["blocked"]:
                found_blocked += 1
                if target is not None and [state.x, state.y] == target:
                    hit_at_position = True
        kind = self.witness["kind"]
        success = (found_blocked > 0 if kind == "blocked" else
                   hit_at_position if kind == "blocked_at_position" else
                   [state.x, state.y] == target)
        return success, (state.x, state.y), found_blocked


def minimize_case(case: dict) -> dict:
    """Reduce moves preserving a declared witness; return a deterministic diagnostic.

    The ``complete`` flag is False if a probe/step budget is exhausted. A True
    result means no single remaining move can be removed while keeping the
    witness (1-minimal), not a globally shortest trace or a safety proof.
    """
    room, start, moves, witness, max_probes = _validate(case)
    original = list(enumerate(moves))
    oracle = _Oracle(room, start, witness, max_probes)
    initial = oracle.evaluate(original)
    if initial is None:
        raise TraceMinimizeError("cannot replay original trace within budget")
    if not initial[0]:
        raise TraceMinimizeError("original trace does not satisfy witness")
    best = original
    best_result = initial
    granularity = 2

    while best:
        chunk = math.ceil(len(best) / granularity)
        changed = False
        for offset in range(0, len(best), chunk):
            candidate = best[:offset] + best[offset + chunk:]
            result = oracle.evaluate(candidate)
            if result is None:
                break
            if result[0]:
                best = candidate
                best_result = result
                granularity = max(2, granularity - 1)
                changed = True
                break
        if oracle.exhausted:
            break
        if changed:
            continue
        if granularity >= len(best):
            break
        granularity = min(len(best), granularity * 2)

    # Additional stable left-to-right single-deletion cleanup: important if
    # nonmonotone predicates let a successful chunk reduction expose new drops.
    if not oracle.exhausted:
        index = 0
        while index < len(best):
            candidate = best[:index] + best[index + 1:]
            result = oracle.evaluate(candidate)
            if result is None:
                break
            if result[0]:
                best = candidate
                best_result = result
                # Deleting a later step can make an earlier one redundant.
                index = 0
            else:
                index += 1

    # Every accepted reduction cached its replay witness, so summarizing does
    # not consume extra unaccounted probes/ticks or defeat the budget.
    final_position = list(best_result[1])
    blocked_count = best_result[2]

    minimized_moves = [dict(move) for _, move in best]
    replay_envelope = {
        "schema": SCHEMA,
        "data_classification": CLASSIFICATION,
        "room": {"width": room.width, "height": room.height,
                 "obstacles": [list(r) for r in room.obstacles]},
        "start": list(start), "moves": minimized_moves, "witness": witness,
    }
    source_envelope = dict(replay_envelope, moves=[dict(move) for move in moves])
    return {
        "schema": "mojum.world_trace_minimize.result/1",
        "data_classification": CLASSIFICATION,
        "source_sha256": _sha(source_envelope),
        "replay_sha256": _sha(replay_envelope),
        "replay_case": replay_envelope,
        "retained_indices": [index for index, _ in best],
        "source_moves": len(moves),
        "minimized_moves": len(best),
        "final_position": final_position,
        "blocked_events": blocked_count,
        "witness_preserved": True,
        "complete": not oracle.exhausted,
        "probes": oracle.probes,
        "evaluated_steps": oracle.steps,
        "meaning": "1-minimal only when complete; offline diagnostic, not attestation",
    }
