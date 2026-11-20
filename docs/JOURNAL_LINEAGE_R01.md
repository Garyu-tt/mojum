# Mojum continuity R0.1 — detecting diverged cold histories

This module answers an important question *before* attempting multi-device
placement: **do these two snapshots contain the same organism's common
history, or have they independently evolved?** It does not choose an owner.

Dependency: PR #58 (freeze), #52 (cold replay), #48 (offline handoff),
#46 (checkpoint validation), #44 (portable bundle). This change adds only a
read-only analyzer, a test file and documentation.

## Observe a real fork using existing experimental Aquarium

First create a synthetic source Aquarium and export a cold archive using
`mojum.cold_aquarium`. Import that archive into two distinct SQLite files
using `restore_cold_aquarium`. Advance each separately using different
`Aquarium.step(dx=...)` values, then export each as a new cold archive.

Both histories begin from the same journal head, but their next committed
Noosphere events contain different movement requests and hash differently.
This is a real **fork of two synthetic local histories**, not necessarily
real-world network split brain.

Audit them offline:

```bash
PYTHONPATH=src python -m mojum.journal_lineage \
  --left-archive ./dante-left.json \
  --right-archive ./dante-right.json \
  --organism-id dante-fixture-001 \
  --left-tick 4 --right-tick 4 \
  --left-manifest-sha256 "$TRUSTED_LEFT_SHA" \
  --right-manifest-sha256 "$TRUSTED_RIGHT_SHA"
```

The optional SHA pins must come through a separate trusted channel. An
internal checksum alone only proves self-consistency of an untrusted archive.

Possible results:

| Classification | Interpretation | Exit code |
| --- | --- | --- |
| `IDENTICAL_HISTORY` | Same exact ordered event-hash sequence | 0 |
| `LEFT_ANCESTOR` | All left events form an exact prefix of right | 0 |
| `RIGHT_ANCESTOR` | All right events form an exact prefix of left | 0 |
| `FORK_DETECTED_STOP` | Shared prefix, different subsequent events | 3 |
| `UNRELATED_ROOT_STOP` | Same apparent organism ID but different bootstrap | 3 |

Malformed archive, mismatched expected ID/tick/manifest digest, changed
event hashes, noncontiguous sequence, duplicate event IDs, duplicate
bootstraps, and noncanonical JSON are rejected before comparison (CLI error).
An archive's journal hash chain is **recomputed independently** using
Noosphere's event canonicalization and original hash contract. The
five-component ledger, shadow neural state, body-disabled sentinel and world
manifest are validated by the existing cold replay component validator.

Output is JSON with event count, tick, hashes, common-prefix length and
status only. No PRIVATE events, sensor frames, motor traces or memory
payloads appear in standard output.

**Every classification returns
`automatic_writer_promotion_allowed: false`.** Even an exact descendant
does not prove its device is authorized to write. A conflict result requires
manual resolution; automated consumers should refuse promotion on exit 3.

## What this does not do

- It does not contact a second device, transfer an archive, grant a lease,
  elect a primary, revoke source writes, fence disconnected hosts, or
  implement distributed consensus.
- It does not authenticate a snapshot's author or protect against two
  colluding devices generating self-consistent false archives. SHA-256
  fingerprints must be independently pinned in a trusted channel.
- It does not automatically merge divergent Noosphere memories. Conflicting
  acquired genes, different experiences and causal order must remain
  distinguishable until an explicit reconciliation design exists.
- Two independently writable copies of one organism can still coexist.
  The purpose of this read-only checker is to **detect** the fork, not to
  prevent it. A durable writer-epoch and split-brain policy are later work.
- The cold snapshot still represents only the CPU synthetic Aquarium and
  its small online pulse predictor. It is not a general cognitive brain,
  MuJoCo body, encrypted backup or real-time migration.
- PRIVATE archive inputs stay on the trusted local device; do not upload
  real memories to public GitHub Actions or issue comments.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_journal_lineage_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Regressions create **two separately restored real SQLite event journals**,
advance them differently, and prove the checker distinguishes shared ancestry
from a fork. They also cover acquired-gene divergence without a new tick,
different bootstraps, corrupted but externally rehashed archive components,
identity/revision/fingerprint mismatch and CLI exit status.

Next: integrate an independently trusted authority-epoch store with
explicit source revocation and consensus/partition policy, rather than
pretending a higher epoch value alone proves permission.
