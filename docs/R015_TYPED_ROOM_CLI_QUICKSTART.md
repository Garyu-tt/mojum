# R0.15 — create and resume a custom CPU point-room from JSON (Draft)

This is a small **operational CLI**, not a new orchestrator, real MuJoCo body, 3D engine, neural brain or general scene loader. It extends the **new-journal-only** `mojum.aquarium.bootstrap/2` typed numeric ABI from [#138](https://github.com/Garyu-tt/mojum/pull/138) and the independently anchored existing-journal resume from [#145](https://github.com/Garyu-tt/mojum/pull/145).

## Why this is useful

Before this change, creating a room with nondefault dimensions/obstacles required writing a Python call `Aquarium(room=Room(...), start=(...))`. Afterward the room could be recovered from `room_exact`, but creating it was still programmer-only.

Now there are **two authoring formats** accepted by the same strictly opt-in `--new-room-contract` argument:

- **Simple numeric JSON (recommended):** `mojum.world.point-room-draft/1`, e.g. [`examples/room_simple_mixed.json`](../examples/room_simple_mixed.json). Enter ordinary JSON numbers: `4` remains an original Python `int`, `4.0` remains a binary64 `float`, and `0.125` is converted to its exact binary64 representation internally. No need to hand-write hexadecimal floats.
- **Expert exact JSON (unchanged):** `mojum.world.room-exact-scalars/1`, e.g. [`examples/room_exact_mixed.json`](../examples/room_exact_mixed.json). Write explicit integer tags and `float.hex()` strings when exact original representation, including signed zero, matters.

**Both formats produce exactly the same `mojum.aquarium.bootstrap/2` persistent event ABI.** The beginner draft schema is input-only and must **never** appear as a journal bootstrap or learned memory event. Old default `bootstrap/1` remains byte-for-byte unchanged.

Example of the complete beginner file:

```json
{
  "schema": "mojum.world.point-room-draft/1",
  "width": 4.5,
  "height": 4,
  "obstacles": [[2.0, 1, 0.125, 2.0]],
  "start": [1.25, 2.5]
}
```

This describes a 4.5m × 4m room, a rectangular obstacle at (2.0m,1m) with width 0.125m and height 2.0m, and an initial point position (1.25m,2.5m). Draft dimensions are capped at 1000m; both formats are restricted to at most 4096 obstacles and a 1 MiB local file. The start coordinates are normalized to finite binary64 floats while original room dimensions/rectangle numeric types remain distinct. This is the existing deterministic swept-AABB point fixture, not graphics or MuJoCo physical dynamics.

## 0. Check the room without creating any SQLite data

```sh
PYTHONPATH=src python -m mojum.room_input examples/room_simple_mixed.json
```

This read-only local-file preflight prints dimensions, original `int`/`float` types, obstacle count, initial coordinates and deterministic initial world revision. A malformed JSON definition exits with a nonzero status and **no SQLite file is created**. The expert hexadecimal form produces the same preview for the same geometry. This preview is not a simulation, Noosphere integrity proof or environmental reality check.

## 1. Create a new room and run 12 synthetic steps

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./my-new-synthetic-room.sqlite3 \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v2 \
  --room-numeric-contract typed_v1 \
  --new-room-contract examples/room_simple_mixed.json \
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

- Accepts **only a local preexisting regular, nonsymlink UTF-8 JSON file** no larger than 1 MiB. Rejects duplicate JSON keys, NaN/Infinity, unknown schema, extra/missing fields, invalid hexadecimal floats (expert format), booleans, geometry outside the room, draft dimensions above 1000m, excessive obstacles and invalid start positions. The simple authoring format allows only numbers/arrays, not arbitrary object commands, file references or scripts.
- `--new-room-contract` requires `--room-numeric-contract typed_v1`; it cannot be mixed with `--resume-typed-room` or historical read-only v2A mode. Invalid input is checked **before** any SQLite journal birth. Existing output is rejected, not rewritten.
- No optional remote URLs, filesystem traversal from JSON, dynamic imports, model prompting, running arbitrary code from input or private data export.
- Default paths and default Aquarium behavior without `--new-room-contract` **remain unchanged**. The new option is strictly opt-in.

## Acceptance tests

Dedicated CPython 3.11, 3.12, 3.13 workflow uses separate subprocesses and real disposable PRIVATE SQLite journals. It checks successful exact-geometry new-room creation and original-number-type manifest, **byte-for-byte equal SQLite event streams and final checkpoint between simple numeric JSON and expert exact JSON**, journal event counts, cold read-only zero-tick reopen without the file, explicit continuation with a new head, invalid/ambiguous/oversized JSON and unsafe draft values fail-closed with no destination file, refusal of existing DB/legacy option/conflicting resume, and empty-frame room creation. It also starts two simultaneous CLI creators of the same target and confirms **exactly one succeeds**, checks final row count, and asserts input/output symlinks never lead to overwriting unrelated private data. Full inherited regression workflows stay required.

All this remains an isolated **Draft**. Independent review and release approval are required; neither this CLI nor green synthetic tests resolve [#116](https://github.com/Garyu-tt/mojum/issues/116) (old v2A/v2B wire ABI collision). Never run real private agent data through an unreviewed experimental PR.
