"""Opt-in heavyweight CI: real Aquarium grows PAST the old 10,000-event cap.

Run once on Python 3.13 only. Uses a disposable synthetic SQLite journal
and no external files, GPU, services, private user data or browser uploads.

A genuine 10,002-frame single-journal run is then cold-replayed and a
range above tick 10,000 is replay-verified/exported. Keep this deliberately
OUT of regular 3-version unittest discovery to avoid multiplying CI costs.
"""

import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mojum.aquarium import Aquarium, MAX_REPLAY_EVENTS  # noqa: E402
from mojum.aquarium_trace_export import export_private_trace  # noqa: E402


SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
STEPS = 10_002


def main():
    assert MAX_REPLAY_EVENTS >= STEPS + 1, "hard journal cap not increased"
    with tempfile.TemporaryDirectory(prefix="mojum-stress-synthetic-") as tmp:
        root = Path(tmp)
        db = root / "synthetic.sqlite3"
        dest = root / "synthetic.private.jsonl"
        started = time.monotonic()
        with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as aquarium:
            for _ in range(STEPS):
                aquarium.step(dx=0.0, dy=0.0)
            checkpoint = aquarium.checkpoint()
            event_count = aquarium.store.count()
        built_in = time.monotonic() - started
        assert checkpoint["tick"] == STEPS
        assert event_count == STEPS + 1

        reopened_started = time.monotonic()
        with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as restored:
            assert restored.tick == STEPS
            assert restored.checkpoint() == checkpoint
            # The latest SQLite head must permit another canonical append,
            # not merely an offline read of the old history.
            extra = restored.step(dx=0.0, dy=0.0)
            assert extra["tick"] == STEPS
            live_head = restored.checkpoint()["journal_head"]
            assert restored.store.count() == STEPS + 2
        reopened_seconds = time.monotonic() - reopened_started

        exported_started = time.monotonic()
        metadata = export_private_trace(
            db, dest, SEED, GRAPH, allow_private_export=True,
            neural_mode="disabled",
            start_tick=10_000, frame_limit=3,
            expected_journal_head=live_head,
        )
        exported_seconds = time.monotonic() - exported_started
        records = [json.loads(line) for line in dest.read_text().splitlines()]
        frames = records[:-1]
        checkpoint_footer = records[-1]
        assert [frame["tick"] for frame in frames] == [10_000, 10_001, 10_002]
        assert metadata["first_tick"] == 10_000
        assert metadata["next_tick"] == 10_003
        assert metadata["source_total_ticks"] == 10_003
        assert not metadata["has_more"]
        assert metadata["event_count"] == STEPS + 2
        assert checkpoint_footer["journal_head"] == live_head
        assert checkpoint_footer["trace_integrity"]["frame_count"] == 3

        print(json.dumps({
            "status": "PASS_ACTUAL_10000_EVENT_BOUNDARY",
            "fixture": "synthetic-private-only",
            "source_events": metadata["event_count"],
            "last_tick": 10_002,
            "slice_ticks": [frame["tick"] for frame in frames],
            "sqlite_size_bytes": db.stat().st_size,
            "elapsed_seconds": {
                "append": round(built_in, 3),
                "cold_replay_and_append": round(reopened_seconds, 3),
                "private_export_full_replay": round(exported_seconds, 3),
            },
            "limitations": "100k hard cap; full CPU-linear verification; no fast snapshot",
        }, sort_keys=True))


if __name__ == "__main__":
    main()
