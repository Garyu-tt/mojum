"""Run the opt-in synthetic spinal fixture without files, devices or a backend.

From the checkout: PYTHONPATH=src python examples/spinal_core_demo.py
The JSON separates deterministic fixture results from host timing diagnostics.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import time

from mojum.motor_gate import MotorGate
from mojum.spinal_graph import build_spinal_graph
from mojum.spinal_runtime import SensoryFrame, SpinalIntent, SpinalRuntime


MIN_TICKS = 1200
MAX_TICKS = 120000
BASE_HZ = 1200
CAPACITY = 8
REVISION = 4
CHECKPOINT_TICK = 137
INTENT_EXPIRY = 181


def _runtime(graph):
    gate = MotorGate({"left_hip": (-1.0, 1.0), "right_hip": (-1.0, 1.0)},
                     revision=REVISION)
    runtime = SpinalRuntime(gate, {"left_hip": "L2", "right_hip": "L3"},
                            graph=graph, queue_capacity=CAPACITY)
    return gate, runtime


def _tick_count(value):
    try:
        ticks = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("ticks must be an integer") from exc
    if not MIN_TICKS <= ticks <= MAX_TICKS:
        raise argparse.ArgumentTypeError(f"ticks must be between {MIN_TICKS} and {MAX_TICKS}")
    return ticks


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _observe_queues(runtime, peaks):
    for name, count in runtime.pending_counts.items():
        peaks[name] = max(peaks[name], count)


def run_demo(ticks=MIN_TICKS):
    """Exercise routing, stalled cognition, expiry, continuation and e-stop."""
    if type(ticks) is not int or not MIN_TICKS <= ticks <= MAX_TICKS:
        raise ValueError(f"ticks must be an integer between {MIN_TICKS} and {MAX_TICKS}")
    graph = build_spinal_graph(REVISION)
    gate, runtime = _runtime(graph)
    runtime.submit_sensory(SensoryFrame("C1", "contact", 0.25, REVISION, 0, "C2"))
    runtime.submit_intent(SpinalIntent("L2", "left_hip", 0.5, REVISION, 0, INTENT_EXPIRY))
    phases = Counter()
    peaks = {name: 0 for name in runtime.pending_counts}
    relay_count = 0
    target_receipts = 0
    expired_intents = 0
    dropped_ascending = 0
    expiry_rest_observed = False
    mirror = None
    mirrored_steps = 0
    cpu_started = time.process_time()
    wall_started = time.perf_counter()

    for _ in range(ticks):
        # The absent cognitive consumer never drains the ascending mailbox.
        # Fresh scalar fixtures keep arriving from the caller every 5 ticks.
        if runtime.tick % 5 == 0:
            frame = SensoryFrame("C1", "contact", 0.25, REVISION, runtime.tick)
            runtime.submit_sensory(frame)
            if mirror is not None:
                mirror.submit_sensory(frame)
        _observe_queues(runtime, peaks)
        result = runtime.step()
        _observe_queues(runtime, peaks)
        phases.update(result.due)
        relay_count += len(result.relayed)
        for frame in result.relayed:
            _require((frame.segment, frame.destination) == ("C1", "C2"),
                     "local sensory delivery escaped its declared route")
        for receipt in result.motors:
            if receipt.target is not None:
                target_receipts += 1
                _require(receipt.joint == "left_hip" and receipt.target == 0.5,
                         "unexpected motor target")
                _require(receipt.tick < receipt.expires_tick == INTENT_EXPIRY,
                         "expired target was emitted")
        expired_intents += result.expired_intents
        dropped_ascending += result.dropped_ascending
        if result.tick == INTENT_EXPIRY:
            expiry_rest_observed = bool(result.motors) and all(
                item.target is None and item.mode == "rest" for item in result.motors)
        if mirror is not None:
            _require(mirror.step() == result, "restored continuation diverged")
            mirrored_steps += 1
        if runtime.tick == CHECKPOINT_TICK:
            _, mirror = _runtime(graph)
            mirror.restore(runtime.snapshot())

    checkpoint = runtime.snapshot()
    _require(mirror is not None and mirror.snapshot() == checkpoint,
             "final restored state diverged")
    _require(relay_count == 1 and target_receipts > 0 and expired_intents == 1,
             "routing or intention lifecycle was not exercised")
    _require(expiry_rest_observed, "expiry did not emit rest at its exact tick")
    _require(peaks["ascending"] == CAPACITY and dropped_ascending > 0,
             "stalled-cognition overflow was not exercised")
    retained = runtime.read_ascending()
    _require(len(retained) == CAPACITY and all(frame.destination == "brain" for frame in retained),
             "ascending mailbox contains unexpected data")

    # Trip the same live gate after accepting another intention. The very next
    # tick must inhibit output, even though it is not a motor schedule boundary.
    runtime.submit_intent(SpinalIntent("L2", "left_hip", 0.25, REVISION,
                                      runtime.tick, runtime.tick + 120))
    gate.trip_estop()
    stopped = runtime.step()
    _require(stopped.inhibit_reason == "estop" and bool(stopped.motors)
             and all(item.target is None for item in stopped.motors),
             "e-stop failed to inhibit the next step")

    # Restoring an older unlatched checkpoint into a fresh, already stopped
    # process-local gate must not clear that live gate's latch.
    stopped_gate, restored_stopped = _runtime(graph)
    stopped_gate.trip_estop()
    restored_stopped.restore(checkpoint)
    _require(stopped_gate.estop_latched, "restore cleared the live e-stop")
    _require(restored_stopped.step().inhibit_reason == "estop",
             "restored runtime bypassed the live e-stop")

    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    step_calls = ticks + mirrored_steps + 2
    return {
        "fixture": {
            "schema": "mojum.spinal-demo/1",
            "scope": "synthetic-opt-in-cpu-fixture",
            "graph_schema": graph["schema"],
            "segments": len(graph["segments"]),
            "directed_adjacent_routes": len(graph["edges"]),
            "base_hz": BASE_HZ,
            "logical_ticks": ticks,
            "logical_seconds": ticks / BASE_HZ,
            "phase_counts": dict(sorted(phases.items())),
            "local_relay_count": relay_count,
            "target_receipts": target_receipts,
            "intent_expiry_tick": INTENT_EXPIRY,
            "rest_at_exact_expiry": expiry_rest_observed,
            "expired_intents": expired_intents,
            "cognitive_consumer_paused": True,
            "queue_capacity": CAPACITY,
            "queue_peaks": peaks,
            "ascending_overflow_drops": dropped_ascending,
            "retained_ascending_frame_ticks": [frame.tick for frame in retained],
            "checkpoint_tick": CHECKPOINT_TICK,
            "matching_continuation_steps": mirrored_steps,
            "final_checkpoint_sha256": checkpoint["sha256"],
            "estop_inhibited_next_tick": True,
            "restore_preserved_live_estop": True,
        },
        "measurements": {
            "workload": "primary-plus-restored-continuation-and-safety-checks",
            "runtime_step_calls": step_calls,
            "wall_seconds": wall_seconds,
            "process_cpu_seconds": cpu_seconds,
            "mean_wall_microseconds_per_step_call": wall_seconds * 1_000_000 / step_calls,
            "interpretation": "host diagnostic; not a hard real-time or biological claim",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=_tick_count, default=MIN_TICKS,
                        help="logical base ticks (1200..120000; default 1200)")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.ticks), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
