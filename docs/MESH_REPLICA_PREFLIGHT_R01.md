# Continuity Mesh R0.1: offline replica metadata preflight (issue #955)

**Advisory evidence comparison only. Never promotes a replica or elects a leader.**

The optional Python 3.11+ stdlib module `mojum.mesh_replica_preflight` compares two
small `mojum.mesh.replica-head/1` metadata claims. It never opens canonical
SQLite files, checkpoint payloads, world state, Noosphere, network connections,
or production hosts. This work is an isolated extension of #12, not a complete
backup, restore, crypto, quorum or failover implementation.

The two manifests have exactly these seven keys:

```json
{
  "schema": "mojum.mesh.replica-head/1",
  "organism_id": "dante.synthetic",
  "node_id": "tower-a",
  "epoch": 5,
  "sequence": 44,
  "journal_head_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "checkpoint_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
}
```

Use the same organism and independently identified source/replica nodes, with
actual independently verified canonical journal head and deterministic
checkpoint payload SHA-256 digests. The example is **synthetic**, not real
custody proof. Manifests are at most 4096 bytes, strictly UTF-8 JSON with no
duplicate/extra keys; epoch and sequence are strictly positive 63-bit native
integers. Hashes are 64 lowercase hex digits. Names are limited to 128 ASCII
identifier characters using internal `._:-` separators. No fallback schema.

Run from the repository root:

```sh
PYTHONPATH=src python -m mojum.mesh_replica_preflight --source source.json --replica replica.json
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_replica_preflight.py' -v
```

Output is one sorted JSON object with `status`, `reason`, and invariant flags
`authorizes_promotion: false`, `requires_external_fencing: true`:

- `match` / exit `0`: exact sequence, epoch, journal head and checkpoint
  digest metadata agree, **not** verified durable or authorized restore.
- `stale` / exit `2`: replica sequence is lower, but ancestry is **unproven**.
- `diverged` / exit `1`: mismatched organism/epoch/hash, same node ID or
  replica ahead of selected reference; investigate outside the tool.
- `invalid` / exit `1`: refused file, malformed JSON/schema or invalid field.

No paths, raw JSON, PRIVATE payload, or IDs are printed on errors. The file
reader rejects symlink path components and nonregular files, and has a hard
byte budget. This is **not** a secure directory custody or race-free reader
against concurrently malicious filesystem writes. The caller must securely
obtain and authenticate manifests and independently verify archived bytes.

## Safety boundary

Matching digests are untrusted *claims* unless provenance and complete
contents are validated by an external trusted implementation. Different node
IDs do not prove two physical hosts or distinct failure domains. A stale
replica may have an unrelated history; only the terminal sequences are
compared. There is no cryptographic signature check or causal chain proof.

In particular, with only two nodes under a network partition, an agreeing
metadata pair **cannot** establish safe new write authority. Require an
independent authenticated witness/quorum or equivalent durable single-writer
fencing and human-audited recovery. Never parse these return codes as a green
light to resume mutation. The analyzer does not implement those mechanisms.
