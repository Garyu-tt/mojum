"""Read-only, stdlib RoboPianist key activation benchmark.

This consumes explicitly exported virtual piano observations. It does not run
RoboPianist, infer motor commands, open private memory, or actuate hardware.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

SCHEMA = "mojum.robopianist.keys/1"
KEYS = 88
MAX_FRAMES = 4096
MAX_BYTES = 1_048_576
MAX_LOOKAHEAD = 32


class PianoBenchmarkError(ValueError):
    """Invalid or untrusted virtual keyboard trace."""


def _integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PianoBenchmarkError(f"invalid {name}")
    return value


def _unit(value, name):
    if type(value) not in (int, float):
        raise PianoBenchmarkError(f"invalid {name}")
    try:
        valid = math.isfinite(value) and 0 <= value <= 1
    except (ValueError, OverflowError, TypeError):
        valid = False
    if not valid:
        raise PianoBenchmarkError(f"invalid {name}")
    return value


def _binary(value, name):
    if _unit(value, name) not in (0, 1):
        raise PianoBenchmarkError(f"invalid {name}")
    return bool(value)


def _indices(values, name):
    if type(values) is not list or len(values) > KEYS:
        raise PianoBenchmarkError(f"invalid {name}")
    previous = -1
    for key in values:
        _integer(key, name, 0, KEYS - 1)
        if key <= previous:
            raise PianoBenchmarkError(f"unordered or duplicate {name}")
        previous = key
    return tuple(values)


def _contacts(values):
    if type(values) is not list or len(values) > 10:
        raise PianoBenchmarkError("invalid finger contacts")
    seen = set()
    result = []
    for contact in values:
        if type(contact) is not dict or set(contact) != {"hand", "finger", "key"}:
            raise PianoBenchmarkError("invalid finger contact")
        hand = contact["hand"]
        if type(hand) is not str or hand not in ("left", "right"):
            raise PianoBenchmarkError("invalid contact hand")
        finger = _integer(contact["finger"], "contact finger", 0, 4)
        key = _integer(contact["key"], "contact key", 0, KEYS - 1)
        if (hand, finger) in seen:
            raise PianoBenchmarkError("duplicate physical finger observation")
        seen.add((hand, finger))
        result.append((hand, finger, key))
    return tuple(result)


def from_robopianist_arrays(*, tick, timestamp_ns, goal, activation,
                           sustain_activation, finger_contacts=None):
    """Build one trace frame from *explicitly measured* RoboPianist arrays.

    RoboPianist's flattened 'goal' is (lookahead+1) x (88 keys + sustain).
    Only the first current-target row is scored; no future goals are exposed.
    'activation' must be measured piano.activation (NOT proposed actions);
    a key is considered pressed at activation >= 0.5. Convert numpy arrays
    to ordinary lists before calling. Contacts are optional measured records.
    """
    _integer(tick, "tick", 0, MAX_FRAMES - 1)
    _integer(timestamp_ns, "timestamp_ns")
    if (type(goal) is not list or len(goal) < KEYS + 1
            or len(goal) % (KEYS + 1) != 0
            or len(goal) > (MAX_LOOKAHEAD + 1) * (KEYS + 1)):
        raise PianoBenchmarkError("invalid flattened RoboPianist goal")
    if type(activation) is not list or len(activation) != KEYS:
        raise PianoBenchmarkError("invalid measured activation")
    # Validate all lookahead entries even though only current row is scored.
    for i, value in enumerate(goal):
        _binary(value, f"goal[{i}]")
    for i, value in enumerate(activation):
        _unit(value, f"activation[{i}]")
    _unit(sustain_activation, "sustain_activation")
    if finger_contacts is None:
        finger_contacts = []
    _contacts(finger_contacts)
    return {
        "tick": tick,
        "timestamp_ns": timestamp_ns,
        "target_keys": [i for i in range(KEYS) if goal[i] == 1],
        "pressed_keys": [i for i in range(KEYS) if activation[i] >= 0.5],
        "target_sustain": bool(goal[KEYS]),
        "pressed_sustain": sustain_activation >= 0.5,
        "finger_contacts": [dict(c) for c in finger_contacts],
    }


def score_trace(trace):
    """Validate a detached, public/synthetic trace and score actual key state.

    Score is an observational evaluation only. No learning, control, motor
    intent, teacher grading or inferred hardware contacts.
    """
    if type(trace) is not dict or set(trace) != {
        "schema", "source", "upstream_ref", "seed", "frames"
    }:
        raise PianoBenchmarkError("invalid trace envelope")
    if trace["schema"] != SCHEMA:
        raise PianoBenchmarkError("unknown trace schema")
    source = trace["source"]
    if type(source) is not str or source not in ("synthetic", "robopianist-explicit-export"):
        raise PianoBenchmarkError("invalid source classification")
    ref = trace["upstream_ref"]
    if source == "synthetic":
        if ref != "synthetic":
            raise PianoBenchmarkError("synthetic source must identify synthetic provenance")
    elif (type(ref) is not str or len(ref) != 40
          or any(ch not in "0123456789abcdef" for ch in ref)):
        raise PianoBenchmarkError("upstream git commit must be pinned")
    seed = _integer(trace["seed"], "seed", 0, 2**32 - 1)
    frames = trace["frames"]
    if type(frames) is not list or not 1 <= len(frames) <= MAX_FRAMES:
        raise PianoBenchmarkError("invalid frame count")

    tp = fp = fn = exact = silence_exact = sustain_exact = contact_hits = 0
    tick0 = timestamp0 = previous_ns = None
    for i, frame in enumerate(frames):
        if type(frame) is not dict or set(frame) != {
            "tick", "timestamp_ns", "target_keys", "pressed_keys",
            "target_sustain", "pressed_sustain", "finger_contacts"
        }:
            raise PianoBenchmarkError("invalid frame fields")
        tick = _integer(frame["tick"], "tick", 0, 2**63 - 1)
        timestamp = _integer(frame["timestamp_ns"], "timestamp_ns")
        if i == 0:
            tick0, timestamp0 = tick, timestamp
        if tick != tick0 + i or (previous_ns is not None and timestamp <= previous_ns):
            raise PianoBenchmarkError("non-contiguous tick or non-monotonic time")
        previous_ns = timestamp
        target = set(_indices(frame["target_keys"], "target keys"))
        pressed = set(_indices(frame["pressed_keys"], "pressed keys"))
        target_sustain = frame["target_sustain"]
        pressed_sustain = frame["pressed_sustain"]
        if type(target_sustain) is not bool or type(pressed_sustain) is not bool:
            raise PianoBenchmarkError("invalid sustain state")
        contacts = _contacts(frame["finger_contacts"])
        matching = target & pressed
        tp += len(matching)
        fp += len(pressed - target)
        fn += len(target - pressed)
        exact += int(target == pressed and target_sustain == pressed_sustain)
        silence_exact += int(not target and not target_sustain)
        sustain_exact += int(target_sustain == pressed_sustain)
        contact_hits += len(matching & {key for _, _, key in contacts})

    n = len(frames)
    denom = 2 * tp + fp + fn
    return {
        "schema": "mojum.robopianist.keyscore/1",
        "source": source,
        "upstream_ref": ref,
        "seed": seed,
        "frames": n,
        "first_tick": tick0,
        "first_timestamp_ns": timestamp0,
        "last_timestamp_ns": previous_ns,
        "key_tp": tp, "key_fp": fp, "key_fn": fn,
        "key_micro_f1": 2 * tp / denom if denom else 0.0,
        "exact_frame_rate": exact / n,
        "silent_control_exact_frame_rate": silence_exact / n,
        "exact_delta_vs_silent": (exact - silence_exact) / n,
        "sustain_accuracy": sustain_exact / n,
        "matching_pressed_keys_with_reported_finger": contact_hits,
        "training_or_real_physics_verified": False,
    }


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PianoBenchmarkError("duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_):
    raise PianoBenchmarkError("nonfinite JSON constant")


def read_trace(path: Path):
    """Read a small untrusted local JSON trace, not a URL or private journal."""
    if type(path) is not Path:
        raise PianoBenchmarkError("input must be a local Path")
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise PianoBenchmarkError("oversized trace")
    try:
        trace = json.loads(data, object_pairs_hook=_unique_pairs,
                           parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise PianoBenchmarkError("invalid trace JSON") from exc
    return score_trace(trace)


def demo_trace():
    """Deterministic, tiny fully synthetic fixture, not a RoboPianist run."""
    goals = ([0, 4], [2], [], [0, 2])
    pressed = ([0, 4], [2], [], [0])
    frames = []
    for i, (target, actual) in enumerate(zip(goals, pressed)):
        frames.append({
            "tick": i,
            "timestamp_ns": i * 50_000_000,
            "target_keys": list(target),
            "pressed_keys": list(actual),
            "target_sustain": False, "pressed_sustain": False,
            "finger_contacts": [],
        })
    return {"schema": SCHEMA, "source": "synthetic",
            "upstream_ref": "synthetic", "seed": 700,
            "frames": frames}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline RoboPianist key score; no simulation or network")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", action="store_true", help="score synthetic fixture")
    group.add_argument("--input", type=Path, help="score explicit local JSON trace")
    args = parser.parse_args(argv)
    result = score_trace(demo_trace()) if args.demo else read_trace(args.input)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
