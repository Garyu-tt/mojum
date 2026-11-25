# Running and replaying an explicitly empty sensory scene

The Aquarium runtime, development and GENESIS commands, trace exporter and both cold-copy commands accept
`--sensory-scene {default,empty}`. Use the same room, start, motor, brain,
neural, sensory and temporal configuration that created the source journal.
The journal's existing bootstrap and replay checks still enforce that match.

| Selection | Replay configuration |
| --- | --- |
| Omitted or `default` | Preserve the existing Aquarium default, including built-in toys when a toy sensory mode is enabled. |
| `empty` | Use no toys, with the admitted `--room-file` dimensions or the built-in room dimensions when no room file is supplied. Requires `toy_probe` or `toy_probe_v2`. |

An authored room does not imply an empty scene. The commands do not inspect
the journal to choose a scene, resize the built-in toys, rewrite a scene,
or repair a configuration mismatch. A wrong selection fails without publishing
an export or altering the input journal. This includes an empty `4×4` scene:
matching the default dimensions does not make its bootstrap match the default
toy scene.

## Continue the same organism through the runtime or foreground host

The foreground host uses an empty scene when `--room-file` is supplied.
To continue that journal through the unpaced `mojum-aquarium` command, select
the same scene explicitly, together with the original room and modes:

```sh
mojum-aquarium --db habitat.sqlite3 --room-file room.json \
  --motor-mode developmental_fixture --sensory-mode toy_probe \
  --sensory-scene empty --temporal-mode range_guided \
  --neural-mode disabled --ticks 2 --expected-head "$SOURCE_HEAD"
```

The command cold-replays the existing history and appends exactly two frames.
Use `--ticks 0` to replay without advancing the physics clock. The same flags
also create a new empty-scene journal when the database path is new and
`--expected-head` is omitted. That journal can subsequently resume in the
foreground host with the same room and mode flags. Preserve each command's
returned head when using an independent anchor for the next command.

Without `--room-file`, `empty` uses the built-in room dimensions. It also works
with `local_cell_v4` and `toy_probe_v2`; fixed-geometry local-cell modes continue
to reject an authored room. The foreground host retains its own scene defaults:
an empty built-in scene selected through Aquarium does not imply that the host
will select it when no authored room is supplied.

Wrong selectors or scene/mode combinations are command errors. Scene geometry
or bootstrap mismatches are reported without an uncaught traceback, and an
existing incompatible journal is never rewritten to fit the requested scene.

## Trace and backup workflow

For an existing empty-scene journal created with an authored room,
`developmental_fixture`, `toy_probe`, `range_guided`, and disabled neural
learning, the following commands preserve those settings. Replace the paths
and supply the independently recorded source head:

```sh
python -m mojum.aquarium_trace_export \
  --db habitat.sqlite3 --output habitat.private.jsonl \
  --room-file room.json --motor-mode developmental_fixture \
  --sensory-mode toy_probe --sensory-scene empty \
  --temporal-mode range_guided --neural-mode disabled \
  --expected-head "$SOURCE_HEAD" --allow-private-export

python -m mojum.aquarium_cold_copy export \
  --db habitat.sqlite3 --output habitat.backup.sqlite3 \
  --room-file room.json --motor-mode developmental_fixture \
  --sensory-mode toy_probe --sensory-scene empty \
  --temporal-mode range_guided --neural-mode disabled

python -m mojum.aquarium_cold_copy verify \
  --db habitat.backup.sqlite3 \
  --room-file room.json --motor-mode developmental_fixture \
  --sensory-mode toy_probe --sensory-scene empty \
  --temporal-mode range_guided --neural-mode disabled \
  --expected-head "$SOURCE_HEAD" \
  --expected-sha256 "$BACKUP_SHA256" --expected-tick "$SOURCE_TICK"
```

`BACKUP_SHA256` is the `db_sha256` returned by cold-copy export. Preserve the
expected head and tick independently from the source checkpoint; merely
copying assertions from an untrusted artifact does not authenticate it.
The trace exporter continues to require explicit PRIVATE history disclosure
consent. JSONL and SQLite outputs contain plaintext synthetic private history.
Existing output paths are never overwritten.

For `toy_probe_v2`, use the journal's original temporal setting (`none`);
`range_guided` remains specific to `toy_probe`. Fixed-geometry local-cell modes
still reject `--room-file`. Omitting a room file supports an explicitly empty
scene in the built-in room, but does not change that room's geometry or start.

## Compatibility and validation

Only CLI configuration forwarding changes. The Python export/verify APIs
already accept `sensory_scene=ToyScene(..., toys=())`; their signatures, journal
schemas, bootstrap identity, replay rules, and output schemas are unchanged.
Arbitrary custom toy collections remain a Python API configuration.

`tests/test_cli_sensory_scene.py` creates real committed journals with the
existing Aquarium API, then runs the public commands from another directory.
It checks exact exported frames, independent heads, backup checksums and ticks,
both sensor versions, shadow/disabled neural modes, authored and built-in room
dimensions, legacy defaults, scene mismatch, PRIVATE consent, and unchanged
source/artifact bytes and sidecar inventory on refusal. These tests do not
depend on an unmerged host feature.

`tests/test_aquarium_cli_sensory_scene.py` additionally exercises actual host
creation, zero-tick Aquarium replay, API-equivalent unpaced continuation,
host restart, trace export and verified cold copy for 4×4 and 5×4 rooms, both
sensor versions and both neural modes. It also checks Aquarium-created empty
rooms, unchanged default-scene hashes, the built-in v4 profile, invalid
selectors before journal access, and wrong-scene/stale-head refusal without
changing closed standalone journal bytes, permissions, mtime or sidecar inventory.
