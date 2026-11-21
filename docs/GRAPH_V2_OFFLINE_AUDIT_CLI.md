# Offline Universal Graph IR audit and explicit v1 -> v2 export

Related: [issue #25](https://github.com/Garyu-tt/mojum/issues/25),
[graph-v2 compatibility contract](GRAPH_V2_CONTRACT_BRIDGE.md).

The executable graph remains `mechanicus.universal-graph/1` in
`mojum.graph.validate` and `Simulation`. This CLI is **offline and
non-executable**. It cannot execute a v2 graph, grant access to any
PRIVATE information, authenticate an author, or rewrite a journal.

From the repository root (or omit `PYTHONPATH=src` after package install):

```bash
PYTHONPATH=src python -m mojum.graph_audit_cli audit --input examples/first_loop.json
PYTHONPATH=src python -m mojum.graph_audit_cli upgrade --input examples/first_loop.json --output ./first_loop.private.v2.json
PYTHONPATH=src python -m mojum.graph_audit_cli audit --input ./first_loop.private.v2.json
```

`audit` accepts canonical v1 or provenance-verified v2 and prints **only**
schema identifiers, SHA-256 checksums, node/edge counts, and explicit
non-execution/security disclaimers. It does not print graph configurations
or node notes. `upgrade` accepts **only** v1, preserves the original bytes,
validates the canonical typed DAG, and emits a distinct v2 schema to a
**new** operator-selected path. Defaults are `PRIVATE` for every node.

To override node labels, provide a JSON object of node ID to one of
`PUBLIC`, `PRIVATE`, `RESTRICTED`:

```json
{"genome":"PUBLIC","heart":"PRIVATE"}
```

```bash
PYTHONPATH=src python -m mojum.graph_audit_cli upgrade --input examples/first_loop.json --output ./labeled.private.v2.json --labels ./labels.private.json
```

The v2 validator rejects illegal visibility downgrades across typed edges.
These labels are metadata, **not an access-control mechanism**. Do not
declassify source content based on a label, and keep the source graph,
optional labels file, and output under appropriate filesystem permissions.

Safety / limits:

- Reject malformed UTF-8, duplicate JSON keys, NaN/Infinity, unsupported
  schemas and mixed v1/v2 shapes. Size bounds: 256 KiB graph input,
  16 KiB labels, 1 MiB output.
- Stage output with owner-only `0600` permissions, sync the temporary
  content, then use atomic hard-link no-clobber publication; refuse an
  existing destination (including symlinks) without overwriting it.
- The destination parent directory must already exist and be trusted.
  No claim of protection against hostile same-UID directory races,
  network filesystems with unusual link semantics or crash-durable
  distributed storage is made.
- SHA-256 provenance checks detect accidental content mismatch only;
  checksums are neither signatures nor an authorization boundary.
- Neither `audit` nor `upgrade` changes running Simulation, existing
  Noosphere events, checkpoints, journals or the original graph.

Focused tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_graph_audit_cli.py -v
```

Merge requires independent review and the full Python 3.11/3.12/3.13
Aquarium CI. This is one tooling slice toward #25, not its complete
runtime/security acceptance.
