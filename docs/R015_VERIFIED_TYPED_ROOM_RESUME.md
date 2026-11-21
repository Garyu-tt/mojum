# R0.15 — explicit, independently anchored typed-room cold resume

**Draft QA, not a release.** This is a direct child of [#138](https://github.com/Garyu-tt/mojum/pull/138), which added `mojum.aquarium.bootstrap/2` and exact int/float numeric room data for **new** SQLite journals. No historical `bootstrap/1` journal is upgraded, rewritten, or inferred into a new geometry.

## Practical purpose

A new `typed_v1` Aquarium preserves the original numeric types in a `room_exact` field: `int(4)`, `float(4.0)`, negative zero and the hexadecimal representation of all finite floats remain distinct. Previously, cold replay still required the caller to supply the original Python `Room` object and starting coordinates from external configuration. That defeats the purpose if the application restarts with only its SQLite journal.

This branch adds an **explicit preflight** `load_verified_typed_room(database, agent_id=..., expected_journal_head=...)` and an optional CLI `--resume-typed-room`. The function:

1. Requires an existing non-symlink local SQLite file, known agent ID and independently supplied 64-character lowercase hexadecimal journal head.
2. Opens the database through the existing `LocalNoosphere(read_only=True)` SQLite `mode=ro` implementation; never executes bootstrap DDL, never appends events, never adopts source data before full verification.
3. Verifies **the complete canonical JSON and hash chain** against the supplied external head, checks the exact `mojum.aquarium.bootstrap/2` event, reads the pinned `room_exact`, and validates it against the legacy projection with type-sensitive serialization.
4. Reconstructs an original `Room` and normalized start using their real int/float representations. Rechecks the journal head/count before closing the read-only connection.
5. Supplies the reconstructed room to the regular Aquarium constructor, which **rechecks the same independently supplied head** and full deterministic replay before any new frame is permitted.

## CLI

For an **existing typed room journal** created with the exact versioned contract:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db /path/to/previously-created-private-typed.sqlite3 \
  --room-numeric-contract typed_v1 \
  --resume-typed-room \
  --expected-journal-head <independently-recorded-64-hex-head> \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v2
```

The restore CLI **defaults to `--ticks 0`**. This now selects a second **SQLite-enforced `mode=ro` / `PRAGMA query_only=ON` Aquarium connection for the entire no-tick inspection**, not just its preflight. The `step`, `acquire`, `save_replay_snapshot` and underlying SQLite `append` paths are all rejected; no WAL conversion, chmod, bootstrap DDL or journal event append is requested by the inspector. A **new non-restore** Aquarium invocation keeps its original default of 120 ticks. To continue writing, the operator must explicitly pass a positive `--ticks N`; this opens a normal writable Aquarium after the same independently anchored preflight and replay. This does not create a journal from a missing path. Supplying only an expected head without the restore flag, selecting a legacy room contract, a different policy, wrong seed, wrong neural/sensory configuration, or an invalid stored room triggers fail-closed error.

The original Aquarium CLI only supports the local synthetic fixture agent ID `dante-fixture-001`; the preflight Python API accepts an explicit agent ID. This feature does **not** claim general agent discovery, browser UI support, distributed authorization, arbitrary physical robotics or access to production Noosphere.

## Limitations, security, and acceptance

- The supplied head must come from an **independent trusted record**. If copied from the same compromised local database, the local chain is not authenticated. There is no key management or digital signature in this helper.
- If another writer advances the journal concurrently, the second pinned-head validation must reject a changed head. The helper does not implement a distributed lock or automatically recover a new one.
- The exact original globe/room contract is only available for **new** `bootstrap/2` histories. Old `bootstrap/1` rooms cannot be reconstructed losslessly if their dimension types were already discarded. The limited explicit v2A → v2B importer [#135](https://github.com/Garyu-tt/mojum/pull/135) remains restricted to its known default synthetic room.
- No external motor actuation, HMAC cache key copying, MuJoCo integration, real Android background runs, neural brain or authenticated multi-node availability is established.

An independent review and owner release decision remain required. This QA extension must not merge automatically or close P0 [#116](https://github.com/Garyu-tt/mojum/issues/116), whose separate old v2A/v2B direct-interchange release gate is intentionally red.
