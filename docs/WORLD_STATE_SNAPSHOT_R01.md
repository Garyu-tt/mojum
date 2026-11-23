# Detached point-room state snapshot, R0.1

Related: #7 (world), #925 (exclusive implementation claim).

The functions mojum.world_state_snapshot.encode_snapshot(room, state) and
decode_snapshot(data) create and verify a deterministic detached snapshot of
the existing synthetic world_room Room and RoomState objects. Encoding returns
bytes; decoding reconstructs independent objects after strict validation.

This is **NOT** a SQLite / PRIVATE Aquarium checkpoint, signed continuity
proof, authorization, real sensor observation, MuJoCo simulation or external
device state. Nothing reads or writes a file, socket, journal or actual world.

## Wire contract

- Exact schema: mojum.world.point-room-state/1.
- Canonical UTF-8 JSON has exactly four top-level keys: fingerprint, room,
  schema, state. The room has width, height and obstacle rectangles. The state
  has logical tick, x, y and the existing room revision.
- The fingerprint is SHA-256 of the canonical JSON for the other three keys,
  **not a cryptographic signature/MAC**.
- Canonical encoding uses sorted keys, compact separators and exact Python
  int vs float spelling, preserving world_room._revision reproducibility.
- Before accepting, reconstruct strict Room/RoomState records and recompute
  the original point-room revision over geometry + position + tick.
- Fail closed on duplicate or unknown keys, invalid schema, noncanonical JSON,
  malformed/oversize numeric token, invalid geometry, obstacle or state,
  checksum mismatch, and stale/corrupt revisions.

## Resource bounds

Maximum document size: 1 MiB. Maximum obstacles: 4096. Finite coordinate
magnitudes: at most 1,000,000 m, with positive dimensions and extents.
Logical tick range: 0 through 1,000,000,000, inclusive. Numeric JSON token
length: at most 32 characters. These are stricter than the bare runtime Room
limits, deliberately: an oversize document fails, never silently truncates.

## Minimal Python usage

    from mojum.world_room import Room, initial_state, advance
    from mojum.world_state_snapshot import encode_snapshot, decode_snapshot

    room = Room(4, 4, ((2, 1, .1, 2),))
    state = initial_state(room, 1, 2)
    state, _ = advance(room, state, tick=1, dx=.1, dy=0,
                       expected_revision=state.revision)
    wire = encode_snapshot(room, state)
    restored_room, restored_state = decode_snapshot(wire)
    assert (restored_room, restored_state) == (room, state)

Focused tests:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_state_snapshot.py' -v

## Security boundary

The fingerprint only detects corruption or accidental rewriting. An adversary
can rewrite a body and its fingerprint together. Any importer must separately
establish origin/authorization before using a detached record. This module
does not attest history, past actions, remote host state or cross-device
continuity; it does not commit the snapshot into the canonical Aquarium.
