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

Authored drafts describe **geometry only**, not spectator toy coordinates.
The host supplies an explicitly **empty** dimension-matched synthetic sensor
scene for custom rooms: the motor policy can move and record observations, but
it has no virtual toy contacts to learn from until an independently reviewed
authored-toy contract is available. Built-in rooms retain the historical
spectator toy fixture. No toys are silently inserted through authored walls.

The installed `mojum-host` command accepts the same options; `PYTHONPATH=src`
is only needed when running directly from the source checkout.

### Trace export and cold-copy replay

Trace export, both cold-copy commands and the developmental lifecycle command
accept `--sensory-scene empty`. Supply it with the same room and mode flags to
replay this custom host journal; passing only the room and modes is insufficient.
See the [installed workflow](INSTALLED_AQUARIUM_WORKFLOW_R01.md) for host
creation, developmental advance, restart, trace viewing and backup commands.

Their Python APIs also accept the same room, start, modes and an explicit
`sensory_scene=ToyScene(width=room.width, height=room.height, toys=())`.
The scene must match the one pinned in the journal bootstrap.

`export_private_trace`, `export_cold_copy`, and `verify_cold_copy` accept those
options. Trace export additionally requires `allow_private_export=True` and
can pin the `journal_head` returned by the host as `expected_journal_head`.
Cold-copy verification requires that head as `expected_journal_head` together
with `expected_agent_id`; the export's `db_sha256` can also be pinned.
The regression below exercises these APIs with a real custom host journal
and confirms that the source database bytes remain unchanged.
The [CLI scene selector contract](CLI_SENSORY_SCENE_R01.md) describes matching
defaults, empty scenes and wrong-configuration rejection.

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
PYTHONPATH=src python -m unittest discover -s tests -p 'test_portable_host*.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_room_draft_runtime.py' -v
```

Tests verify the custom room bootstrap, paced cold restart, refusal to resume
without the pinned room or after changing it, absent/invalid/oversized draft
rejection before DB birth, v4 compatibility rejection, and default host ABI.
They also cover signal interruption followed by cold restart and explicit
custom-scene replay through the trace and cold-copy Python APIs.
No online APIs or external workers are required.

## Explicit limits

- This is the existing deterministic 2D synthetic room, not physical contact
  or real MuJoCo simulation.
- This foreground process commits bounded ticks only while running; a stopped
  process does not advance the organism or guarantee 24/7 availability.
- SQLite journal replay provides checksum validation but not cryptographically
  authenticated private storage or distributed failover.
