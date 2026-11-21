# R0.1 offline typed-room validator (`mojum-room-check`)

Read-only, standard-library validation of a room JSON draft emitted by the
offline [room designer](../tools/room_designer.html) or written by hand. It
opens no SQLite journal, mutates no world, grants no motor authority and never
touches a running organism.

```sh
PYTHONPATH=src python3 -m mojum.room_spec room.json
PYTHONPATH=src python3 -m mojum.room_spec room.json --format json
```

Exit codes: `0` valid, `1` invalid or unreadable document, `2` CLI usage error.

## Why number tokens matter

Legacy bootstrap manifests hash the *exact* JSON spelling of the built-in
point-room. Re-parsing `0.1` as a Python float changes the revision even though
the geometry looks identical, which is why only the canonical default room is
currently transferable (`src/mojum/local_cell_transfer.py`). This validator
therefore keeps every number token verbatim (`0.1`, `0.10000000000000001`,
`1e-1`, `4e0`) and echoes it unchanged in the report summary.

## Accepted document

The validator accepts the editor's `mojum.world.point-room-draft/1` format
and hand-written files with the same geometry keys and no `schema` field.
Other schema values and extra fields are rejected.

```json
{"schema": "mojum.world.point-room-draft/1", "width": 4, "height": 4, "obstacles": [[2, 1, 0.1, 2]], "start": [1.0, 2.0]}
```

- `width`, `height` — finite JSON numbers greater than zero.
- Every geometry number token is limited to 128 characters and must be representable as a finite non-underflowing Python float, matching the point-room runtime's numeric domain. The report preserves original token spelling, including `start` coordinates.
- `obstacles` — list of at most 4096 rectangles, each exactly four finite
  JSON numbers `[x, y, w, h]` with `w > 0`, `h > 0`, inside the room.
- `start` — exactly two finite numbers inside `[0, width] × [0, height]`, and
  not on or inside any obstacle. Obstacle boundaries are **closed** because the
  runtime's point-room also blocks a point that touches a wall.

After exact-decimal shape validation, the checker also verifies that the same
native JSON integers/floats and Aquarium's float start position are accepted
by the actual point-room fixture. This catches apparent decimal gaps that
vanish under float rounding and rectangles like `0.1 + 0.2` that exceed a
runtime wall at `0.3`. Such documents fail closed rather than receiving a
misleading valid preflight. This check does **not** rewrite or normalize the
original number spellings, launch the Aquarium or touch SQLite.

Rejected without partial acceptance: non-object roots, missing/unexpected keys,
duplicate JSON keys at any depth, `NaN`/`Infinity`, invalid UTF-8, invalid JSON,
documents larger than 256 KiB, booleans/strings/null used as geometry.

## Warnings (still valid)

- Overlap and exact-duplicate rectangles are reported; overlaps are a designer
  mistake but do not make the document unrepresentable.
- Pairwise overlap analysis runs only up to 256 rectangles so validation stays
  CPU-bounded; above that the report says so explicitly.

`estimated_free_area` subtracts each rectangle separately, so overlapping
rectangles can make it understate free space. It is a diagnostic, not a
physical measure.
