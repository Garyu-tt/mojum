#!/usr/bin/env python3
"""Conservatively audit completed, successful model API concurrency from JSONL.

This checks the structure of trusted collector telemetry; a file supplied by
workers is not independent evidence that provider operations happened.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import stat
import sys

MAX_EVENTS = 100_000
MAX_LINE_BYTES = 4096
MAX_COST = Decimal("1000000")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
START_KEYS = frozenset({"at_ms", "kind", "request_id", "worker", "model"})
FINISH_KEYS = START_KEYS | {"outcome", "input_tokens", "output_tokens", "cost_usd"}


class AuditError(ValueError):
    """Invalid, untrusted or inconsistent collector telemetry."""


def _unique_keys(pairs):
    obj = {}
    for key, val in pairs:
        if key in obj:
            raise AuditError("duplicate JSON key")
        obj[key] = val
    return obj


def _reject_constant(value):
    raise AuditError("nonfinite JSON number")


def _identifier(value, pattern, label):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AuditError("invalid " + label)


def _uint(value, label):
    if type(value) is not int or not 0 <= value <= 10**15:
        raise AuditError("invalid " + label)


def _tokens(value, label):
    if type(value) is not int or not 0 <= value <= 10**9:
        raise AuditError("invalid " + label)


def _cost(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,9})?", value):
        raise AuditError("cost_usd must be a bounded nonnegative decimal string")
    try:
        cost = Decimal(value)
    except InvalidOperation as exc:
        raise AuditError("invalid cost_usd") from exc
    if cost > MAX_COST:
        raise AuditError("cost_usd exceeds per-event bound")
    return cost


def _parse(line, line_number):
    try:
        event = json.loads(line, object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise AuditError(f"line {line_number}: invalid JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise AuditError(f"line {line_number}: event must be an object")
    kind = event.get("kind")
    if kind not in ("start", "finish"):
        raise AuditError(f"line {line_number}: kind must be start or finish")
    expected = START_KEYS if kind == "start" else FINISH_KEYS
    if kind == "finish" and event.get("outcome") == "ok":
        expected = expected | {"response_id"}
    if event.keys() != expected:
        raise AuditError(f"line {line_number}: missing or unexpected event fields")
    _uint(event["at_ms"], "at_ms")
    _identifier(event["request_id"], ID, "request_id")
    _identifier(event["worker"], ID, "worker")
    _identifier(event["model"], MODEL, "model")
    if kind == "finish":
        if event["outcome"] not in ("ok", "error"):
            raise AuditError(f"line {line_number}: invalid outcome")
        _tokens(event["input_tokens"], "input_tokens")
        _tokens(event["output_tokens"], "output_tokens")
        event["cost_usd"] = _cost(event["cost_usd"])
        if event["outcome"] == "ok":
            _identifier(event["response_id"], ID, "response_id")
    return event


def _peak(intervals):
    # Half-open intervals: a request ending at t does NOT overlap one
    # starting at t. Count distinct workers, not only concurrent requests.
    events = []
    for start, end, worker in intervals:
        events.append((start, 1, worker))
        events.append((end, 0, worker))
    events.sort()
    live = Counter()
    active = 0
    peak_requests = peak_workers = 0
    # Track the tie-breaker at the worker peak independently from the
    # lifetime maximum number of simultaneous requests. The two peaks
    # need not occur at the same time.
    requests_at_worker_peak = 0
    peak_at_ms = None
    for at_ms, kind, worker in events:
        if kind == 0:
            live[worker] -= 1
            if live[worker] == 0:
                del live[worker]
            active -= 1
        else:
            live[worker] += 1
            active += 1
            peak_requests = max(peak_requests, active)
            if (len(live), active) > (peak_workers, requests_at_worker_peak):
                peak_workers = len(live)
                requests_at_worker_peak = active
                peak_at_ms = at_ms
    return {"peak_requests": peak_requests, "peak_distinct_workers": peak_workers,
            "peak_at_ms": peak_at_ms}


@contextmanager
def _bounded_source(path: str | Path):
    """Open a regular collector log without following final symlinks or FIFO stalls."""
    descriptor = -1
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AuditError("collector source must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            yield stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def audit(path: str | Path, target: int = 300, max_events: int = MAX_EVENTS):
    if type(target) is not int or not 1 <= target <= 300:
        raise ValueError("target must be 1..300")
    if type(max_events) is not int or not 2 <= max_events <= 1_000_000:
        raise ValueError("max_events must be 2..1000000")
    pending = {}
    used_request_ids = set()
    used_response_ids = set()
    completed = []
    successful = []
    total_cost_nanos = cost_places = 0
    input_tokens = output_tokens = finished = ok = errors = count = 0
    last_ms = -1
    with _bounded_source(path) as stream:
        # A normal "for line in stream" reads the *entire* attacker-sized
        # JSONL line before any 4096-byte check can fire.
        for line in iter(lambda: stream.readline(MAX_LINE_BYTES + 1), b""):
            count += 1
            if count > max_events:
                raise AuditError("event count exceeds configured bound")
            if len(line) > MAX_LINE_BYTES:
                raise AuditError(f"line {count}: event exceeds 4096 bytes")
            try:
                raw = line.decode("utf-8", "strict")
            except UnicodeDecodeError as exc:
                raise AuditError(f"line {count}: invalid UTF-8") from exc
            event = _parse(raw, count)
            at_ms = event["at_ms"]
            if at_ms < last_ms:
                raise AuditError(f"line {count}: nonmonotonic collector clock")
            last_ms = at_ms
            rid = event["request_id"]
            if event["kind"] == "start":
                if rid in used_request_ids:
                    raise AuditError(f"line {count}: reused request_id")
                used_request_ids.add(rid)
                pending[rid] = (at_ms, event["worker"], event["model"])
            else:
                if rid not in pending:
                    raise AuditError(f"line {count}: finish without matching start")
                start_ms, worker, model = pending.pop(rid)
                if (worker, model) != (event["worker"], event["model"]):
                    raise AuditError(f"line {count}: request identity mismatch")
                if at_ms <= start_ms:
                    raise AuditError(f"line {count}: model operation has no positive duration")
                interval = (start_ms, at_ms, worker)
                completed.append(interval)
                finished += 1
                input_tokens += event["input_tokens"]
                output_tokens += event["output_tokens"]
                # Decimal construction is exact, but Decimal addition obeys
                # the caller's precision, exponent limits and traps. Costs
                # admit at most nine fractional places; sum integer units.
                cost_tuple = event["cost_usd"].as_tuple()
                coefficient = 0
                for digit in cost_tuple.digits:
                    coefficient = coefficient * 10 + digit
                places = -cost_tuple.exponent
                total_cost_nanos += coefficient * 10 ** (9 - places)
                cost_places = max(cost_places, places)
                if event["outcome"] == "ok":
                    if event["response_id"] in used_response_ids:
                        raise AuditError(f"line {count}: reused provider response_id")
                    used_response_ids.add(event["response_id"])
                    successful.append(interval)
                    ok += 1
                else:
                    errors += 1
    peak = _peak(successful)
    whole_cost, fractional_cost = divmod(total_cost_nanos, 10**9)
    total_cost_text = str(whole_cost)
    if cost_places:
        total_cost_text += "." + f"{fractional_cost:09d}"[:cost_places]
    return {
        "target_distinct_workers": target,
        "target_met_in_log": peak["peak_distinct_workers"] >= target,
        "successful_peak": peak,
        "completed_peak_including_errors": _peak(completed),
        "events": count,
        "completed": finished,
        "successful": ok,
        "failed": errors,
        "unfinished": len(pending),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": total_cost_text,
        "evidence": "untrusted unless produced by an independently controlled provider-side collector",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSONL from a trusted provider-boundary collector")
    parser.add_argument("--target", type=int, default=300, help="distinct concurrently active workers")
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS)
    parser.add_argument("--require-target", action="store_true", help="exit 1 until target is demonstrated")
    args = parser.parse_args(argv)
    try:
        result = audit(args.input, args.target, args.max_events)
    except OSError as exc:
        # OSError.strerror may include an untrusted path (or secret-bearing
        # filename); never echo it to stderr or CI logs.
        print("spartan audit: source unavailable (" + type(exc).__name__ + ")",
              file=sys.stderr)
        return 2
    except ValueError as exc:
        # AuditError messages contain positions/reasons, not raw JSON bodies.
        print("spartan audit: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if args.require_target and not result["target_met_in_log"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
