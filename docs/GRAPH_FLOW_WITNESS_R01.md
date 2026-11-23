# Graph-v2 flow lineage witness R0.1

**Tracking:** #928 (scoped child of #25). **State:** opt-in audit utility,
not a new executable graph, security mechanism, migration, or journal format.

## Purpose

`mojum.graph_flow_witness.attest_flow(v2_graph)` takes a caller-supplied
**canonical graph-v2 Python dict** and returns a deterministic, JSON-safe
information-flow ancestry report. The only executable graph remains
`mechanicus.universal-graph/1`; v2 is an audit-only schema.

Before reporting, it calls `graph_v2.validate_v2` to verify:

- Lossless UTF-8 JSON, exact v1 provenance SHA-256 and original typed ports.
- One supported v2 visibility label per node: PUBLIC, PRIVATE, RESTRICTED.
- Every edge has nondecreasing visibility. This is an **information-flow
  labeling assertion**, not authentication, encryption or access control.
- The canonical v1 shape, all required inputs and acyclic execution graph.

For every node, the witness includes sorted direct incoming typed endpoints,
sorted transitive upstream node IDs (excluding self) and sorted upstream root
IDs (the nodes without incoming edges). It also includes the original v1
and v2 graph SHA-256 identifiers to support deterministic offline comparisons.
An independent `witness_sha256` hashes the canonical JSON of the report
**without its own hash field**. Hashes detect changed bytes; they do not
authenticate an author.

The output copies only IDs, kinds, visibility labels and edge port names.
It does **not** copy node `config`, `notes`, secret values, source code or
event payloads. IDs and port names are **not anonymized**, so only supply
graph descriptions whose metadata is allowed to be shared.

## Offline use

```python
from mojum.graph_v2 import upgrade_v1
from mojum.graph_flow_witness import attest_flow

# v1_graph is a caller-provided, validated PUBLIC_SYNTHETIC graph dictionary.
audit_only_v2 = upgrade_v1(v1_graph, labels={"seed": "PUBLIC"})
witness = attest_flow(audit_only_v2)
assert witness["execution"] == "NOT_SUPPORTED"
```

The code does no file IO, imports no optional dependencies, sends no requests,
opens no SQLite database and does not edit input graphs.

## Bounded resource profile

- Max 256 nodes (canonical graph-v1 limit).
- Max 128 UTF-8 bytes per node ID **for this witness only**.
- Max 8192 total transitive `(node, ancestor)` pairs.
- Max 1 MiB JSON report (checked before adding the checksum).
- No intermediate report is returned on any validation/size failure.
- Computational cost: bounded DAG traversal with per-node set unions.
  Peak memory includes the caller-owned validated v2 object and ancestor
  sets; **this is not an arbitrary-object deserialization sandbox**.
- No claim of timing uniformity across machines. Core stays CPU-first.

## Verification

```bash
PYTHONPATH=src python -m unittest -v tests/test_graph_flow_witness.py
PYTHONPATH=src python -m unittest discover -s tests
```

Full GitHub Actions Aquarium/Motor/Mesh matrices on Python 3.11, 3.12 and
3.13, exact-head review and a second independent approver are required
**before merge**. Runtime execution, declassification, real PRIVATE data,
network telemetry, nested/foreign schema import and enforcement of physical
actions remain explicitly out of scope.
