# Mesh replication lag evaluator R0.1

This document describes the opt-in, offline measurement helper in
`mojum.mesh_replication_lag`.

## Scope

The evaluator compares two caller-supplied `CheckpointHead` values for one
organism stream and one fencing epoch. It reports:

- sequence lag: source sequence minus replica sequence;
- replica age: caller's evaluation time minus replica commit time;
- source age: caller's evaluation time minus source commit time;
- a threshold result: `current`, `lagging`, or `stale`.

The source and replica records are input assertions. The helper does not fetch,
authenticate, sign, store, or independently verify them.

## Example

```python
from mojum.mesh_replication_lag import CheckpointHead, measure_replication_lag

source = CheckpointHead("dante-main", 4, 120, 8_000, "a" * 64)
replica = CheckpointHead("dante-main", 4, 118, 7_500, "b" * 64)
report = measure_replication_lag(
    source, replica, now_ns=8_200,
    max_sequence_lag=2, max_replica_age_ns=1_000,
)
assert report.status == "current"
assert report.sequence_lag == 2
```

The example uses synthetic values only. The digest shape is validated, but this
module cannot prove that the digest commits to a real checkpoint.

## Admission rules

- Exact `CheckpointHead` instances and exact native scalar types are required.
- Stream ID, epoch, sequence, timestamps and SHA-256 digest are bounded.
- Source and replica must name the same stream and epoch.
- The replica cannot be ahead in sequence or commit timestamp.
- Neither timestamp may be later than caller-supplied `now_ns`.
- Equal sequence numbers with different state digests are rejected as divergent.
- Thresholds are explicit caller policy; age and sequence equality at the limit
  are admitted.

A `stale` result takes precedence over `lagging` when both thresholds are
exceeded. `current` means only that the two measurements are within the
provided thresholds.

## Non-goals and safety

This is an advisory comparison, not a replication protocol or failover system.
It does not prove causal ancestry, independent durable copies, checkpoint
integrity, peer identity, authentication, authorization, quorum, fencing,
availability, or recovery-point guarantees. It does not persist watermarks,
perform network or filesystem I/O, touch SQLite/Noosphere, access PRIVATE data,
or wire into runtime startup. A `current` report never authorizes promotion or
writes.

## Tests

Run the stdlib-only focused suite from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_replication_lag.py' -v
```

Python 3.11+ is the supported project baseline. No new mandatory dependency is
introduced.
