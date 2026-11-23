# Optional-world adapter failover contract r0.1

## Purpose

`mojum.adapter_failover` defines a small, deterministic policy boundary for an **optional external world adapter**. The home room remains the default/fallback world. A failed external adapter is quarantined and the policy selects the home room. Recovery is never inferred from a later heartbeat: an explicit capability-granted enable event is required.

This is an opt-in policy contract only. It does not start, stop, monitor, restart, or sandbox a process, and it is not wired into the Aquarium runtime.

## Wire contract

Schema: `mojum.optional-world-failover/1`

Events are caller-supplied native dictionaries. All events contain exactly:

- `schema`, `event_id`, `adapter_id`, `tick`, `expected_revision`, `kind`

Allowed kinds and their additional fields:

| Kind | Additional fields | Transition |
| --- | --- | --- |
| `enable` | `capability_granted=true` | disabled/quarantined → active |
| `disable` | none | any → disabled |
| `failure` | allowlisted `error_code` | active → quarantined |
| `healthy` | none | active remains active |

Each accepted event advances the revision exactly once and must have a strictly increasing integer simulation tick and matching expected revision. Booleans are not accepted as integers. IDs are bounded printable ASCII. Unknown fields and non-native mapping/scalar types fail closed.

`selected_world` is `external_adapter` only while mode is `active`. In `disabled` or `quarantined` it is `home_room`. The home room is treated as an always-available logical fallback; this contract does not prove that a runtime process or physical world is alive.

Snapshots include a canonical SHA-256 consistency digest. The digest detects accidental mismatch and is not an authentication signature. `replay()` applies a finite event sequence deterministically.

## Resource and failure boundaries

- Python 3.11+ standard library only; no mandatory third-party dependency.
- Input event sequences are caller-bounded; each individual event is schema-bounded and the module performs constant work per transition.
- No filesystem, network, wall clock, SQLite, Noosphere, graph mutation, device-control signal, hardware access, process supervision, or background thread.
- Adapter errors are represented by allowlisted reason codes only; arbitrary error messages and payloads are never copied into state.
- A quarantined adapter stays quarantined until an explicit `enable` event with `capability_granted=true` arrives. A healthy event cannot recover it implicitly.
- `event_id` deduplication in this r0.1 state is limited to rejecting a repeat of the most recently accepted event; this is not a durable global idempotency ledger.

## Verification

Run the focused deterministic tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_adapter_failover.py' -v
```

Before merge, the exact PR head must pass the repository's required Python 3.11/3.12/3.13 checks and receive independent QA. No production integration or release approval is implied.
