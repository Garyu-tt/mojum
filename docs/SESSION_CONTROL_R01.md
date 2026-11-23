# Session control R0.1: tick-boundary lifecycle state machine

`mojum.session-control/1` is an optional, stdlib-only control-plane contract
for the Aquarium lifecycle. It models `created`, `running`, `paused`, and
`terminated` and accepts only these transitions:

| Current state | Command | Next state |
| --- | --- | --- |
| created | start | running |
| running | pause | paused |
| paused | resume | running |
| created / running / paused | terminate | terminated |

A caller supplies the last committed logical tick, a unique event ID, and the
expected current revision. Commands are rejected while a tick is in progress,
when the tick moves backwards, after termination, when a revision is stale, or
when the bounded 1,024-event history is full. Failed commands do not mutate
the state or consume the event ID. Snapshots replay the ordered transition
history and validate state, tick, sequence, IDs and checksum before restore.

## Usage

```python
from mojum.session_control import SessionController

session = SessionController("dante-fixture")
session.transition(
    "start", event_id="start-001",
    expected_revision=session.revision,
    committed_tick=0,
)
# The caller performs a complete Aquarium tick and commits it first.
session.transition(
    "pause", event_id="pause-001",
    expected_revision=session.revision,
    committed_tick=10,
)
snapshot = session.snapshot()
```

## Important boundary

This module is a pure in-memory value object. It does not run or control an
Aquarium process, install signal handlers, sleep, access SQLite, write Noosphere
records, coordinate threads, authenticate callers, or provide a distributed
lock. The caller must serialize writers and invoke it only after the matching
tick is committed. Its SHA-256 revision is a corruption/CAS token, not an
identity signature or proof of durable storage. Integration into
`portable_host` remains a separate, independently reviewed change.

## Verification

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_session_control.py' -v
```

The suite covers valid lifecycle replay, stale revisions, invalid transitions,
mid-tick refusal, duplicate IDs, monotonic ticks, tampered snapshots, strict
replay of impossible histories, input types and history bounds.
