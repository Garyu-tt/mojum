# Mojum offline checkpoint handoff R0.1 — synthetic fixture bytes

This opt-in CPU-only module gives a **portable on-disk file** for exactly five
already-consistent component byte streams (genome, neural, body, world, memory).
It stacks on the checkpoint envelope + QA integration PR #46, itself stacked
on PR #44. It does **not** yet capture a running Aquarium or wake an organism
in a second process. A restored directory is a quarantined **data payload**,
not execution permission or a live process.

## Reproduce on device A

Use separate files for the five synthetic component bytes. The command does
not infer files or read the running aquarium's private SQLite automatically.

```bash
PYTHONPATH=src python -m mojum.checkpoint_handoff export \
  --organism-id dante-fixture-001 --revision 17 \
  --genome ./genome.bin --neural ./neural.bin --body ./body.bin \
  --world ./world.bin --memory ./memory.bin \
  --output ./dante-transfer.mojum.json
```

Output JSON prints only identity, manifest SHA-256, revision, component hashes
and file location. **No private component bytes** are printed. The archive
itself **contains all component bytes in base64 including private memory**:
keep it on a trusted device or encrypted storage and transfer it manually
(e.g. USB). Never upload it to a public issue or CI artifact.

## Verify and stage on device B

Use the **expected local organism ID**, not the ID read from the archive.
The `--manifest-sha256` must be recorded and verified independently (e.g.
a trusted previously saved fingerprint), **not copied from the untrusted
archive**. Never take the expected revision from an untrusted archive if
anti-rollback is a concern.

```bash
PYTHONPATH=src python -m mojum.checkpoint_handoff verify \
  --archive ./dante-transfer.mojum.json \
  --organism-id dante-fixture-001 --revision 17 \\
  --manifest-sha256 <trusted-64-character-lowercase-hex>

PYTHONPATH=src python -m mojum.checkpoint_handoff restore \
  --archive ./dante-transfer.mojum.json \
  --organism-id dante-fixture-001 --revision 17 \
  --manifest-sha256 <trusted-64-character-lowercase-hex> \
  --directory ./dante-verified-components
```

The destination directory must **not exist**. The module validates canonical
ASCII JSON, five bounded Base64 components, manifest digests, expected identity
and optional expected revision **before** it creates a staging directory.
It writes `genome.bin`, `neural.bin`, `body.bin`, `world.bin`, `memory.bin`
and `manifest.json` to a temporary sibling directory and renames it into
place after writing every file. Files use private permissions on supported
local filesystems. Existing destinations are rejected, not overwritten.

The export file is also staged completely, then published through a hard link
that refuses pre-existing destination names. This requires a local filesystem
supporting hard links. Some removable/network filesystems may not support
these operations; such failures abort rather than silently degrade.

## Limitations / next engineering step

- This is **not a snapshot of the live Aquarium**. The five supplied byte
  streams are caller-controlled fixtures. No agreement protocol connects
  a simultaneous GENESIS/neural/body/world/Noosphere revision.
- The optional trusted SHA-256 anchor detects substitution relative to an
  independently held digest; it is still not a digital signature.
- There is no built-in network transport, device pairing, encryption,
  authentication, signature, trust-on-first-use or authorization. A manifest
  SHA-256 detects corruption against an independently trusted reference;
  it cannot authenticate a maliciously rewritten envelope.
- An expected revision is equality matching, **not durable anti-rollback**.
- The restore parent directory must be trusted and free of competing writers.
  Portable Python directory rename does not provide a no-replace atomic
  guarantee against a malicious local process racing the final destination.
  No power-loss or durable fsync-of-directory recovery claim is made.
- No running neural/physics engine is resumed from these files. In particular,
  motor gate emergency stop must not be cleared by importing state.
- Do not copy or publish actual Noosphere PRIVATE history outside the
  owning device's security policy. This demo does not encrypt it.

**Next:** design a snapshot consistency barrier connected to the actual
Aquarium journal/GENESIS/brain/world and a separately validated *cold*
restore adapter. Then test deterministic replay across two independent local
processes, without claiming distributed consensus or continuous failover.
