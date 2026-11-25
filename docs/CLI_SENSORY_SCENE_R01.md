# Replaying an explicitly empty sensory scene

The trace exporter and both cold-copy commands accept
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
