# Spinal Core R0.1: opt-in CPU foundation

This implements a runnable, bounded foundation for [#1358](https://github.com/Garyu-tt/mojum/issues/1358): a 31-segment typed topology, Brain–Spine packets, deterministic scheduling, sensory routing, expiring intentions and checkpoint continuation. It **partially implements #1358**. Canonical Universal Graph IR container/scheduler integration and Aquarium journal integration remain open.

The runtime is a synthetic engineering fixture. Segment names and rates do not establish anatomical fidelity. No neural controller, reflex controller, CPG, physical body, walking, or real actuator is implemented here.

## Run the demonstration

From the repository root with Python 3.11+:

```sh
PYTHONPATH=src python examples/spinal_core_demo.py
PYTHONPATH=src python examples/spinal_core_demo.py --ticks 12000
```

The example uses only the standard library and in-memory fixtures. It writes a JSON summary to stdout and creates no files, journals, network connections or device access. `--ticks` is bounded to 1,200–120,000 logical base ticks.

The demonstration sends one adjacent-segment sensory packet and continuously supplies scalar ascending observations. Cognition remains absent while the runtime steps. It verifies that a bounded mailbox fills and drops its oldest telemetry, a motor intention expires exactly at tick 181, a checkpoint at tick 137 continues identically in a fresh runtime, and e-stop inhibits the next step. Restoring a checkpoint into an already stopped gate preserves that gate's latch.

`fixture` contains deterministic outcomes and the final checkpoint digest. `measurements` reports actual wall time and process CPU time for the primary run, restored continuation, comparisons and safety checks. These host diagnostics include Python/example overhead. They are not measured hardware response latency, guaranteed real-time deadlines, or evidence of physiological performance.

## Explicit schema boundary

Current `mechanicus.universal-graph/1` admits the six bootstrap node kinds and a DAG. Graph v2 is a non-executable audit projection of that graph. Neither currently provides an executable segment container with delayed feedback and checkpointed queues.

This implementation therefore uses distinct schemas and does not add kinds to `graph.PORTS`, reinterpret existing Aquarium graphs, or claim that Graph v2 executes:

| Schema | Purpose |
| --- | --- |
| `mojum.spinal-graph/1` | Fixed segment topology and typed ports |
| `mojum.spinal-sensory/1` | Scalar timestamped sensory packet |
| `mojum.spinal-intention/1` | Revisioned joint intention with an explicit expiry |
| `mojum.spinal-state/1` | Bounded runtime checkpoint |

The implementation is in `mojum.spinal_graph` and `mojum.spinal_runtime`. It is invoked explicitly by its caller and is not wired into `Simulation`, Aquarium, Noosphere or an external physics adapter.

## Topology and routing

`build_spinal_graph(revision=0)` creates 31 logical segments: `C1–C8`, `T1–T12`, `L1–L5`, `S1–S5`, `Co1`. The 30 adjacent pairs have both directed routes, yielding 60 explicit intersegment edges. Every segment declares sensory, intention and intersegment inputs, plus ascending, intersegment and motor outputs.

`validate_spinal_graph(graph)` accepts exactly this fixed inventory and the declared types. It rejects missing or duplicate segments, missing/duplicate/nonadjacent routes, incorrect port types, unsupported schemas and malformed fields. Input list order is normalized. The returned graph owns its own containers.

Runtime dispatch derives its route set from this validated graph. A `SensoryFrame` can address `brain` or one adjacent destination connected by an explicit edge. Local deliveries appear in `StepResult.relayed`; ascending deliveries enter a separate mailbox read by `read_ascending()`. One submission makes at most one hop. There is no automatic broadcast, implicit long-range delivery or recursive forwarding around the bidirectional topology.

The caller provides a complete `joint -> segment` mapping covering exactly the joints in its existing `MotorGate`. A joint can be addressed only through its mapped segment. This mapping is an engineering configuration, not an anatomical calibration.

## API example

```python
from mojum.motor_gate import MotorGate
from mojum.spinal_graph import build_spinal_graph
from mojum.spinal_runtime import SensoryFrame, SpinalIntent, SpinalRuntime

gate = MotorGate({"hip": (-1.0, 1.0)}, revision=4)
runtime = SpinalRuntime(
    gate,
    {"hip": "L2"},
    graph=build_spinal_graph(revision=4),
    queue_capacity=64,
)
runtime.submit_sensory(SensoryFrame("C1", "contact", 0.25, 4, 0, "C2"))
runtime.submit_sensory(SensoryFrame("L2", "joint_angle", 0.1, 4, 0))
runtime.submit_intent(SpinalIntent("L2", "hip", 0.5, 4, 0, 181))

for _ in range(120):
    result = runtime.step()
    # result.relayed and result.motors are immutable synthetic receipts.

observations = runtime.read_ascending()
checkpoint = runtime.snapshot()

replacement_gate = MotorGate({"hip": (-1.0, 1.0)}, revision=4)
replacement = SpinalRuntime(replacement_gate, {"hip": "L2"},
                            graph=build_spinal_graph(revision=4))
replacement.restore(checkpoint)
assert replacement.step() == runtime.step()
```

Constructing packets does not admit them. Submission checks exact native values, finite bounded scalars, identifiers, topology/bindings, revision and logical time. Packets are copied into normalized immutable records. Scalar observations are caller-supplied fixtures; the runtime does not obtain world state or authenticate their origin.

## Logical time and rates

`step()` advances one integer base tick. The initial tick is zero; no phase executes until a step advances time. A phase is due when the new tick is an exact multiple of `base_hz // rate`. Defaults use a 1,200 Hz logical base:

| Phase | Logical rate | Period in base ticks | Implemented behavior |
| --- | ---: | ---: | --- |
| `sensory` | 240 Hz | 5 | Drain admitted sensory ingress into local results or the ascending mailbox |
| `motor` | 120 Hz | 10 | Produce gate-validated targets or target-free rest receipts |
| `cpg` | 100 Hz | 12 | Scheduling signal only |
| `coordination` | 60 Hz | 20 | Scheduling signal only |
| `intention` | 20 Hz | 60 | Consume accepted intentions into active per-joint state |
| `homeostasis` | 5 Hz | 240 | Scheduling signal only |
| `cognition` | 2 Hz | 600 | Scheduling signal only; no inference callback is invoked |

Rates are positive native integers and must divide the configured integer base exactly. All seven phase names are required when replacing `rates`. The base is bounded to 1–12,000 and every rate is at most the base. Floating-point frequency rounding, wall-clock sleeps, threads and backend calls do not drive simulation state.

The due-phase tuple is deterministic. A phase signal does not imply its controller exists: CPG, coordination, homeostasis and cognition implementations belong to subsequent integration tasks. Local reflex circuits also remain outside this slice.

## Intention lifetime and motor safety

`SpinalIntent` carries `segment`, `joint`, `target`, `revision`, `issued_tick` and `expires_tick`. An intention is inadmissible if issued in the future, already expired, attached to a stale revision, or outside its joint binding/limits. Its lifetime must be positive and at most `max_intent_age_ticks` (default 600 base ticks). Later admission does not refresh its origin or deadline.

An intention is expired whenever `now >= expires_tick`. Expiry housekeeping runs on every base tick, independently of the slower intention/motor schedules. Expiry of an active target emits target-free rest receipts immediately, including between motor boundaries. A queued intention that expires before consumption is discarded without becoming active.

Accepted intentions are consumed in submission order; the last accepted one for a joint wins. An older issue tick cannot overwrite a newer queued/active intention. Equal issue ticks retain deterministic submission order.

Every numeric target passes the existing `MotorGate` both at submission and immediately before emission. `MotorReceipt.mode` is `intent`, `rest` or `inhibited`. A rest/inhibited receipt has `target=None`; it grants no numeric target and does not command a physical hold posture. The emitted deadline remains explicit. A downstream adapter must not reuse a target after its expiry.

E-stop or a changed gate revision clears pending/active intentions and produces inhibited, target-free output on the next step. Gate revision change requires a fresh compatible runtime before checkpointing or restoring. Restore never replaces the provided gate or clears its live latch; a latched checkpoint can only latch it further. This is process-local validation, not a security boundary against arbitrary Python code in the same process.

## Bounded queues and stalled cognition

Sensory ingress, intention ingress and ascending telemetry each have `queue_capacity` entries (default 64, supported range 1–256). Active intentions are bounded by the joint inventory, which has at most 64 joints. Ingress overflow raises `SpinalError` without changing the previously accepted queue.

An undrained ascending mailbox drops its oldest frame when full and increments `dropped_ascending`. This explicit telemetry-loss policy lets logical stepping and motor expiry continue while cognition is absent. `read_ascending(limit)` drains at most the admitted limit; it neither advances time nor invokes a backend.

Sensory timestamps cannot be in the future. Their age must not exceed `sensory_max_age_ticks` (default 1,200, configurable up to 120,000); exact equality is admitted. Every step removes overage packets from both ingress and ascending queues and increments `dropped_sensory`. Both cumulative counters are preserved in checkpoints. Local delivered packets leave runtime ownership in the returned immutable result.

The runtime is single-threaded. Callers coordinating asynchronous producers must serialize `submit_*`, `step`, reads and restore operations; this module does not provide cross-thread synchronization or transport authentication.

## Checkpoint and continuation

`snapshot()` returns detached JSON-compatible state. It includes logical tick, ordered queues, active intentions, loss counters, e-stop state and a SHA-256 checksum. A profile digest binds the normalized topology, revision, joint bindings, gate limits, rates, capacities and age limits.

`restore(snapshot)` validates the entire bounded structure, packet routes/types/timestamps, profile, checksum and state ordering before assigning runtime state. It rejects a different configuration and live tick rollback. For cold continuation, create an identically configured runtime and provide its process-local gate, then restore. An already latched live gate remains latched regardless of the checkpoint flag.

Checksums detect accidental modification; a caller capable of editing state can recompute them. They do not authenticate an origin or make this a durable journal. Reproducing outputs after a checkpoint plus identical ordered inputs demonstrates deterministic continuation. Persisted event replay, transactional Aquarium/Noosphere commits and complete organism snapshots remain separate work.

## Acceptance and remaining integration

Focused tests are runnable with:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_spinal_*.py' -v
```

They cover topology, bounded native admission, routing isolation, scheduling, overflow, expiry, safety gates and checkpoint behavior. Run the existing Aquarium/graph/motor suites and repository CI for compatibility before acceptance.

This slice leaves the following parts of #1358/#1357 open: canonical Graph IR container and delayed-feedback scheduler integration; authoritative body/physics observations and motor adapter coupling; durable event replay and Aquarium lifecycle integration; implemented reflex/CPG/coordination controllers; measurements on an actual coupled body and supported deployment hardware. Those capabilities must not be inferred from the fixture's successful CPU run.
