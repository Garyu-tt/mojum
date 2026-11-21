# R0.15 — exact numeric room bootstrap, opt-in for NEW journals

**Status: Draft, synthetic point-room only; no automatic migration or release approval.** This work is stacked on experimental migration PR #135, which is itself stacked on #131/#125. Original v2A/v2B/v3 journal bytes, signed replay cache, main, original PR branches and Noosphere histories remain unchanged.

## Precise underlying problem

The legacy Aquarium bootstrap uses `width: float(room.width)` and `height: float(room.height)`, which makes the bootstrap of `Room(4,4,...)` indistinguishable from `Room(4.0,4.0,...)` on those fields. But `world_room._revision` hashes the **original Python** scalar representations: `json.dumps([4,4,...])` is different from `json.dumps([4.0,4.0,...])`. Reconstructing old source rooms from such ambiguous metadata can therefore invalidate deterministic world-state hashes, even if floating-point geometry looks the same. The earlier v2A→v2B migration safely had to restrict itself to the single exact original built-in room.

## Implementation — no change to existing default

New explicit `room_numeric_contract="typed_v1"` option for **new** Aquarium journals and CLI `--room-numeric-contract typed_v1`. The default stays `legacy`, byte-for-byte, for all existing persisted R0.13/R0.14 room manifests. An existing journal cannot silently switch between contracts; replay compares the complete bootstrap and fails closed. Historical v2A archive mode **cannot** opt in or be rewritten.

Typed new journals use a **new top-level bootstrap identity** `mojum.aquarium.bootstrap/2` (legacy retains exactly `mojum.aquarium.bootstrap/1`), retain the old `room` projection for explicit comparison, and **add** an independent versioned `room_exact` field:

```json
{
  "schema": "mojum.world.room-exact-scalars/1",
  "width": {"type": "int", "value": 4},
  "height": {"type": "float", "hex": "0x1.0000000000000p+2"},
  "obstacles": [[
    {"type": "int", "value": 2},
    {"type": "float", "hex": "0x1.0000000000000p+0"},
    {"type": "float", "hex": "0x1.999999999999ap-4"},
    {"type": "int", "value": 2}
  ]],
  "start": [
    {"type": "float", "hex": "0x1.0000000000000p+0"},
    {"type": "float", "hex": "0x1.0000000000000p+1"}
  ]
}
```

Signed 64-bit original integers and exact finite binary64 `float.hex()` values are encoded distinctly. Strict decoder rejects unknown schemas/fields, noncanonical float encodings, nonfinite numbers, booleans, mixed scalar tags, oversized integers, invalid obstacles/starts and inconsistencies with the companion old `room` projection. It reconstructs original Python scalar types before computing `world_revision`; a complete encode→decode→encode is required. No goals, rewards, human pain signals, hidden geometry to the motor policy, or external actuator capability is added.

## Tests and compatibility

Dedicated Python 3.11, 3.12, 3.13 workflow checks lossless room reconstruction, unchanged world revision through multiple advances, distinction of int vs float dimensions that the legacy bootstrap cannot represent, rejection of corrupt/ambiguous scalar records, binary float exactness and negative zero, actual multi-tick SQLite Aquarium cold replay using different scalar types, fail-closed attempts to open a typed journal as legacy (and vice versa), no unexpected event append after failed open, and CLI explicit opt-in.

A second immutable-source CI control checks out the exact pre-change [#135](https://github.com/Garyu-tt/mojum/pull/135) SHA `961e9954248dc9fb3a2803a3ab4108c96606ff42`, runs the old and new Aquarium as **separate processes** in five default-format modes (scripted, goal-free legacy, v2B, v3B and goal-free toy-probe), and compares all SQLite event rows including literal `wire_json` and every event hash, as well as the final checkpoint JSON. This is a stronger historical ABI check than simply re-opening the new branch's own old-format journal.

Old **v2A→v2B migration remains restricted to the original room**, because these new fields cannot be retrospectively reconstructed from an old ambiguous bootstrap. New typed histories can be used in a **future** separately reviewed importer; this PR does not claim arbitrary historical geometry recovery, compatible cross-version event hashes or full semantic portability.

## Limitations

The original world-room revision algorithm still depends on CPython/Python JSON serialization of the reconstructed original scalar types and does not define an independent polyglot binary hash canonicalization standard. This contract captures enough information for exact replay within supported CPython versions but cannot guarantee cross-language or future-runtime hash compatibility. No physical Android, MuJoCo, real neural brain, HMAC key transport, distributed failover, independent account reviewer or owner approval has been established.

Do not merge automatically, turn the R0.14 [#116](https://github.com/Garyu-tt/mojum/issues/116) red direct wire gate green, or rewrite any existing original journal.
