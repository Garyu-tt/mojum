"""Read-only journal capacity witness for the pinned synthetic Local Cell v4 ABI.

This module does not open SQLite, authenticate a journal, or authorize writes.
It assumes a canonical bootstrap + frame + epoch event stream. Never use its
arithmetic as a replacement for Aquarium's CAS/replay validation.
"""

from __future__ import annotations

import argparse
import json

from .epoch_sense_v2 import EPOCH_TICKS, MAX_ABSOLUTE_TICKS

SCHEMA = "mojum.aquarium.epoch-capacity-witness/1"
# Version-pinned to the v4 fixture's MAX_REPLAY_EVENTS budget. This module
# deliberately cannot be used for other motor modes or future segment formats.
MAX_JOURNAL_EVENTS = 100_000
MAX_EPOCH_MARKERS = (MAX_ABSOLUTE_TICKS - 1) // EPOCH_TICKS


class EpochCapacityError(ValueError):
    """Invalid/inconsistent caller-supplied v4 journal accounting metadata."""


def plan_epoch_capacity(*, absolute_tick: int, epoch_index: int,
                        stored_events: int, steps: int) -> dict:
    """Validate one v4 journal cursor and forecast a bounded append.

    ``absolute_tick`` is the *next* uncommitted motor frame tick, not the last
    committed one. At an epoch boundary, the epoch marker may already be
    durable even if no motor frame has followed it. ``epoch_index`` denotes
    the last durable marker adopted by the caller. The exact event count is
    one bootstrap + ``absolute_tick`` frames + ``epoch_index`` markers.

    At the terminal frame cap, a new empty epoch is not allowed. Passing a
    consistent tuple is not proof the underlying SQLite journal is genuine.
    """
    if (type(absolute_tick) is not int
            or not 0 <= absolute_tick <= MAX_ABSOLUTE_TICKS
            or type(epoch_index) is not int
            or not 0 <= epoch_index <= MAX_EPOCH_MARKERS
            or type(stored_events) is not int
            or not 1 <= stored_events <= MAX_JOURNAL_EVENTS
            or type(steps) is not int
            or not 0 <= steps <= MAX_ABSOLUTE_TICKS):
        raise EpochCapacityError("invalid v4 cursor or requested step bounds")

    # A marker belongs to the next epoch only when at least one frame of that
    # epoch is admissible. At a boundary the last marker can be pending (old
    # index) or already durable (new index); anywhere else the index is exact.
    expected_epoch = min(absolute_tick // EPOCH_TICKS, MAX_EPOCH_MARKERS)
    if absolute_tick and absolute_tick % EPOCH_TICKS == 0:
        allowed_indices = {expected_epoch - 1, expected_epoch}
        if absolute_tick == MAX_ABSOLUTE_TICKS:
            allowed_indices = {MAX_EPOCH_MARKERS}
    else:
        allowed_indices = {expected_epoch}
    if epoch_index not in allowed_indices:
        raise EpochCapacityError("epoch index inconsistent with absolute tick")
    if stored_events != 1 + absolute_tick + epoch_index:
        raise EpochCapacityError("committed event count disagrees with v4 cursor")

    end = absolute_tick + steps
    if end > MAX_ABSOLUTE_TICKS:
        raise EpochCapacityError("v4 absolute frame budget exhausted")

    final_epoch = ((end - 1) // EPOCH_TICKS) if steps else epoch_index
    markers = tuple(range(epoch_index + 1, final_epoch + 1))
    needed = steps + len(markers)
    total = stored_events + needed
    if total > MAX_JOURNAL_EVENTS:
        raise EpochCapacityError("v4 fixed journal event budget exhausted")

    return {
        "schema": SCHEMA,
        "absolute_start_tick": absolute_tick,
        "absolute_end_tick": end,
        "start_epoch_index": epoch_index,
        "end_epoch_index": final_epoch,
        "requested_frames": steps,
        "new_epoch_markers": len(markers),
        "marker_absolute_ticks": [i * EPOCH_TICKS for i in markers],
        "required_new_events": needed,
        "stored_events_before": stored_events,
        "stored_events_after": total,
        "remaining_event_slots": MAX_JOURNAL_EVENTS - total,
        "remaining_frame_slots": MAX_ABSOLUTE_TICKS - end,
        "evidence": "arithmetic_only_not_authenticated_not_write_authority",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline read-only Local Cell v4 epoch journal capacity witness"
    )
    parser.add_argument("--absolute-tick", required=True, type=int)
    parser.add_argument("--epoch-index", required=True, type=int)
    parser.add_argument("--stored-events", required=True, type=int)
    parser.add_argument("--steps", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        witness = plan_epoch_capacity(
            absolute_tick=args.absolute_tick, epoch_index=args.epoch_index,
            stored_events=args.stored_events, steps=args.steps,
        )
    except EpochCapacityError as exc:
        parser.error(str(exc))
    print(json.dumps(witness, sort_keys=True, separators=(",", ":")))
    return witness


if __name__ == "__main__":
    main()
