"""Bounded offline analysis of PUBLIC_SYNTHETIC tick-latency JSONL traces.

Input rows: {"tick": 0, "duration_us": 120, "visibility": "PUBLIC_SYNTHETIC"}.
No runtime imports, network calls, or access to private simulation journals.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

MAX_ROWS = 100_000
MAX_LINE_BYTES = 4096
MAX_TOTAL_BYTES = 16 * 1024 * 1024
BUCKETS_US = (100, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000)


class TraceError(ValueError):
    """Input violates the bounded public synthetic trace contract."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def analyze(lines):
    counts = [0] * (len(BUCKETS_US) + 1)
    durations = []
    previous_tick = -1
    total_bytes = 0
    for line_number, line in enumerate(lines, 1):
        if line_number > MAX_ROWS:
            raise TraceError("row limit exceeded")
        if not isinstance(line, str):
            raise TraceError("expected UTF-8 text")
        try:
            line_bytes = len(line.encode("utf-8"))
        except UnicodeError as exc:
            raise TraceError(f"line {line_number}: invalid UTF-8 text") from exc
        total_bytes += line_bytes
        if line_bytes > MAX_LINE_BYTES or total_bytes > MAX_TOTAL_BYTES:
            raise TraceError("byte limit exceeded")
        try:
            row = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (ValueError, TypeError) as exc:
            raise TraceError(f"line {line_number}: invalid JSON") from exc
        if not isinstance(row, dict) or set(row) != {"tick", "duration_us", "visibility"}:
            raise TraceError(f"line {line_number}: invalid fields")
        tick, duration = row["tick"], row["duration_us"]
        if row["visibility"] != "PUBLIC_SYNTHETIC":
            raise TraceError(f"line {line_number}: non-public trace refused")
        if type(tick) is not int or tick < 0 or tick <= previous_tick:
            raise TraceError(f"line {line_number}: ticks must increase")
        if (
            type(duration) not in (int, float)
            or (type(duration) is float and not math.isfinite(duration))
            or duration < 0
            or duration > 1_000_000_000
        ):
            raise TraceError(f"line {line_number}: invalid duration")
        previous_tick = tick
        durations.append(duration)
        bucket = next((i for i, upper in enumerate(BUCKETS_US) if duration <= upper), len(BUCKETS_US))
        counts[bucket] += 1
    ordered = sorted(durations)
    def percentile(p):
        if not ordered:
            return None
        return ordered[math.ceil(p * len(ordered)) - 1]
    return {
        "schema": "mojum.tick_latency_histogram/1",
        "visibility": "PUBLIC_SYNTHETIC",
        "count": len(ordered),
        "min_us": ordered[0] if ordered else None,
        "max_us": ordered[-1] if ordered else None,
        "p50_us": percentile(0.50),
        "p95_us": percentile(0.95),
        "p99_us": percentile(0.99),
        "buckets_us": [{"lte_us": upper, "count": counts[i]} for i, upper in enumerate(BUCKETS_US)]
        + [{"gt_us": BUCKETS_US[-1], "count": counts[-1]}],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="local PUBLIC_SYNTHETIC JSONL trace")
    args = parser.parse_args(argv)
    try:
        if not args.trace.is_file() or args.trace.is_symlink():
            raise TraceError("input must be a regular non-symlink file")
        if args.trace.stat().st_size > MAX_TOTAL_BYTES:
            raise TraceError("file byte limit exceeded")
        with args.trace.open("r", encoding="utf-8") as stream:
            result = analyze(stream)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except (TraceError, OSError, UnicodeError) as exc:
        print(f"trace rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
