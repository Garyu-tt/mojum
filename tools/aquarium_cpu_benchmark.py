"""Disposable CPU-first Aquarium cold-start/restart benchmark.

This opt-in probe only launches PUBLIC_SYNTHETIC fixture journals inside a
TemporaryDirectory. It never opens an existing user journal or uses a network.
Measurements characterize one host; they are not deterministic acceptance
proof or a neural/physics performance benchmark.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

SCHEMA = "mojum.benchmark.aquarium-cpu/1"
MAX_TICKS = 1000
MAX_REPETITIONS = 12
MAX_TIMEOUT_SEC = 120
MAX_OUTPUT_BYTES = 128 * 1024


class BenchmarkError(ValueError):
    """Rejected measurement, untrusted child output or resource limit."""


def _bounded_int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise BenchmarkError(f"{name} must be an integer in {low}..{high}")


def _child_cpu_ns():
    """Completed child-process CPU time, or None on unsupported platforms."""
    try:
        import resource
    except ImportError:  # Windows has no stdlib resource module.
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return int((usage.ru_utime + usage.ru_stime) * 1_000_000_000)


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise BenchmarkError("duplicate JSON checkpoint key")
        value[key] = item
    return value


def _reject_nonfinite(value):
    raise BenchmarkError("non-finite JSON checkpoint constant")


def _checkpoint(stdout, *, expected_tick, expected_events):
    if type(stdout) is not str:
        raise BenchmarkError("Aquarium returned non-text output")
    try:
        raw = stdout.encode("utf-8")
    except UnicodeError as exc:
        raise BenchmarkError("Aquarium output is not UTF-8") from exc
    if len(raw) > MAX_OUTPUT_BYTES:
        raise BenchmarkError("Aquarium output exceeds benchmark limit")
    try:
        value = json.loads(
            stdout, object_pairs_hook=_unique_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (ValueError, RecursionError) as exc:
        raise BenchmarkError("Aquarium returned invalid JSON") from exc
    if type(value) is not dict:
        raise BenchmarkError("Aquarium checkpoint must be an object")
    if (type(value.get("tick")) is not int
            or value["tick"] != expected_tick
            or type(value.get("stored_events")) is not int
            or value["stored_events"] != expected_events):
        raise BenchmarkError("Aquarium tick/event continuity check failed")
    head = value.get("journal_head")
    if (type(head) is not str or len(head) != 64
            or any(ch not in "0123456789abcdef" for ch in head)):
        raise BenchmarkError("Aquarium returned an invalid journal head")
    return head


def _invoke(command, *, timeout_sec, runner, clock, cpu_clock):
    start_wall = clock()
    start_cpu = cpu_clock()
    try:
        result = runner(
            command, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=timeout_sec, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError("Aquarium invocation timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise BenchmarkError("Aquarium subprocess could not execute") from exc
    end_cpu = cpu_clock()
    end_wall = clock()
    if type(result.returncode) is not int or result.returncode != 0:
        # Do not echo arbitrary child stderr, which may contain local paths.
        raise BenchmarkError("Aquarium subprocess exited unsuccessfully")
    if (type(start_wall) is not int or type(end_wall) is not int
            or end_wall < start_wall):
        raise BenchmarkError("invalid monotonic wall-clock measurement")
    cpu_ns = None
    if start_cpu is not None and end_cpu is not None:
        if (type(start_cpu) is not int or type(end_cpu) is not int
                or end_cpu < start_cpu):
            raise BenchmarkError("invalid child CPU measurement")
        cpu_ns = end_cpu - start_cpu
    return {"wall_ns": end_wall - start_wall, "child_cpu_ns": cpu_ns}, result.stdout


def benchmark(ticks=64, repetitions=3, timeout_sec=60, *,
              runner=subprocess.run, clock=time.perf_counter_ns,
              cpu_clock=_child_cpu_ns):
    """Measure separate fresh and cold-reopen processes on disposable journals.

    A head pinned by the fresh process is passed to the restart command.
    Both event count and logical tick are checked against the canonical
    bootstrap + N frame contract; no report contains journal contents,
    temporary filenames, organism identifiers or hash heads.
    """
    _bounded_int(ticks, "ticks", 1, MAX_TICKS)
    _bounded_int(repetitions, "repetitions", 1, MAX_REPETITIONS)
    _bounded_int(timeout_sec, "timeout_sec", 1, MAX_TIMEOUT_SEC)
    rows = []
    for index in range(repetitions):
        with tempfile.TemporaryDirectory(prefix="mojum-bench-") as temporary:
            db = Path(temporary) / "synthetic_PRIVATE.sqlite3"
            command = [
                sys.executable, "-m", "mojum.aquarium",
                "--db", str(db), "--ticks", str(ticks),
                "--neural-mode", "disabled",
                "--motor-mode", "developmental_fixture",
                "--sensory-mode", "toy_probe",
            ]
            cold, cold_output = _invoke(
                command, timeout_sec=timeout_sec, runner=runner,
                clock=clock, cpu_clock=cpu_clock,
            )
            head = _checkpoint(
                cold_output, expected_tick=ticks, expected_events=ticks + 1,
            )
            warm, warm_output = _invoke(
                command + ["--expected-head", head],
                timeout_sec=timeout_sec, runner=runner,
                clock=clock, cpu_clock=cpu_clock,
            )
            new_head = _checkpoint(
                warm_output, expected_tick=2 * ticks,
                expected_events=2 * ticks + 1,
            )
            if new_head == head:
                raise BenchmarkError("restart did not advance journal head")
            rows.append({"iteration": index + 1, "cold": cold, "restart": warm})

    def summary(phase, name):
        values = [row[phase][name] for row in rows]
        if any(value is None for value in values):
            return None
        return int(statistics.median(values))

    return {
        "schema": SCHEMA,
        "fixture": "PUBLIC_SYNTHETIC_DISPOSABLE",
        "neural_mode": "disabled",
        "motor_mode": "developmental_fixture",
        "sensory_mode": "toy_probe",
        "ticks_per_phase": ticks,
        "repetitions": repetitions,
        "measurements": rows,
        "median_cold_wall_ns": summary("cold", "wall_ns"),
        "median_restart_wall_ns": summary("restart", "wall_ns"),
        "median_cold_child_cpu_ns": summary("cold", "child_cpu_ns"),
        "median_restart_child_cpu_ns": summary("restart", "child_cpu_ns"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline disposable Aquarium cold/restart CPU benchmark",
    )
    parser.add_argument("--ticks", type=int, default=64)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout-sec", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        report = benchmark(args.ticks, args.repetitions, args.timeout_sec)
    except BenchmarkError as exc:
        parser.exit(2, f"benchmark rejected: {exc}\n")
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
