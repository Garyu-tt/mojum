# Mojum R0.3 — offline checkpoint file transfer (experimental)

This module adds a small, **CPU-only, offline** bridge between an already
validated five-component in-memory checkpoint bundle and a private local file.
It does not create a checkpoint from a running brain, connect two devices,
restore process execution, implement replicated Noosphere, or authenticate an
organism. It is stacked on PR #46 (combined QA of draft #44).

## Example

```python
from mojum.checkpoint_bundle import pack
from mojum.checkpoint_file import export_checkpoint_file, import_checkpoint_file

# These are *example bytes*, not a running organism's real state.
parts = {name: b"fixture" for name in ("genome", "neural", "body", "world", "memory")}
package = pack("dante-fixture", 7, parts)
export_checkpoint_file("./private-dante-r7.mojum.json", package)

# Copy the file out of band, only via a channel appropriate for PRIVATE data.
# The receiving app must supply its independently known identity and revision.
manifest, restored_parts = import_checkpoint_file(
    "./private-dante-r7.mojum.json",
    expected_organism_id="dante-fixture",
    expected_revision=7,
)
assert restored_parts == parts
assert manifest["revision"] == 7
```

## Guarantees and non-guarantees

- Exports canonical ASCII JSON, then atomically **publishes a new filename**
  on local filesystems supporting hard links. It never overwrites an existing
  destination, including a symlink. The temporary file is created with private
  permissions (`0600` on POSIX), flushed and `fsync`ed before publication.
- A failure to publish leaves no intended destination and cleans up the
  temporary sibling. No unsafe non-atomic fallback is attempted on unsupported
  filesystems. The parent directory **must already exist**, and must be trusted.
- Import accepts only a regular file, rejects >30 MB before reading, and caps
  the actual read to 30 MB + 1 byte. Where the OS provides `O_NOFOLLOW`,
  symlink sources are refused. On platforms without it, that guarantee does
  not apply.
- Import requires both an expected organism ID and an exact expected revision,
  *provided by the caller independently of the untrusted file*. It reuses the
  bundle's exact byte checks, canonical JSON, duplicate-key rejection, SHA-256
  manifest and identity/revision gates before returning component bytes.
- No public/private network path is opened. The resulting file is **plaintext**
  and may contain sensitive individual memory. Protect and encrypt it when
  exporting from a trusted local device to untrusted storage or transport.
- This is **not** a cryptographically authenticated origin, an anti-rollback
  mechanism, a full crash-consistent store, a distributed transaction, a true
  replica, a secure restore API or a live migration implementation. A file can
  be internally self-consistent yet attacker-authored. Never apply arbitrary
  restored payloads to actuators, credential stores or other live capabilities.
- File `fsync` and atomic name visibility do not by themselves guarantee
  recovery after arbitrary power loss; directory metadata durability and
  filesystem behavior vary. No guarantee is made for concurrent deletion,
  hostile directory replacement, or exotic network filesystems.

## Targeted test

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_checkpoint_file_r03.py' -v
```

Run the repository-wide suite and independent CI on the final PR head before
integration. The file module does not modify the user's `main` branch.
