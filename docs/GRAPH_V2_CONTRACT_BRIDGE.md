# Universal Graph IR v2: opt-in non-executable compatibility bridge

Related: issue #25 (https://github.com/Garyu-tt/mojum/issues/25).

The only runtime accepted by mojum.graph.validate and Simulation remains
mechanicus.universal-graph/1, with nodes {id,kind,config,notes} and edges
{from:{node,port},to:{node,port}}. The unrelated PR #2 validator expects
nodes {id,visibility,inputs,outputs} and flat edge endpoints. It must never
be accepted under the same v1 schema: canonical executable kinds cannot
be safely inferred from that incompatible wire.

mojum.graph_v2 introduces an opt-in, separate wire name:
mechanicus.universal-graph/2. upgrade_v1 copies an existing valid v1
fixture, adds visibility (default PRIVATE) and read-only port declarations
derived from the registered node kind, and records the canonical v1
graph SHA-256. The source graph, existing event journal, checkpoints and
Simulation runtime are not changed. validate_v2 reconstructs and validates
the original v1 typed DAG, checks registered port declarations, verifies
the origin checksum and rejects an information-flow downgrade across
any edge (RESTRICTED > PRIVATE > PUBLIC). There is no implicit runtime
fallback, declassification, kind inference or execute-on-import.

downgrade_v2 is the explicit reverse migration for audit and offline
interop. It first runs the full validate_v2 checks, then drops only the
additive v2 metadata and returns the canonical v1 graph whose SHA-256 must
equal the recorded origin. upgrade_v1 -> downgrade_v2 is byte-for-byte
lossless; tampered configuration, forged visibility or non-additive fields
fail closed instead of being silently discarded.

IMPORTANT: Origin SHA-256 is not an authenticated signature. Visibility
labels are information-flow metadata, not enforceable file permissions
or plugin access control. This experimental adapter cannot execute v2
graphs, export PRIVATE data or grant access to memory. Real enforcement
and a versioned v2 executor are future work.

Local focused check (run from repo root):

    PYTHONPATH=src python -m unittest discover -s tests -p test_graph_v2.py -v
    PYTHONPATH=src python -m unittest discover -s tests -p test_graph_v2_downgrade.py -v

Promotion requires independent review and Python 3.11-3.13 CI. This
is one safe migration/audit slice, NOT full closure of issue #25.
