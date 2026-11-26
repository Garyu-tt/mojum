# Graph editor nested containers R0.1

This is an opt-in, offline, editor-only projection contract. It does not add
nested containers to the canonical executable Universal Graph IR and does not
change graph-v1/v2, Aquarium, SQLite, Noosphere, or runtime execution.

## Model

mojum.graph-editor-nested/1 carries a validated canonical graph-v2 parent
plus up to 64 editor container records. Each record contains a stable editor
ID, one registered parent-node anchor, a separately validated graph-v2 child,
a bounded position, and up to 32 explicit typed portals. Portal direction
must match on both sides (input-to-input or output-to-output), and both
registered port types must be identical. IDs and endpoint mappings are unique.

## Backends and round-trip

export_nested_editor emits stable React Flow-shaped or Rete-shaped container
nodes. The full child graph and portal metadata remain in data; no edge or
node fields are silently dropped. import_nested_editor requires an
independently supplied parent revision and SHA-256 anchor. The source digest
covers the parent graph plus container IDs, child graphs and portal inventory;
only layout positions are excluded. In R0.1 only layout positions may change. Structural edits fail closed and require a future
versioned contract.

## Limits and security

- Python standard library only; no editor package, network, filesystem,
  renderer, or runtime dependency.
- 1 MiB serialized payload, 64 containers, 32 portals per container,
  bounded finite coordinates, and the graph-v2 validator's own node/edge
  limits.
- Child graph and parent graph are validated independently. The helper does
  not prove that a parent node is a runtime container, execute a child graph,
  or enforce permissions. Visibility labels remain metadata.
- execution and hot_swap are explicitly NOT_PERFORMED.
- SHA-256 is a consistency/CAS anchor, not authentication.

Run focused tests with:

PYTHONPATH=src python -m unittest tests.test_graph_editor_nested
