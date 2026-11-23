"""Deterministic offline piano-key benchmark fixture; not a physics or learning engine.

All inputs are caller-owned values. This module performs no I/O, networking,
audio playback, hardware access, or writes to Mojum's canonical state.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

TASK_SCHEMA = "mojum.robopianist-task/1"
TRACE_SCHEMA = "mojum.robopianist-trace/1"
RESULT_SCHEMA = "mojum.robopianist-score/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_NOTES = 64
MAX_TICK = 2**31 - 1
MAX_ABS_ERROR_TICKS = 8
KEYS = tuple(range(48, 60))  # one synthetic C3-B3 octave
FINGERS = (1, 2, 3, 4, 5)
MAX_EVENT_COUNT = MAX_NOTES
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


class BenchmarkError(ValueError):
    """Invalid, unconsented, unbounded, or incompatible benchmark data."""


def _plain_int(value: Any, low: int, high: int, field: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise BenchmarkError("invalid " + field)
    return value


def _canonical(value: Any) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        raw = text.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise BenchmarkError("value is not canonical JSON data") from exc
    if len(raw) > 32_768:
        raise BenchmarkError("serialized benchmark budget exceeded")
    return raw


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finger_for_key(key: int) -> int:
    # A simple fixed synthetic mapping, not a biomechanical hand model.
    return 1 + ((key - KEYS[0]) % 5)


def make_task(seed: int, *, held_out: bool = False, note_count: int = 12) -> dict:
    """Create a deterministic public synthetic exercise from a bounded seed."""
    _plain_int(seed, 0, 2**32 - 1, "seed")
    if type(held_out) is not bool:
        raise BenchmarkError("held_out must be bool")
    _plain_int(note_count, 4, MAX_NOTES, "note_count")
    # SHA-256-derived choices avoid platform PRNG/version dependencies.
    notes = []
    for index in range(note_count):
        material = f"mojum-rp1:{seed}:{int(held_out)}:{index}".encode("ascii")
        value = int.from_bytes(hashlib.sha256(material).digest()[:4], "big")
        key = KEYS[value % len(KEYS)]
        tick = index * 4 + (value >> 8) % 2
        notes.append({"index": index, "key": key, "finger": _finger_for_key(key),
                      "tick": tick, "duration_ticks": 2})
    task = {"schema": TASK_SCHEMA, "provenance": PROVENANCE,
            "consent": True, "seed": seed, "split": "held_out" if held_out else "train",
            "tempo_ticks_per_second": 4, "notes": notes}
    task["sha256"] = _digest(task)
    return task


def validate_task(task: Any) -> dict:
    """Validate and detach a task. Its digest is integrity evidence, not identity."""
    if type(task) is not dict or set(task) != {
        "schema", "provenance", "consent", "seed", "split",
        "tempo_ticks_per_second", "notes", "sha256"
    }:
        raise BenchmarkError("invalid task envelope")
    if task["schema"] != TASK_SCHEMA or task["provenance"] != PROVENANCE or task["consent"] is not True:
        raise BenchmarkError("task schema, provenance, or consent rejected")
    _plain_int(task["seed"], 0, 2**32 - 1, "seed")
    if task["split"] not in ("train", "held_out"):
        raise BenchmarkError("invalid split")
    if type(task["tempo_ticks_per_second"]) is not int or task["tempo_ticks_per_second"] != 4:
        raise BenchmarkError("unsupported tempo")
    notes = task["notes"]
    if type(notes) is not list or not 4 <= len(notes) <= MAX_NOTES:
        raise BenchmarkError("invalid note budget")
    detached = []
    previous_tick = -1
    for index, note in enumerate(notes):
        if type(note) is not dict or set(note) != {"index", "key", "finger", "tick", "duration_ticks"}:
            raise BenchmarkError("invalid note fields")
        if _plain_int(note["index"], 0, MAX_NOTES - 1, "note index") != index:
            raise BenchmarkError("note indices must be contiguous")
        key = _plain_int(note["key"], KEYS[0], KEYS[-1], "key")
        finger = _plain_int(note["finger"], 1, 5, "finger")
        tick = _plain_int(note["tick"], 0, MAX_TICK, "tick")
        duration = _plain_int(note["duration_ticks"], 1, 8, "duration")
        if finger != _finger_for_key(key) or tick <= previous_tick:
            raise BenchmarkError("inconsistent finger or note chronology")
        previous_tick = tick
        detached.append({"index": index, "key": key, "finger": finger,
                         "tick": tick, "duration_ticks": duration})
    unsigned = {k: task[k] for k in ("schema", "provenance", "consent", "seed",
                                      "split", "tempo_ticks_per_second", "notes")}
    if type(task["sha256"]) is not str or task["sha256"] != _digest(unsigned):
        raise BenchmarkError("task digest mismatch")
    return {"schema": TASK_SCHEMA, "provenance": PROVENANCE, "consent": True,
            "seed": task["seed"], "split": task["split"],
            "tempo_ticks_per_second": 4, "notes": tuple(
                (n["index"], n["key"], n["finger"], n["tick"], n["duration_ticks"])
                for n in detached), "sha256": task["sha256"]}


def make_trace(task: Any, *, mode: str = "policy") -> dict:
    """Create a deterministic policy or frozen-control trace for a task."""
    checked = validate_task(task)
    if mode not in ("policy", "frozen"):
        raise BenchmarkError("unknown trace mode")
    events = []
    for index, key, finger, tick, duration in checked["notes"]:
        if mode == "policy":
            actual_key, actual_finger, actual_tick = key, finger, tick
        else:
            actual_key = KEYS[0]
            actual_finger = _finger_for_key(actual_key)
            actual_tick = tick + 1
        events.append({"index": index, "key": actual_key, "finger": actual_finger,
                       "tick": actual_tick, "duration_ticks": duration})
    trace = {"schema": TRACE_SCHEMA, "provenance": PROVENANCE, "consent": True,
             "task_sha256": checked["sha256"], "mode": mode, "events": events}
    trace["sha256"] = _digest(trace)
    return trace


def score_trace(task: Any, trace: Any, *, consent: bool = False) -> dict:
    """Compare a trace to its task; report fixture metrics, never learning claims."""
    checked = validate_task(task)
    if consent is not True:
        raise BenchmarkError("explicit caller consent required")
    if type(trace) is not dict or set(trace) != {
        "schema", "provenance", "consent", "task_sha256", "mode", "events", "sha256"
    }:
        raise BenchmarkError("invalid trace envelope")
    if (trace["schema"] != TRACE_SCHEMA or trace["provenance"] != PROVENANCE
            or trace["consent"] is not True or trace["task_sha256"] != checked["sha256"]
            or trace["mode"] not in ("policy", "frozen")):
        raise BenchmarkError("trace/task provenance or identity mismatch")
    events = trace["events"]
    if type(events) is not list or len(events) != len(checked["notes"]) or len(events) > MAX_EVENT_COUNT:
        raise BenchmarkError("invalid event budget")
    unsigned = {k: trace[k] for k in ("schema", "provenance", "consent",
                                       "task_sha256", "mode", "events")}
    if type(trace["sha256"]) is not str or trace["sha256"] != _digest(unsigned):
        raise BenchmarkError("trace digest mismatch")
    hits = 0
    timing_error = 0
    for index, event in enumerate(events):
        if type(event) is not dict or set(event) != {
            "index", "key", "finger", "tick", "duration_ticks"
        }:
            raise BenchmarkError("invalid event fields")
        ei = _plain_int(event["index"], 0, MAX_NOTES - 1, "event index")
        key = _plain_int(event["key"], KEYS[0], KEYS[-1], "event key")
        finger = _plain_int(event["finger"], 1, 5, "event finger")
        tick = _plain_int(event["tick"], 0, MAX_TICK, "event tick")
        duration = _plain_int(event["duration_ticks"], 1, 8, "event duration")
        if ei != index or finger != _finger_for_key(key):
            raise BenchmarkError("invalid event order or finger mapping")
        _, target_key, target_finger, target_tick, target_duration = checked["notes"][index]
        if key == target_key and finger == target_finger:
            hits += 1
        timing_error += min(abs(tick - target_tick), MAX_ABS_ERROR_TICKS)
        if duration != target_duration:
            # Duration is part of the task contract, but does not change pitch accuracy.
            pass
    count = len(events)
    result = {"schema": RESULT_SCHEMA, "provenance": PROVENANCE,
              "task_sha256": checked["sha256"], "trace_sha256": trace["sha256"],
              "mode": trace["mode"], "note_count": count, "correct_notes": hits,
              "pitch_accuracy": hits / count,
              "mean_abs_timing_error_ticks": timing_error / count,
              "transfer_measured": False, "learning_claimed": False}
    result["sha256"] = _digest(result)
    return result


def compare_controls(task: Any) -> dict:
    """Score policy fixture against frozen control, without asserting improvement."""
    policy = score_trace(task, make_trace(task, mode="policy"), consent=True)
    frozen = score_trace(task, make_trace(task, mode="frozen"), consent=True)
    return {"schema": "mojum.robopianist-comparison/1",
            "task_sha256": validate_task(task)["sha256"],
            "policy": policy, "frozen_control": frozen,
            "policy_outscored_control": policy["pitch_accuracy"] > frozen["pitch_accuracy"],
            "learning_claimed": False, "transfer_measured": False}
