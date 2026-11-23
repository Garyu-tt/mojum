# CR-SQLite optional collaborative UI metadata spike (R0.1, issue #759)

Parent: #698 (NATS JetStream is a **separate claimed integration**). **Research only.** CR-SQLite is not a Noosphere backend, not a Continuity Mesh journal, not a mesh owner-election mechanism, and not part of the default Mojum environment.

## Implemented

- `src/mojum/crsqlite_collab_spike.py`: standalone Python 3.11+ stdlib-only reference for bounded, versioned, explicitly `PUBLIC_SYNTHETIC` *ephemeral UI layout* events. Accepted keys are `view.vN.x`, `view.vN.y`, `view.vN.collapsed`, with bounded integer/boolean values or tombstones. Strict actor pseudonyms, logical clocks, event IDs and frame byte cap. Replicas accept out-of-order/duplicate delivery, fail atomically on equivocating IDs and refuse other fields/visibility. The oracle uses a deterministic `(counter, actor)` last-write-wins resolution; **this is not asserted to match CR-SQLite internals**.
- `src/mojum/crsqlite_native_spike.py`: optional, **manual**, SHA-256-pinned, version-targeted upstream extension smoke. Copies the trusted native binary to a disposable directory, launches a separate Python interpreter with two fresh `:memory:` SQLite databases, loads extension only there, calls `crsql_as_crr`, exchanges `crsql_changes` from both sides using post-v0.16.1 `site_id = crsql_site_id()` semantics and verifies convergence. It never opens canonical Mojum SQLite journals. **Native library runs as user code, and subprocess isolation is not a security sandbox**.
- Deterministic adversarial tests for partition/reorder/duplicate/replay, tombstone, conflict equivalence, budgets, malformed UTF-8/JSON, privacy rejection and native failure handling. **Native extension not bundled, downloaded or installed**.

## Run (offline, no external packages)

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_crsqlite*.py' -v
PYTHONPATH=src python -m mojum.crsqlite_collab_spike
```

Manual native proof only on an isolated, non-privileged system with independently reviewed binary and known exact SHA-256:

```sh
# Review upstream source/release and dynamic libraries first.
# Use the locally acquired v0.16.3 binary named crsqlite.so/dll/dylib.
PYTHONPATH=src python -m mojum.crsqlite_native_spike \
  --extension /absolute/trusted/crsqlite.so \
  --sha256 <exact-reviewed-64-digit-lowercase-sha256>
```

Never pass an extension from an untrusted download directly. The SHA-256 must come from a separately trusted record, not from the same unverified download. No automatic extension loading, new mandatory Python dependency, network sync or production integration is provided.

## Upstream and compatibility

- CR-SQLite repository: https://github.com/vlcn-io/cr-sqlite
- Documented installation: https://vlcn.io/docs/cr-sqlite/installation
- Documented change merge: https://vlcn.io/docs/cr-sqlite/quickstart
- Pinned **reference release v0.16.3**, https://github.com/vlcn-io/cr-sqlite/releases/tag/v0.16.3 . It is an external **optional binary**, with SHA-256 passed per run; this PR does not declare that a platform-specific release asset has been verified.
- Upstream repository LICENSE is **MIT**, copyright One Law LLC, 2023. Any packaged native library/transitive licenses and platform ABI must be independently checked before acceptance.
- Important break: v0.16.1 changed `crsql_changes.site_id` from nullable to required. Older example queries using `site_id IS NULL` no longer identify local changes in current releases. Use `site_id = crsql_site_id()` for local changes.

## Safety, capacity, remaining work

- **Only synthetic layout metadata**, never graph execution authority, task leasing, identity, remote commands, credentials, PRIVATE Noosphere events, motor state or organism memory. The pseudonymous `r1` etc. fixture actor IDs must not be sourced from real host IDs.
- Reference event budget is 2,048 entries and 256 KiB serialized frame; x/y are strictly bounded integers. It retains *all* events including losing/tombstone operations until discarded. No claim of garbage collection or long-run bounded multi-writer synchronization beyond this cap.
- A CRDT is not a consensus mechanism. Even converged UI layout cannot authorize writes to canonical SQLite, revoke PRIVATE data in replicas, or establish a single writer. Revocation means refusing a forbidden event, **not** deleting data already copied elsewhere.
- Native probe bounds input binary to 64 MiB and subprocess duration to 15 s by default, but it is **not an OS security sandbox** and cannot enforce network denial or CPU/RAM restrictions. Do not run with production secrets or elevated permissions.
- This is **not** production-ready. Still required: genuine v0.16.3 native binary smoke across target OS/SQLite ABI, seeded conflicting-key native two-peer trials, memory/CPU numbers, independently anchored authorization, signature/fencing and replay-safe transport. CR-SQLite must remain separate from NATS, and user data/schema must never be automatically promoted to a CRR.
- Existing optional integration registry remains `planned` and unchanged; independent QA + exact-head CI required before merging.
