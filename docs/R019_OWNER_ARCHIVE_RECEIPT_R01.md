# R0.19 — owner-HMAC receipt for physically archived Noosphere state

Research-only Draft stacked on [#147](https://github.com/Garyu-tt/mojum/pull/147),
which itself adds explicit point-in-time PRIVATE sealed+tail archives. This
new capability never exports original user data without an explicit call,
touches no production server and never changes old Noosphere histories.

## Trust boundaries

An archive containing its own SHA-256 checksums is self-consistent, but
NOT externally authenticated. To catch an attacker who forges a new
archive manifest, an operator can keep a separately signed receipt
and independently pin that receipt's SHA-256 digest on a trusted device.

`sign_owner_receipt(archive, receipt_file, key_file,
source_events=N, external_expected_source_head=H)`:

1. Re-verifies **every** physical sealed segment + selected live
   tail against a caller-provided trusted source head `H`.
2. Asserts exact owner ID, schema, global source head and event
   count, sealed-prefix head, segment size, sealed/tail counts,
   and whether any archived source event is PRIVATE.
3. Signs only those **metadata** using HMAC-SHA256 and a separately
   provided owner-only 32-byte secret file (0600).
4. Publishes a NEW owner-only 0600 receipt in a pre-existing
   0700 directory **outside** the archived data tree; refuses
   replacing any prior receipt.
5. Returns a receipt SHA-256 digest to pin separately (e.g. on
   an **authorized independent** device). Merely printing and
   re-reading it from the same compromised storage does not
   create an independent trust root.

`verify_owner_receipt(...,
independently_pinned_receipt_sha256=...)` compares the exact
expected digest in constant time, requires canonical JSON and
validates the owner HMAC against the separate key. A stolen
key plus modified archive plus captured external root is outside
this prototype's protection.

`verify_two_archive_copies(A, B, receipt, key, expected_receipt_sha)`
fully verifies both explicit PRIVATE archive directories against
one pinned owner statement, without changing either. Two
directories on one storage volume **are not two independent
failure domains**. It does not start replication, prove a
second physical node, or grant write authority to any remote.

No PRIVATE event payloads, learned memories or secret key bytes
are included in the owner receipt. The receipt contains actor ID,
event counts and SHA hashes, which should still be treated as
potentially sensitive metadata. The archives themselves remain
**plaintext** until an independently approved encryption layer is
deployed.

## Manual offline owner receipt commands

These commands operate only on a **synthetic or separately owner-authorized**
PRIVATE archive. The receipt directory must be a preexisting owner-only 0700
location **outside** the archive tree; the 32-byte HMAC key must be an
independently protected 0600 file. Never commit or share the key.

```bash
PYTHONPATH=src python -m mojum.segment_archive_cli seal-root \
  --archive-dir /private/archive-A \
  --receipt-file /private/receipts/source-000001.json \
  --key-file /private/secret-32bytes.key \
  --source-events 130 --expected-source-head TRUSTED_SOURCE_HEAD

# Pin the returned receipt_sha256 independently before checking it.
PYTHONPATH=src python -m mojum.segment_archive_cli verify-root \
  --receipt-file /private/receipts/source-000001.json \
  --key-file /private/secret-32bytes.key \
  --expected-receipt-sha TRUSTED_RECEIPT_SHA256

PYTHONPATH=src python -m mojum.segment_archive_cli verify-copies \
  --archive-dir-a /private/archive-A --archive-dir-b /private/archive-B \
  --receipt-file /private/receipts/source-000001.json \
  --key-file /private/secret-32bytes.key \
  --expected-receipt-sha TRUSTED_RECEIPT_SHA256
```

The results print receipt metadata only. A successful comparison does
not establish that A and B reside on different physical devices.

## Test gates

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_archive_root_receipt.py -v
```

The synthetic fixture creates 130 PRIVATE source events (128 sealed +
2 tail), a separate 32-byte HMAC key, a receipt outside archive A,
and explicit archive copies A and B. Verifies both copies against
the owner-HMAC receipt and a separately pinned receipt digest. It
also injects wrong keys, tampering, missing copy members,
reusing the same path, overwriting a receipt, trusted SHA mismatch
and privacy of receipt JSON. Genesis-only live-tail source receipt
is tested with 7 PRIVATE events.

CI runs the targeted tests and the underlying sealed+tail
cold recovery suite on Python 3.11, 3.12 and 3.13.

## Not established

- No public-key signature or independent auditor identity;
  HMAC is symmetric and the holder of the key can forge the
  owner's statement.
- No intrinsic anti-rollback without the digest pinned
  **outside** the archive and preferably outside the host.
- No two independently mounted disks/devices or real network
  partition/failover tested; two synthetic directories don't count.
- No scheduler, auto-copy, live-tail incremental replication,
  encrypted backup, key rotation, device proofs or storage
  durability guarantee after power loss.
- Not a "second brain" or evidence about consciousness. Existing
  v4 absolute time and original bounded experimental neural
  semantics remain unchanged.

Next: explicit owner-authorized transfer to physically separate
storage and verifier-held receipt roots; do not auto-merge or
overwrite any canonical writer. See [Issue #141](https://github.com/Garyu-tt/mojum/issues/141).
