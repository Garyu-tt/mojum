# Mesh quorum decision evaluator R0.1

## Purpose

`mojum.mesh-quorum-decision/1` is a pure-standard-library, offline decision
calculator for the narrow case of a three-member Continuity Mesh. It evaluates
already-authenticated vote receipts for a proposed next fencing epoch and
returns whether one candidate has a strict majority. It does not run an
election, mint or authenticate votes, persist replay state, promote a writer,
replicate organism state, or perform failover.

## Safety contract

- Membership is exactly three unique canonical node IDs, supplied in sorted
  order. Two-node membership is rejected: a 1/2 partition cannot safely choose
  a new writer without an independent witness or third vote.
- A decision concerns exactly `current_epoch + 1`; skipped or stale votes fail
  closed. Epochs and timestamps are bounded signed-63-bit nonnegative integers
  (the current epoch must be positive).
- A strict majority means at least two of the three distinct members support
  the same candidate. A 1-1 split or three-way split returns `no_majority`.
  Two matching votes plus one opposing vote is a valid 2-of-3 majority.
- Every receipt must target the same organism and next epoch, come from a
  member, name a member candidate, have a unique 128-bit lowercase-hex vote ID,
  and be within a caller-supplied five-minute validity window. Receipt
  cardinality is checked against the eight-field schema before key iteration or
  set allocation, so unexpected-field payloads are rejected without building
  an attacker-sized temporary key set. Duplicate voters,
  duplicate IDs, previously seen IDs, stale, future-dated or expired votes are
  rejected rather than silently ignored.
- The returned SHA-256 digest covers canonical receipt metadata and the result.
  It is a reproducibility checksum, **not** a signature or authentication proof.

## Trust and integration boundary

The caller must independently authenticate each receipt, bind it to the
verified member identity and organism, and supply `seen_vote_ids` from a
trusted durable replay ledger. This module cannot establish that a claimed
receipt was signed, that membership is authoritative, that the clock is
trusted, or that the replay ledger is complete. It has no transport, key
storage, SQLite, Noosphere, network, hardware or automatic retry behavior.

`QuorumDecision.granted` is an advisory result only. A separate owner-controlled
fencing authority must atomically persist the epoch and enforce single-writer
semantics before any state mutation. On partition, minority nodes must stop
conflicting writes; a positive result from this pure function does not override
that gate. In particular, this module does not promise availability under a
two-node split or continuous operation with strict consistency.

## Example

```python
from mojum.mesh_quorum import evaluate_quorum

members = ("node-a", "node-b", "node-c")
# `receipts` must already have passed the application's authenticated
# receipt verification and durable replay checks.
decision = evaluate_quorum(
    organism_id="dante-01",
    members=members,
    current_epoch=4,
    vote_receipts=tuple(receipts),
    now_ns=now_ns,
    seen_vote_ids=tuple(previously_seen_vote_ids),
)
if decision.granted:
    # Still require a separate atomic fencing/CAS operation.
    print(decision.candidate_id, decision.epoch, decision.digest)
```

Run focused tests from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_quorum.py' -v
```
