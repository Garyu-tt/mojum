# Opt-in CR-SQLite synthetic metadata spike, issue #756 (part of #698)

**Status: experimental review candidate. NOT a Noosphere/GENESIS/Graph IR backend.** NATS transport on parent issue #698 is separately claimed by another worker.

This spike permits **PUBLIC_SYNTHETIC** metadata only: `label.*`, `layout.*` and `note.*` keys in a `demo_*` workspace. Payloads are printable ASCII, at most 256 characters each; batches are at most 128 operations. Only transient in-memory state is produced; it is forbidden to feed PRIVATE organism records or host/lease identity. Scope comes from independent authorization; public labels alone do not authenticate a peer. The API is an explicit local research harness, not an automatic production integration.

## Offline deterministic boundary

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_crsqlite_collab.py' -v
```

`reduce_fixture(operations)` is a simple deterministic `(counter, replica)` last-writer-wins reference with tombstones. It tolerates reordered/duplicate synthetic inputs and refuses cross-workspace changes, actor/counter equivocation and unknown fields. **It is NOT an emulation or proof of CR-SQLite's native conflict resolution**. It deliberately does not synchronize timestamps, brain/motor actions, PRIVATE data or canonical histories.

## Optional real-native CR-SQLite proof (manual and sandboxed)

Upstream: [vlcn-io/cr-sqlite](https://github.com/vlcn-io/cr-sqlite), pinned candidate **v0.16.3**, release commit prefix `0d62b52` (actual `SELECT crsql_sha()` verified when invoked). Upstream project license: **MIT**; distribution of binaries/transitive dependencies and ABI still require independent review. The extension is **not installed** and is never downloaded by Mojum. No new mandatory packages.

An operator in a disposable offline OS sandbox may explicitly call:

```python
from mojum.crsqlite_collab import native_partition_probe
# left_ops/right_ops are PUBLIC_SYNTHETIC, validated operation lists.
result = native_partition_probe('/absolute/sandbox/crsqlite.so', '<64-hex-pinned-artifact-sha256>', left_ops, right_ops)
assert result['converged']
```

This creates **two separate `:memory:` databases**, loads the exact pre-hashed local shared library with SQLite's extension API, checks upstream `crsql_sha()`, converts only a new `collab_meta` table using `crsql_as_crr`, exchanges bounded `crsql_changes` in both directions, then verifies the projections agree. It never opens the real Noosphere or an existing database; all connection handles are closed and `crsql_finalize()` attempted. The preflight rejects symlinks, world/group-writable artifacts, non-absolute paths, oversize binaries and SHA mismatches. This is not a security sandbox: a native `.so` executes arbitrary code; the inode can change between preflight and SQLite dynamic loading, so run it in a disposable restricted subprocess/container with no private mounts/network. Never point at an untrusted `.so` on a live host.

## Failure modes / evidence still required

- Native CR-SQLite extension not present in the test runner; **real two-replica convergence is not claimed**. Upstream tests/CI must run against exact `v0.16.3` binary on Python 3.11-3.13 with an offline container, pinning full source SHA-256 and downloadable artifact checksum.
- `crsql_changes` has a versioned ABI. Conflicting writes, concurrent deletes, repeated delivery, partitions, schema evolution and rollback need real-engine tests, not only the stdlib reducer.
- Synthetic limits: 128 ops, 256 printable-ASCII characters per value, 32 MiB native extension preflight, 2048 change rows per direction. Measure native memory footprint, per-change CPU and SQLite ABI compatibility; compare to ordinary SQLite and keep native path disabled until accepted.
- No authentication, encryption, identity enrollment, transport, real UI, cross-host synchronization, network listener, source-of-truth replacement, policy mutation, or multiwriter Noosphere. External authorization, independent QA and exact-HEAD CI required prior to merge.

**Rollback**: do not enable the optional module; no migration, persistent file or journal schema is changed. No registry status promotion until formal license and dependency review.
