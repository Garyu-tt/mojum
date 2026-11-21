"""Real >10k-event SQLite journal: bounded streaming replay evidence.

Not discovered by ``unittest discover`` (filename does not start with
``test_``). Run explicitly:

    PYTHONPATH=src python3 tests/stress_streaming_replay.py [frames]

Builds a synthetic PRIVATE journal with more than the legacy 10,000-event
limit, cold-reopens it while ``LocalNoosphere.dataset`` is disabled (proving
replay streams instead of loading the whole projection), appends one more
frame after full replay, and prints counts and timings only. No frame payload
is printed. This is a synthetic CPU-only fixture, not a claim of endless
operation or authenticated history.
"""

import argparse
import json
from pathlib import Path
import tempfile
import time
import unittest.mock

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mojum.aquarium import MAX_REPLAY_EVENTS, Aquarium  # noqa: E402
from mojum.noosphere import LocalNoosphere  # noqa: E402


SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", nargs="?", type=int, default=10_001)
    parser.add_argument("--db", type=Path, default=None,
                        help="optional persistent synthetic path (deleted by caller)")
    args = parser.parse_args()
    frames = args.frames
    total_events = frames + 1  # bootstrap + one frame per tick
    if total_events > MAX_REPLAY_EVENTS:
        parser.error(f"{total_events} events exceeds the {MAX_REPLAY_EVENTS:,} ceiling")

    temporary = None if args.db is not None else tempfile.TemporaryDirectory()
    try:
        db = args.db if args.db is not None else Path(temporary.name) / "stress.sqlite3"
        started = time.perf_counter()
        with Aquarium(db, SEED, GRAPH) as aquarium:
            for _ in range(frames):
                aquarium.step(dx=0.02)
            expected_tick = aquarium.tick
            expected_head = aquarium.checkpoint()["journal_head"]
        build_seconds = time.perf_counter() - started

        started = time.perf_counter()
        with unittest.mock.patch.object(
                LocalNoosphere, "dataset",
                side_effect=AssertionError("full-history dataset load")):
            with Aquarium(db, SEED, GRAPH) as reopened:
                replay_seconds = time.perf_counter() - started
                assert reopened.tick == expected_tick, (reopened.tick, expected_tick)
                assert reopened.stored_events == total_events, reopened.stored_events
                assert reopened.checkpoint()["journal_head"] == expected_head
                assert reopened.store.count() == total_events
                started = time.perf_counter()
                reopened.step(dx=0.02)
                append_seconds = time.perf_counter() - started
                assert reopened.tick == expected_tick + 1
                assert reopened.stored_events == total_events + 1

        size = db.stat().st_size
        print(json.dumps({
            "status": "PASS_SYNTHETIC_STREAMING_REPLAY",
            "frames": frames,
            "events_after_build": total_events,
            "events_after_append": total_events + 1,
            "sqlite_bytes": size,
            "build_seconds": round(build_seconds, 3),
            "cold_replay_seconds": round(replay_seconds, 3),
            "single_append_seconds": round(append_seconds, 3),
            "dataset_used_during_replay": False,
            "ceiling": MAX_REPLAY_EVENTS,
            "note": "synthetic CPU-only fixture; CPU and disk grow linearly with history",
        }, indent=2))
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    main()
