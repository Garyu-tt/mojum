# R0.15 — create and resume a custom CPU point-room from JSON (Draft)

This is a small **operational CLI**, not a new orchestrator, real MuJoCo body, 3D engine, neural brain or general scene loader. It extends the **new-journal-only** `mojum.aquarium.bootstrap/2` typed numeric ABI from [#138](https://github.com/Garyu-tt/mojum/pull/138) and the independently anchored existing-journal resume from [#145](https://github.com/Garyu-tt/mojum/pull/145).

## Why this is useful

Before this change, creating a room with nondefault dimensions/obstacles required writing a Python call `Aquarium(room=Room(...), start=(...))`. Afterward the room could be recovered from `room_exact`, but creating it was still programmer-only.

Now there is a deliberately constrained **local JSON input** for a **new synthetic journal**. It uses the exact `mojum.world.room-exact-scalars/1` schema, retaining every original `int` separately from every finite `float.hex()` representation.

The checked-in example [`examples/room_exact_mixed.json`](../examples/room_exact_mixed.json) describes a 4.5m × 4m point-room, a rectangle at x=2.0m, y=1m, width=0.125m, height=2.0m and a start at (1.25m,2.5m). No graphics/MuJoCo collision physics is implied: this is the existing deterministic swept-AABB point fixture.

## 1. Create a new room and run 12 synthetic steps

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./my-new-synthetic-room.sqlite3 \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v2 \
  --room-numeric-contract typed_v1 \
  --new-room-contract examples/room_exact_mixed.json \
  --ticks 12
```

The command returns a JSON checkpoint including `journal_head`. Record that expected head **outside** the SQLite file; reading a hash only from a compromised database cannot authenticate its contents.

This new-room operation refuses an existing SQLite destination **before** opening it. It also reserves the new filename with `O_CREAT|O_EXCL` (`O_NOFOLLOW` when supported), so two concurrent cooperating creators cannot both create the same journal or truncate an existing file. A crash **after exclusive reservation but before SQLite bootstrap** may leave an empty file: the CLI deliberately refuses to reuse or automatically delete that possibly owned stub. Inspect it explicitly before choosing a different fresh path. This is **not a distributed lock** for subsequent writes; do not point it at a live, shared directory or untrusted filesystem. No historical Noosphere `bootstrap/1` journal is converted or silently adopted.

## 2. Inspect/reopen the same room with zero new writes

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./my-new-synthetic-room.sqlite3 \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v2 \
  --room-numeric-contract typed_v1 \
  --resume-typed-room \
  --expected-journal-head YOUR_INDEPENDENT_64_HEX_HEAD
```

With `--resume-typed-room` and no `--ticks`, the command explicitly defaults to **0 ticks**. The original `Room` and starting coordinates are reconstructed from the verified stored exact numeric type tags. The entire inspect-only Aquarium is SQLite `mode=ro` + `query_only=ON`, and all app-level write paths are refused.

To continue for exactly N steps, supply `--ticks N` **and the current independently recorded head**. A positive value explicitly requests normal writable replay/continuation, and the resulting head changes.

## Parsing and integrity boundaries

- Accepts **only a local preexisting regular, nonsymlink UTF-8 JSON file** no larger than 1 MiB. Rejects duplicate JSON keys, NaN/Infinity, unknown schema, extra/missing fields, invalid hexadecimal floats, booleans, geometry outside the room, excessive obstacles and invalid start positions.
- `--new-room-contract` requires `--room-numeric-contract typed_v1`; it cannot be mixed with `--resume-typed-room` or historical read-only v2A mode. Invalid input is checked **before** any SQLite journal birth. Existing output is rejected, not rewritten.
- No optional remote URLs, filesystem traversal from JSON, dynamic imports, model prompting, running arbitrary code from input or private data export.
- Default paths and default Aquarium behavior without `--new-room-contract` **remain unchanged**. The new option is strictly opt-in.

## Acceptance tests

Dedicated CPython 3.11, 3.12, 3.13 workflow uses separate subprocesses and real disposable PRIVATE SQLite journals. It checks successful exact-geometry new-room creation and original-number-type manifest, journal event counts, cold read-only zero-tick reopen without the file, explicit continuation with a new head, invalid/ambiguous/oversized JSON fail-closed with no destination file, refusal of existing DB/legacy option/conflicting resume, and empty-frame room creation. It also starts two simultaneous CLI creators of the same target and confirms **exactly one succeeds**, checks final row count, and asserts input/output symlinks never lead to overwriting unrelated private data. Full inherited regression workflows stay required.

All this remains an isolated **Draft**. Independent review and release approval are required; neither this CLI nor green synthetic tests resolve [#116](https://github.com/Garyu-tt/mojum/issues/116) (old v2A/v2B wire ABI collision). Never run real private agent data through an unreviewed experimental PR.
