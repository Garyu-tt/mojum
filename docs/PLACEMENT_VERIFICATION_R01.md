# Independent placement safety verifier (R0.1)

This is a **read-only, CPU-only** safety check for optional *stateless* work.
It is not a worker, distributed lock, authentication system, resource monitor,
enrollment, consensus, migration mechanism or an execution authorization.

## Why

`mojum.placement.plan()` constructs a local-first advisory placement. A
future caller could instead receive a candidate plan from another coordinator
or an untrusted input file. The planner's safety assumptions should not become
an unchecked trust boundary in a remote executor.

`mojum.placement_verify.verify_placement()` validates a `PlacementPlan`
against the caller's independently established `StatelessJob`, `NodeBudget`,
local node identity and **explicitly trusted** remote ID set. It returns
`True` for a safe complete plan, otherwise raises `PlacementError`.

The verifier checks that:
- All requested jobs occur exactly once, either assigned or deferred.
- Assignments use real online nodes, and remote assignments are in the
  caller's allowlist.
- The required capability set fits the target node.
- The sum of CPU slots and MiB of allocated memory on every node fits its
  caller-declared **free** budget, after reserving local organism resources.
- Input sets, records and identifiers obey the existing bounded contracts.

A plan that deliberately defers all jobs is safe, so verification is **not**
an optimality or fairness certificate. Neither an ID nor a `True` result
proves authentication, real host availability or available RAM. Do not deploy
an executor that treats this function as a substitute for those controls.

## Offline example

```python
from mojum.placement import NodeBudget, StatelessJob, plan
from mojum.placement_verify import verify_placement

local = NodeBudget("phone", 2, 256, ("cpu",))
jobs = [StatelessJob("test-1", 1, 64, ("cpu",))]
nodes = [local]
candidate = plan(jobs, nodes, local_node_id="phone")
assert verify_placement(candidate, jobs, nodes, local_node_id="phone")
```

Regression suite:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_placement_verify.py' -v
```
