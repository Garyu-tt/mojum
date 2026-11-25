# Spinal conduction R01: bounded synthetic tick-delay queue

Status: additive CPU/stdlib foundation for offline synthetic experiments.

`mojum.spinal_conduction` delays caller-supplied logical signals by exact
integer ticks. It provides a deterministic queue, immutable receipts, and
snapshot/replay. It does not model biological axons, calibrate propagation
speed, run from a wall clock, learn plasticity, or control hardware.

## Profile contract

`compile_conduction_profile()` accepts one exact native dictionary:

```json
{
  "schema": "mojum.spinal-conduction.profile/1",
  "visibility": "PUBLIC_SYNTHETIC",
  "consent": "explicit",
  "revision": 7,
  "max_pending": 64,
  "routes": [
    {"route_id": "left.flexor", "delay_ticks": 2},
    {"route_id": "right.flexor", "delay_ticks": 3}
  ]
}
```

Route identifiers are opaque restricted-ASCII labels. Their names do not imply
anatomical accuracy. Input route order is insignificant; the compiled frozen
profile sorts routes by identifier and binds the complete profile to a
canonical SHA-256 digest.

Limits are 1..64 routes, route delays of 0..4096 ticks, and a caller-selected
pending capacity of 1..1024 signals. Revision and logical ticks are bounded
native integers. Booleans, floats, mapping/list subclasses, duplicate routes,
unknown fields, oversized identifiers, non-public visibility, and implicit
consent fail closed. Exact cardinality is checked before key enumeration so a
large native dictionary with extra fields is rejected before canonicalization.

## Tick transition

`SpinalConductionQueue.step(tick, signals)` accepts only the next contiguous
logical tick and an exact native list containing at most 128 signals. Each
signal has this exact envelope:

```json
{
  "schema": "mojum.spinal-conduction.signal/1",
  "visibility": "PUBLIC_SYNTHETIC",
  "consent": "explicit",
  "profile_revision": 7,
  "event_id": "fixture-event-0001",
  "source_id": "synthetic-fixture",
  "source_revision": 2,
  "route_id": "left.flexor",
  "signal_milli": 125
}
```

Each signal must carry the exact active profile revision, so a record produced
for an older profile cannot be silently reinterpreted with new route delays.
Signal magnitude is an opaque signed integer in `-1000..1000`; it has no
physical unit or calibration claim. The emitted tick is the admitted step tick
and the due tick is `emitted_tick + profile delay`. Delay zero is delivered in
the same step. A delayed signal is delivered exactly once when its due tick is
admitted.

Envelope discriminators are exact native strings; string subclasses and objects
that spoof equality cannot cross the boundary. All validation,
lifetime-overflow checks, duplicate-pending-event checks, and capacity checks
complete before state changes. An invalid batch therefore does
not advance the queue or release already-due work. Pending capacity can be
reused by signals delivered during the same successful step. Event IDs must be
unique while pending; this R01 queue intentionally retains no unbounded global
history after delivery.

Batch order is insignificant. Pending records and deliveries are ordered by
due tick, route ID, event ID, source ID, and source revision. Each frozen
delivery receipt binds the full profile identity, provenance, emitted/delivered
ticks, delay, magnitude, and the fixed `PUBLIC_SYNTHETIC`/explicit-consent
classification to canonical SHA-256. Frames explicitly report
`physical_actuation=false`.

## Resource envelope

For `p` existing pending records and a batch of `b` signals, one successful
step takes `O((p + b) log(p + b))` CPU time for canonical ordering plus linear
validation, receipt hashing, and snapshot hashing. The queue retains at most
1024 records; admission accepts at most 128 more, so the largest transient
sortable record set is 1152. The canonical JSON byte budgets are 64 KiB per
profile, 1 MiB per snapshot, and 8 KiB per delivery receipt. These checks are
hard failures and occur before queue state changes.

## Snapshot and replay

`snapshot()` returns a frozen record containing the schema, full profile
binding, current tick, canonically ordered pending signals, and a state digest.
`from_snapshot()` revalidates exact types, every bound, route/delay agreement,
due-tick arithmetic, canonical ordering, unique pending event IDs, profile
identity, and SHA-256 before restoring any state. A snapshot cannot retain a
signal already due at its recorded tick.

Given the same future signal batches, uninterrupted and restored queues produce
equal frames, receipts, and final snapshots. The digest detects accidental
change; it is not a signature, custody proof, consent mechanism, or persistence
format with atomic durability.

## Non-goals and verification

R01 does not edit or integrate the existing spinal core, CPG, sensory map,
MotorGate, Aquarium, Noosphere, MuJoCo, simulator, network, filesystem, or real
sensor/actuator path. Neural plasticity, learned delays, neuron-population LOD,
biological fidelity, locomotion, and real-time scheduling remain separate work.

Focused verification:

```sh
PYTHONPATH=src python -B -W error::ResourceWarning -m unittest \
  tests.test_spinal_conduction -v
```

The suite covers native-type and exact-shape admission, maximum boundaries,
canonical ordering, delay-zero behavior, atomic rejection, duplicates, queue
saturation and due-slot reuse, tick overflow, long-run bounds, immutable state,
tamper rejection, profile binding, and exact replay continuation.
