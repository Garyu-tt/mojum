# GENESIS effective-prefix validation cost

`GenomeLedger` validates the inherited parameters plus every acquisition that
is effective at a given tick. An append must remain within the existing gene
bounds at **every effective boundary**, including boundaries in the future.
The canonical schema, event order, revisions and 1,024-event limit are unchanged.

## Grouped boundary sweep

The validator groups temporary references by native integer `valid_from_tick`,
then walks the sorted boundaries once. It starts with the inherited parameters,
applies every delta at a boundary, and checks gene bounds after the entire group.
Tick zero is always included. The first failure is still reported at the earliest
invalid tick and in the existing `GENES` order.

Grouping matters: events recorded as `+120 at tick 10`, `-100 at tick 10`, then
`+50 at tick 5`, with inherited heartbeat BPM 60, are valid. The phenotype is
110 at tick 5 and 130 at tick 10. Checking an intermediate total inside the
tick-10 group would incorrectly reject this history.

The sweep does not reorder the stored acquisition list. Snapshot bytes, revision
hashes and `applied_acquisition_ids` retain their recorded order. `from_snapshot`
still validates each recorded append prefix. A snapshot whose first event is
invalid is rejected even if a later event cancels its delta and its final hash
is internally consistent. CAS, duplicate-ID, recorded-time and quota checks
continue through the unchanged append path.

For N events and K distinct effective boundaries, the helper now performs
O(N + K log K) work with O(N + K) temporary storage. The former implementation
repeated a full N-event scan at every boundary. Full Aquarium acquisition and
cold replay still include unchanged ledger reconstruction and canonical JSON
hashing; this is not a claim that the whole replay has linear cost.

## Reproduced evidence

The deterministic regression counts event-field reads without a wall-clock
assertion. All events are valid zero-delta acquisitions at distinct future ticks.

| Acquisitions | Previous field reads | Grouped field reads |
| ---: | ---: | ---: |
| 64 | 8,384 | 192 |
| 256 | 131,840 | 768 |
| 1,024 | 2,100,224 | 3,072 |

A separate real Aquarium/SQLite experiment on CPython 3.12 used the packaged seed
and graph, native integer zero deltas, one distinct future effective tick per
acquisition, and a cold read-only reopen pinned to the committed journal head.
The complete checkpoint and event count matched before and after every reopen.
Measured against accepted base `5f0000782728c14c2e1f2dc289de3214f0c3de4c`:

| Acquisitions | Previous cold replay | Grouped cold replay |
| ---: | ---: | ---: |
| 32 | 0.101 s | 0.103 s |
| 64 | 0.753 s | 0.562 s |
| 128 | 5.989 s | 4.115 s |
| 256 | 56.487 s | 31.451 s |

At 256 acquisitions, creation through the real public `Aquarium.acquire` path
also decreased from 46.277 s to 29.508 s. These are single-run observations in
one development environment, not latency guarantees or CI thresholds. No
PRIVATE journal contents are included in this report.

## Regression checks

`tests/test_genesis_prefix_sweep.py` exercises the work budget, a brute-force
boundary oracle, simultaneous positive/negative changes, all genes, tick zero,
maximum effective tick, exact revisions and recorded order, atomic rejection,
invalid intermediate snapshot histories, the full 1,024-event snapshot limit,
and real acquisition → anchored reopen → frame continuation → second reopen.

Run the broader GENESIS regression with:

```sh
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -p 'test_genesis*.py' -v
```

The focused work-budget test fails on the previous implementation at all three
sizes. Successful tests preserve ordinary acceptance semantics rather than
skipping replay, checksums or validation to obtain lower runtime.
