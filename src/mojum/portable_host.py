"""Opt-in CPU-first, offline, paced host for a synthetic Aquarium organism.

Usable on ordinary CPython 3.11+ (including Android Termux) without a GPU,
browser, native APK, network listener, service manager or external packages.
Each tick is already committed by the canonical Aquarium to PRIVATE SQLite;
restarting this process replays the journal rather than inventing new state.

This is intentionally a BOUNDED foreground runner, NOT an Android background
service, 24/7 guarantee, physical body or real neural brain.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import threading
import time

from .aquarium import Aquarium, MAX_REPLAY_EVENTS


SCHEMA = "mojum.portable-host.run/1"
MIN_INTERVAL_MS = 20
MAX_INTERVAL_MS = 60 * 60 * 1000
MAX_TICKS = 1000


class PortableHostError(ValueError):
    """Invalid CPU host plan, capacity or unsupported runtime assumption."""


def validate_plan(ticks, interval_ms):
    """Reject resource-unbounded plans *before* creating a SQLite organism."""
    if type(ticks) is not int or not 1 <= ticks <= MAX_TICKS:
        raise PortableHostError("ticks must be an integer in 1..1000")
    if type(interval_ms) is not int or not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
        raise PortableHostError("interval_ms must be an integer in 20..3600000")
    return interval_ms / 1000.0


@contextmanager
def termination_signals(stop_event):
    """Graceful Ctrl-C/TERM: finish an in-flight SQLite transaction, then stop.

    No process-global signal handlers are installed outside this short-lived
    CLI scope; restore previous handlers when exiting even on errors.
    """
    previous = {}

    def request_stop(_signum, _frame):
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        except ValueError:  # Not running in the main Python thread.
            raise PortableHostError("signal handlers require the main thread")
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_paced(aquarium, *, ticks, interval_ms, stop_event,
              monotonic=None, wait=None):
    """Execute a bounded number of *committed* goal-free synthetic ticks.

    Interval is wall-clock pacing; it MUST NOT change the canonical virtual
    clock dt_ns stored in the private SQLite manifest. Slow ticks/phone sleep
    never cause catch-up bursts or fake skipped journal events.

    The default wait uses Event.wait so TERM/INT interrupts idle sleep.
    Optional clock/wait injections make battery pacing deterministic in QA.
    """
    interval_s = validate_plan(ticks, interval_ms)
    if stop_event is None or not callable(getattr(stop_event, "is_set", None)):
        raise PortableHostError("stop_event must support is_set()")
    if not callable(getattr(stop_event, "wait", None)):
        raise PortableHostError("stop_event must support wait(timeout)")
    if aquarium.exploration_mode != "goal_free":
        raise PortableHostError("portable host requires opt-in goal_free Aquarium")
    if aquarium.store.count() + ticks > MAX_REPLAY_EVENTS:
        raise PortableHostError(
            "requested ticks exceed fixed 10000-event replay budget; "
            "use a smaller bounded run, streaming replay is not implemented"
        )

    clock = time.monotonic if monotonic is None else monotonic
    sleep_interruptible = stop_event.wait if wait is None else wait
    deadline = clock()
    completed = 0
    while completed < ticks and not stop_event.is_set():
        remaining = deadline - clock()
        if remaining > 0:
            sleep_interruptible(remaining)
            if stop_event.is_set():
                break
        # A failed SQLite CAS/append leaves committed state unchanged and is
        # never silently skipped. No synthetic user dx/dy is injected.
        aquarium.step()
        completed += 1
        # The next tick is not earlier than one full interval after the end
        # of the previous commit; delayed Android scheduling cannot replay a
        # huge burst of missed frames on wake.
        deadline = max(deadline + interval_s, clock() + interval_s)
    return completed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mojum CPU-only bounded local Aquarium host (Termux compatible)"
    )
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--db", type=Path, required=True,
                        help="PRIVATE local SQLite path; no network access")
    parser.add_argument("--genome", type=Path, default=root / "examples/genesis_seed.json")
    parser.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    parser.add_argument("--ticks", type=int, default=120,
                        help="one bounded 1..1000-tick foreground session")
    parser.add_argument("--interval-ms", type=int, default=1000,
                        help="wall-clock delay 20..3600000 ms, NOT virtual dt")
    parser.add_argument("--exploration-mode", choices=("goal_free",), default="goal_free")
    parser.add_argument("--sensory-mode", choices=("toy_probe", "none"),
                        default="toy_probe")
    parser.add_argument("--neural-mode", choices=("shadow", "disabled"),
                        default="shadow")
    parser.add_argument("--expected-head",
                        help="optional independently pinned source journal SHA-256")
    args = parser.parse_args(argv)
    try:
        validate_plan(args.ticks, args.interval_ms)
    except PortableHostError as exc:
        parser.error(str(exc))
    # Read and validate inputs before any new SQLite bootstrap is possible.
    seed = json.loads(args.genome.read_text(encoding="utf-8"))
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    stopped = threading.Event()
    started = time.monotonic()
    with termination_signals(stopped):
        with Aquarium(
            args.db, seed, graph,
            exploration_mode=args.exploration_mode,
            sensory_mode=args.sensory_mode,
            neural_mode=args.neural_mode,
            external_expected_head=args.expected_head,
        ) as aquarium:
            begin_tick = aquarium.tick
            committed = run_paced(
                aquarium, ticks=args.ticks, interval_ms=args.interval_ms,
                stop_event=stopped,
            )
            checkpoint = aquarium.checkpoint()
            status = {
                "schema": SCHEMA,
                "agent_id": aquarium.agent_id,
                "start_tick": begin_tick,
                "end_tick": aquarium.tick,
                "committed_ticks": committed,
                "journal_head": checkpoint["journal_head"],
                "journal_events": aquarium.store.count(),
                "virtual_dt_ns": aquarium.dt_ns,
                "wall_interval_ms": args.interval_ms,
                "wall_elapsed_seconds": round(time.monotonic() - started, 3),
                "stop_reason": "signal" if stopped.is_set() else "completed",
                "classification": "PRIVATE_SYNTHETIC_SINGLE_DEVICE_NOT_FULL_BRAIN",
                "journal_trust": "checksum_only_not_authenticated",
            }
    # Metadata only; NEVER dump PRIVATE frames, genome, learner weights,
    # actual sensor history or private Noosphere events on stdout.
    print(json.dumps(status, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    main()
