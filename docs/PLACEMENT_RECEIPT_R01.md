# Placement receipt R0.1 (independent mesh slice #815)

**Status:** opt-in, offline, stdlib-only, advisory. Parent: [Continuity Mesh #12](https://github.com/Garyu-tt/mojum/issues/12).

The existing `mojum.placement.plan` proposes placement for *stateless* optional jobs.
`mojum.placement_verify.verify_placement` independently checks safety.
`mojum.placement_receipt` adds a portable checksum **of an exact valid
proposal, declared inventory, admitted remote IDs and caller-provided logical
epoch**. It never starts a worker, reads host resources, contacts a peer, stores
private organism state or changes authority.

## Example

```python
from mojum.placement import Assignment, NodeBudget, PlacementPlan, StatelessJob
from mojum.placement_receipt import (
    seal_placement, verify_receipt, receipt_bytes, parse_receipt,
)

jobs = (StatelessJob("paint", 1, 64, ("cpu",)),)
nodes = (NodeBudget("local", 2, 256, ("cpu",)),)
proposal = PlacementPlan((Assignment("paint", "local"),), ())
receipt = seal_placement(
    proposal, jobs, nodes, local_node_id="local", epoch=42,
)
portable = receipt_bytes(receipt)
assert verify_receipt(
    parse_receipt(portable), proposal, jobs, nodes,
    local_node_id="local", expected_epoch=42,
)
```

## Contract

- Exact schema: `mojum.mesh-placement-receipt/1`. Five fields:
  `schema`, `epoch`, `inputs_sha256`, `plan_sha256`,
  `receipt_sha256`. JSON is UTF-8, keys are sorted, wire is capped at
  512 bytes, noncanonical duplicate keys and unknown fields are rejected.
- `epoch` must be a caller-supplied integer in `[0, 2**63-1]`.
  The host must independently enforce monotonic **fencing**. The module
  does **not** allocate epochs or provide anti-replay durable storage.
- The input digest includes *all* job IDs, requested CPU slots/memory MiB,
  priority, required capabilities and stateless flag, plus *all* admitted
  node IDs, caller-declared free budgets, online flags, capability sets,
  local ID and explicitly trusted remote IDs.
- The plan digest covers every assignment and deferred job. List/order
  permutations do not change the digest; changed placements or inventories
  do. Safety is revalidated before hashing and again when verifying.
- The outer digest covers schema/epoch/input digest/plan digest. The tool
  does not contain job payloads or device secrets. Malformed, stale,
  unsafe, inconsistent or incomplete plans fail closed.

## Security boundary and limitations

**A SHA-256 digest is not a digital signature.** A malicious peer can forge a
new receipt and its hashes. The caller must separately authenticate enrollment,
authorizations, budget evidence, source revision and epoch, and verify the exact
trusted snapshot being evaluated. A receipt proves only that *these declared
inputs and the proposal* remain equal to the verified snapshot. A stale
receipt from a prior epoch is rejected only when the caller passes the **new
independently established** `expected_epoch`.

A successfully verified receipt is **not permission to execute**. It grants
no API, network, memory, filesystem, physical-actuation, migration or
authoritative organism privileges. For future real execution, add separate
signed/authenticated lease, fencing, failover and independent resource
measurement contracts with QA. Do not treat this module as distributed
high-availability or a proof of two independent durable copies.

## Tests

```bash
PYTHONPATH=src python -m unittest tests/test_placement_receipt.py
# Or: PYTHONPATH=src python -m unittest discover -s tests -p 'test_placement_receipt.py'
```

Coverage: deterministic portable round-trip, reordering, stale epochs,
changed budgets/capabilities/priority/admission, valid-but-changed plan,
duplicate assignments, forged dataclass constructor bypass, malformed and
duplicate-key JSON, long wire, and no-job plan.
