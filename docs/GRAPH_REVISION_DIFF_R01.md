# CPU-first Universal Graph revision diff (R0.1)

Issue: #921 (a narrow, independent part of #25).

This opt-in **read-only** Python API compares two independently validated,
same-schema Universal Graph IR objects and returns a small deterministic
redacted change report. It does not execute, migrate, edit, or persist graphs
or journals. V1 is the only executable graph format; V2 is an audit-only
metadata format and is never interpreted as an executable v1 graph.

## Usage

From a local trusted process with in-memory parsed graph objects:

~~~python
from mojum.graph_revision_diff import compare_revisions

report = compare_revisions(old_graph, new_graph)
assert report["execution"] == "NOT_PERFORMED"
print(report["change_counts"])
~~~

Inputs are JSON-compatible Python dictionaries, **not paths**. The caller is
responsible for any file access, authorized graph retrieval, and secure output
handling. The module does no I/O or network calls. Only exact built-in JSON
types, finite numbers, UTF-8 text, a maximum JSON nesting depth of 128 and
a maximum 256 KiB serialized size *per graph* are admitted. Both graphs must
pass the existing v1/v2 validators before comparison. Graph IDs must match,
and mixed v1/v2 comparison is refused: explicitly upgrade/downgrade offline
and revalidate first.

## Report semantics

- **wire_changed** compares JSON byte-canonicalized inputs. Reordering a node
  or edge list makes it true, even if there is no semantic change.
- **semantic_changed** compares a SHA-256 of canonical graph content with node
  and edge order normalized and **revision excluded**. Config, notes, node
  kinds, edge endpoints and V2 visibility are included, so changes are
  detected without publishing values.
- **revision_changed** compares the explicit revision string.
- **change_counts** reports additions/deletions of nodes and edges, and counts
  changes to kind, config, notes and (for V2 only) visibility on retained
  node IDs. A node replacement/rename can also change the edge counts.
- Output contains SHA-256 fingerprints but **no graph ID, node ID,
  configuration, note, edge endpoint, label text or private journal data**.
  SHA-256 hashes are **not** signatures, permissions, declassification
  checks, or authentication.

Limitations: full-graph hashes can permit comparisons against known
candidate graphs; treat them as potentially sensitive metadata. The
comparison is not an access-control boundary. V2's validated
no-high-to-low edge metadata gate remains a *metadata rule*, not an
enforced runtime privacy policy. For executable graphs, rely on the
canonical v1 validator and graph-pinned replay contracts. Changing graph
contents never rewrites historical checkpoint provenance.

## Tests

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_graph_revision_diff.py' -v
~~~

Covers no-op, order-only changes, revision-only changes, nested config and
notes redaction, JSON type distinctness, V2 visibility, malformed/tampered
V2 origin, mismatched ID/schema and hostile Python structures. Use normal
independent QA/SHIP and pinned-head GitHub Actions before merge.
