# R0.14: opt-in v2A → v2B replay into a NEW SQLite journal

**Research-only Draft, not an automatic upgrade, not an in-place migration, not canonical ABI approval.** This independent QA branch is based on the read-only historical archive [#131](https://github.com/Garyu-tt/mojum/pull/131) and original pinned [#112](https://github.com/Garyu-tt/mojum/pull/112). Existing journal bytes, version identifiers, original peer branches and `main` are never rewritten.

## Why this exists

Two opt-in implementations advertise `local_cell_v2` but have *different wire protocols*: historical v2A uses `mojum.developmental.explorer.local-cell/2` and `motor_policy`, whereas v2B uses `mojum.developmental.local-cell/2` and `developmental_policy`. They cannot mutually replay each other's event hashes. The compatibility [P0 #116](https://github.com/Garyu-tt/mojum/issues/116) remains open; the independent [#123](https://github.com/Garyu-tt/mojum/pull/123)/[#126](https://github.com/Garyu-tt/mojum/pull/126) release gate is rightly red for same-named **direct** cross-open.

An archived v2A history may now be *explicitly re-expressed* under v2B as a **completely new journal**, with different event hashes, if — and only if — its deterministic observations and learned counters reproduce exactly. It must be crystal clear which source created each journal.

## Deliberately restricted acceptance domain

The first implementation supports only **1–1000 contiguous, already verified goal-free synthetic frames**, `sensory_mode=none`, the **exact original built-in Room(4,4,((2,1,0.1,2),)) fixture and start (1.0,2.0)**, source original v2A schema, no GENESIS acquisitions, no added events, no physical actuation. Other geometries **must fail closed**: the old bootstrap loses some numeric int-vs-float type information needed to reproduce the exact world revision hash, even if geometric dimensions look equivalent. The caller supplies **the exact original genome and graph**, not arbitrary substitute input. The source head must be supplied explicitly from a separately trusted record; this tool does not authenticate a head copied from a compromised database. Real hardware, Android background services, MuJoCo, private user data, HMAC cache migration, v3 LRU, life-history generalization and future epoch formats are outside this contract.

No event is simply relabelled or blindly copied. The source v2A journal is opened with SQLite URI `mode=ro` plus `PRAGMA query_only`, replayed using original v2A semantics, and audited against the externally supplied head. A fresh v2B Aquarium is created in a **temporary private directory** on the destination filesystem and advances itself without privileged movement commands. For every tick the two persisted traces must match on *all fields* except explicitly version-dependent policy tags, source labels and learned-snapshot digest. After the last tick, learned action counters, motor spatial memory, the common genome and world/neural checkpoint fields must match. The target is fully hash-audited and cold-replayed a second time.

Only after all checks does the tool publish the verified new SQLite file with an **exclusive no-clobber hardlink**: the destination must not already exist, and a concurrent newly created path is never overwritten. Its contents are file-synced (directory-synced on POSIX). All staging files are cleaned automatically on failed verification. The original is read-only and remains the authoritative old ABI. A concurrent external writer must still be prevented operationally (stop the old writer) because no cross-process exclusive freeze protocol exists yet; the tool double-checks the original count/trusted head before publishing and aborts if it detects advancement.

The output is a **metadata-only JSON report** with the original trusted source head, source event count, new journal head, destination event count, number of compared frames, and the two true schema IDs. It is not a signature or automatically durable provenance database; store the report separately if future migration audit is required.

## Explicit CLI — caller must opt in

```sh
PYTHONPATH=src python -m mojum.migration_v2a_v2b \
  --source /path/to/PRIVATE-original-v2A.sqlite3 \
  --destination /path/to/PRIVATE-new-v2B.sqlite3 \
  --genome examples/genesis_seed.json \
  --graph examples/first_loop.json \
  --expected-source-head <previously-saved-independent-64-hex-hash> \
  --create-new-journal \
  --max-ticks 1000
```

**Do not run on production histories** until independent security/restore review, user-authorized backups and a real recovery exercise exist. No automatic daemon or agent action invokes the transfer. Running it without `--create-new-journal`, with a wrong source head, with an existing destination, a symlink source, mismatched genotype/room, unverified/ambiguous JSON, unsupported sensory/acquired event or a single differing motor frame must fail with *no published destination*.

Resume the new v2B journal with `--exploration-mode goal_free` and
`--developmental-policy local_cell_v2b`. Use the original genome and graph
and the neural mode from the source. The converter writes the original
v2B wire schema. The historical `local_cell_v2` spelling can still resume
an existing v2B journal. It cannot create one.

## CI, independent controls, limitations

Pinned [#112 source SHA](https://github.com/Garyu-tt/mojum/commit/8d2eaf234c09177ae0b92dfef4bb41950529c912) is checked out separately in GitHub Actions. Three CPython versions (3.11, 3.12, 3.13) run real original v2A journal creation then candidate transfer, successful v2B cold replay/resume, source full-row fingerprint immutability, destination head differences, bad head/non-opt-in/budget, wrong seed, tampered duplicate JSON, existing output, injected divergent target policy, CLI confirmation and connection-leak checks. Existing full Aquarium/Motor/Mesh/HMAC/streaming gates remain required.

The transfer matrix also runs on Windows. The verified staging file is
synced using a writable descriptor before exclusive publication. The staged
bytes are left intact. Comparison uses canonical JSON so numeric and boolean
types are preserved. A subprocess from the pinned original [#115 writer](https://github.com/Garyu-tt/mojum/commit/b755da530634c0d6b8866107e7b4e9e325839175)
opens the transferred file at tick 120 and appends tick 121. Its resulting
checkpoint and chain head match a fresh 121-tick run of the same native
runtime. The source v2A file remains byte-identical.

**No automatic promotion:** distinct human/account review and explicit owner acceptance are still needed. Do not close #116: a *new journal* is not two-way wire interchange and the default runtime does not change. No complete organism, real neural brain, MuJoCo, Android device or distributed failover is claimed.
