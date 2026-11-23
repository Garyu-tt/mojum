# Portable foreground host: authored point-room draft (experimental)

Issue: #927, parent #11. This is an opt-in extension of the bounded,
single-device **synthetic** Aquarium. The canonical SQLite event journal remains
the sole authoritative state; this does not provide MuJoCo, a real neural model,
continuous uptime, or external-device access.

## Use from the repository root (Python 3.11+, stdlib only)

Create a `room.json` with the offline designer, or use this small test draft:

```json
{"schema":"mojum.world.point-room-draft/1","width":5,"height":4,"obstacles":[[2,1,0.1,2]],"start":[1,2]}
```

Run a paced synthetic session:

```sh
PYTHONPATH=src python -m mojum.portable_host \
  --db ./custom_room_PRIVATE.sqlite3 --room-file ./room.json \
  --ticks 2 --interval-ms 20 --neural-mode disabled
```

Repeat **with the same `--room-file`** to resume and append two frames. The
journal bootstrap pins the converted runtime geometry and start position. On
resume, omission of the draft or a mismatch in geometry/start is rejected before
any new events are appended. The original file bytes are not authenticated or
hashed; two different drafts that yield the same runtime geometry are compatible.

Without `--room-file`, the host retains the built-in room and its existing
`mojum.portable-host.run/2` JSON output. In both cases stdout reports **metadata
only**, not PRIVATE observations, genome, geometry, or file paths.

## Rejection and boundaries

The input is admitted by the same strict `load_runtime_room` function used
by `mojum.aquarium`: explicitly versioned JSON, bounded input size, duplicate
key rejection, geometric validation, safe initial point, and regular-file checks.
A bad, missing, oversized, or unsupported draft fails **before** writable
SQLite is opened. Symlink rejection is available where the platform supports
`O_NOFOLLOW`. On POSIX the draft is opened nonblocking and FIFOs are rejected.

The opt-in `local_cell_v4` motor profile continues to require its original
built-in room and therefore **rejects** `--room-file` rather than silently
switching geometry. Older compatibility policies are not enabled by this host.

The room draft is not an external world adapter or executable code. Its
geometry is read at start and cannot be hot-swapped into a living journal.
A new world layout requires a **new** PRIVATE journal path. The path may
identify private world configuration, so it is not emitted to stdout.

## Offline regression tests

```sh
PYTHONPATH=src python -m unittest -v tests.test_portable_host_room_draft
PYTHONPATH=src python -m unittest -v tests.test_portable_host tests.test_room_draft_runtime
```

Tests verify the custom room bootstrap, paced cold restart, refusal to resume
without the pinned room or after changing it, absent/invalid/oversized draft
rejection before DB birth, v4 compatibility rejection, and default host ABI.
No online APIs or external workers are required.

## Explicit limits

- This is the existing deterministic 2D synthetic room, not physical contact
  or real MuJoCo simulation.
- This foreground process commits bounded ticks only while running; a stopped
  process does not advance the organism or guarantee 24/7 availability.
- SQLite journal replay provides checksum validation but not cryptographically
  authenticated private storage or distributed failover.
