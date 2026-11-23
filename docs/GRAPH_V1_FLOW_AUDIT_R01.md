# Canonical v1 graph information-flow audit (R0.1)

This is an **offline, read-only advisory audit** for the executable
`mechanicus.universal-graph/1` contract implemented by `mojum.graph`.

## Why a sidecar

Canonical v1 graph nodes have the stable wire shape
`{id, kind, config, notes}`; changing that shape to add a visibility field
would be a schema migration and could invalidate graph revisions already
pinned in manifests. This tool therefore accepts a separate versioned
visibility sidecar bound to the exact graph `id` and `revision`. It does not
rewrite the graph or change the v1 wire format.

## Sidecar

```json
{
  "schema": "mojum.graph.v1.flow-labels/1",
  "graph_id": "example-graph",
  "graph_revision": "sha256-or-caller-revision",
  "labels": {
    "genesis": "public",
    "brain": "private"
  }
}
```

The `labels` object must contain exactly one entry for every node, with
one of three literal values: `public`, `restricted`, or `private`. Missing,
extra, stale, or unknown entries are rejected. The sidecar is not accepted
for another graph id/revision.

## Rule

Edges are interpreted as directed information flow from `from.node` to
`to.node`. The ordered levels are public < restricted < private. An edge is
reported when its source node has a more restrictive label than its target.
Flow to an equal or more restrictive destination is not flagged. The report
uses edge indices and labels, not node IDs or node payloads.

## Use

```python
from mojum.graph_v1_flow_audit import audit_graph_v1_flow

report = audit_graph_v1_flow(graph_dict, visibility_sidecar)
print(report.to_dict())
```

Invalid canonical graphs, malformed sidecars, stale bindings, non-JSON Python
objects, non-finite values, excess depth/items, and canonical JSON larger than
256 KiB fail closed with `GraphV1FlowAuditError`. Native dict/list cardinality
is checked before scheduling child traversal entries. Preflight counts the
exact compact JSON wire-size envelope, including quoted/escaped strings and
keys, container delimiters, commas, colons and scalar tokens; control-character
expansion is rejected before `JSONEncoder` allocates an escaped chunk. Oversized
native integer tokens are rejected by bit-length estimate before decimal
serialization. The underlying canonical
`mojum.graph.validate()` remains the authority for the executable v1 graph
contract, including endpoint and typed-port checks.

## Security boundary and limitations

- Visibility values are caller-supplied **metadata assertions**. This audit
  cannot authenticate their source or prove they are true.
- A passing result does **not** enforce access control, prevent graph runtime
  execution, sanitize node configuration, or grant permission to disclose data.
- No declassification nodes or exceptions are recognized. A restrictive-to-
  less-restrictive edge is always reported.
- This tool does not inspect PRIVATE Noosphere data, access files/network,
  execute nodes, change a graph, mutate SQLite, or alter runtime policy.
- The SHA-256 values are deterministic content identifiers, **not signatures**.
- The report is static: it cannot prove that runtime behavior matches declared
  graph edges or that plugins honor the declared topology.

## Tests

Run with the repository root on the import path:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_v1_flow_audit.py' -v
```

The suite covers complete/stale sidecars, unknown labels, restrictive flow,
canonical graph rejection, hostile Python objects, deterministic hashing,
and input immutability. This fixture audit does not replace independent
security review or exact-head CI.
