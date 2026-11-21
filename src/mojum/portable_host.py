"""Bounded CPU-first foreground host for the canonical synthetic Aquarium.

The host is deliberately small: the Aquarium remains the only owner of the
organism state and commits each tick to its PRIVATE SQLite journal.  This
module supplies wall-clock pacing and clean stop handling around the existing
developmental sensorimotor fixture.  It is not a service manager, a physical
device adapter, a neural brain, or a 24/7 execution guarantee.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from importlib.resources import files
from pathlib import Path
import signal
import threading
import time

from .aquarium import Aquarium, AquariumError, V4_MODE
from .aquarium import MAX_MOTOR_REPLAY_EVENTS as MAX_REPLAY_EVENTS
from .graph import GraphError, load_graph
from .genesis import GenesisError, validate_seed


SCHEMA = "mojum.portable-host.run/2"
MIN_INTERVAL_MS = 20
MAX_INTERVAL_MS = 60 * 60 * 1000
MAX_TICKS = 1000


class PortableHostError(ValueError):
    """Invalid host plan or unsupported Aquarium mode."""


def validate_plan(ticks, interval_ms):
    """Validate resource bounds before opening or creating a journal."""
    if type(ticks) is not int or not 1 <= ticks <= MAX_TICKS:
        raise PortableHostError("ticks must be an integer in 1..1000")
    if type(interval_ms) is not int or not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
        raise PortableHostError("interval_ms must be an integer in 20..3600000")
    return interval_ms / 1000.0


def _require_sensorimotor_fixture(aquarium):
    """Reject accidental use with the scripted or shadow-only Aquarium."""
    mode = getattr(aquarium, "motor_mode", None)
    if mode not in ("developmental_fixture", V4_MODE):
        raise PortableHostError("portable host requires a supported internal motor mode")
    if getattr(aquarium, "motor", None) is None:
        raise PortableHostError("developmental motor controller is unavailable")
    if mode == V4_MODE:
        if getattr(aquarium, "sensory_bridge", None) is not None:
            raise PortableHostError("local_cell_v4 requires sensory_mode=none")
    elif getattr(aquarium, "sensory_bridge", None) is None:
        raise PortableHostError("portable host requires sensory_mode=toy_probe")


@contextmanager
def termination_signals(stop_event):
    """Request a stop between ticks and restore process-global handlers."""
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
        except ValueError as exc:
            raise PortableHostError("signal handlers require the main thread") from exc
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_paced(aquarium, *, ticks, interval_ms, stop_event,
              monotonic=None, wait=None):
    """Run at most ``ticks`` committed steps without catch-up bursts.

    ``interval_ms`` controls wall time only; Aquarium's virtual ``dt_ns`` is
    unchanged.  The clock is checked after every interruptible wait, so a
    spurious/early wake cannot authorize an early journal append.  A stop
    signal is observed before the next tick, while a tick already in progress
    is allowed to finish its SQLite CAS append.
    """
    interval_s = validate_plan(ticks, interval_ms)
    if stop_event is None or not callable(getattr(stop_event, "is_set", None)):
        raise PortableHostError("stop_event must support is_set()")
    if not callable(getattr(stop_event, "wait", None)):
        raise PortableHostError("stop_event must support wait(timeout)")
    _require_sensorimotor_fixture(aquarium)
    if getattr(aquarium, "motor_mode", None) == V4_MODE:
        try:
            required_events = aquarium.required_events_for_steps(ticks)
        except AquariumError as exc:
            raise PortableHostError(str(exc)) from exc
        event_budget = aquarium.journal_event_budget
    else:
        required_events = ticks
        event_budget = MAX_REPLAY_EVENTS
    if aquarium.store.count() + required_events > event_budget:
        raise PortableHostError(
            f"requested ticks exceed fixed {event_budget}-event replay budget; "
            "use a smaller bounded run"
        )

    clock = time.monotonic if monotonic is None else monotonic
    sleep_interruptible = stop_event.wait if wait is None else wait
    deadline = clock()
    completed = 0
    while completed < ticks and not stop_event.is_set():
        while not stop_event.is_set():
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep_interruptible(remaining)
        if stop_event.is_set():
            break

        # No external dx/dy is injected.  The developmental controller derives
        # the virtual intent from previously committed measured outcomes.
        aquarium.step()
        completed += 1
        # Delayed scheduling must not produce a burst of imaginary catch-up
        # ticks after the process wakes.
        deadline = max(deadline + interval_s, clock() + interval_s)
    return completed


def _load_inputs(genome_path, graph_path):
    try:
        seed = json.loads(genome_path.read_text(encoding="utf-8"))
        validate_seed(seed)
        graph = load_graph(graph_path)
    except (OSError, UnicodeError, ValueError, GraphError, GenesisError) as exc:
        raise PortableHostError(f"invalid genome or graph input: {exc}") from exc
    return seed, graph


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mojum bounded CPU-only local Aquarium host"
    )
    packaged = files("mojum").joinpath("data")
    parser.add_argument("--db", type=Path, required=True,
                        help="PRIVATE local SQLite path; no network access")
    parser.add_argument("--genome", type=Path,
                        default=packaged.joinpath("genesis_seed.json"))
    parser.add_argument("--graph", type=Path,
                        default=packaged.joinpath("first_loop.json"))
    parser.add_argument("--ticks", type=int, default=120,
                        help="bounded 1..1000-tick foreground session")
    parser.add_argument("--interval-ms", type=int, default=1000,
                        help="wall-clock delay 20..3600000 ms, not virtual dt")
    parser.add_argument("--motor-mode", choices=("developmental_fixture", V4_MODE),
                        default="developmental_fixture",
                        help="internal synthetic motor mode (default unchanged)")
    parser.add_argument("--sensory-mode", choices=("toy_probe", "none"),
                        default="toy_probe",
                        help="synthetic sensing; v4 requires none")
    parser.add_argument("--neural-mode", choices=("shadow", "disabled"),
                        default="shadow")
    parser.add_argument("--brain-mode", choices=("placeholder", "developmental_controller"),
                        default="placeholder",
                        help="opt-in synthetic controller; placeholder is the legacy default")
    parser.add_argument("--temporal-mode", choices=("none", "range_guided"),
                        default="range_guided",
                        help="whether measured range outcomes guide later actions")
    parser.add_argument("--expected-head",
                        help="optional independently pinned journal head SHA-256")
    args = parser.parse_args(argv)
    try:
        validate_plan(args.ticks, args.interval_ms)
        seed, graph = _load_inputs(args.genome, args.graph)
    except PortableHostError as exc:
        parser.error(str(exc))
    # Reject invalid mode combinations before creating any SQLite journal.
    if args.motor_mode == V4_MODE and args.sensory_mode != "none":
        parser.error("local_cell_v4 requires --sensory-mode none")
    if args.motor_mode == V4_MODE and args.temporal_mode != "none":
        parser.error("local_cell_v4 requires --temporal-mode none")
    if args.motor_mode == V4_MODE and args.brain_mode != "placeholder":
        parser.error("local_cell_v4 requires --brain-mode placeholder")

    stopped = threading.Event()
    started = time.monotonic()
    with termination_signals(stopped):
        try:
            with Aquarium(
                args.db, seed, graph,
                neural_mode=args.neural_mode,
                motor_mode=args.motor_mode,
                brain_mode=args.brain_mode,
                sensory_mode=args.sensory_mode,
                temporal_mode=args.temporal_mode,
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
                    "brain_mode": args.brain_mode,
                    "motor_mode": aquarium.motor_mode,
                    "sensory_mode": aquarium.sensory_mode,
                    "temporal_mode": args.temporal_mode,
                    "wall_elapsed_seconds": round(time.monotonic() - started, 3),
                    "stop_reason": "signal" if stopped.is_set() else "completed",
                    "classification": "PRIVATE_SYNTHETIC_FOREGROUND_NOT_FULL_BRAIN",
                    "journal_trust": "checksum_only_not_authenticated",
                }
        except (AquariumError, PortableHostError) as exc:
            parser.error(str(exc))
    # Stdout contains metadata only, never private frames, genome, sensor
    # measurements or learner state.
    print(json.dumps(status, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    main()
