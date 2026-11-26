# Spinal topology ↔ Universal Graph IR compatibility audit R0.1

Related: [#1358](https://github.com/Garyu-tt/mojum/issues/1358), [spinal runtime PR #1389](https://github.com/Garyu-tt/mojum/pull/1389).

## Purpose

The spinal runtime currently defines an explicit `mojum.spinal-graph/1` topology. This small read-only tool reports whether that topology can be represented and executed by the repository's canonical Universal Graph IR contracts. It does not translate, execute, persist, or modify either graph.

Run from the repository root:

```sh
PYTHONPATH=src python tools/spinal_graph_ir_compat.py
PYTHONPATH=src python tools/spinal_graph_ir_compat.py --revision 17
PYTHONPATH=src python -m unittest discover -s tests -p test_spinal_graph_ir_compat.py -v
```

The CLI prints one deterministic JSON report. The tool accepts no paths, opens no files, and has no network, SQLite, hardware, or actuator behavior.

## Verified current boundary

The audit validates the supplied spinal topology using `mojum.spinal_graph.validate_spinal_graph`, then inspects the actual canonical Graph v1 port registry:

- The spinal fixture has 31 logical segments and 60 directed adjacent routes.
- Its bidirectional chain contains 30 feedback pairs.
- Graph v1 (`mechanicus.universal-graph/1`) has a closed registry of six node kinds and requires a DAG. It has no spinal segment kind or spinal sensory/intention/motor port types.
- Graph v2 (`mechanicus.universal-graph/2`) is an audit/migration projection; its execution status is explicitly `NOT_SUPPORTED`.

Consequently the report marks `compatible_for_execution=false` and emits stable blocker codes. It deliberately does not flatten 31 segments into generic nodes, drop reverse edges, or reinterpret v2 as executable: any such conversion would lose feedback/state semantics while appearing successful.

## Contract and limitations

- Input must be a complete valid `mojum.spinal-graph/1` topology. Invalid, partial, duplicate, or malformed routes fail closed.
- The report is advisory compatibility evidence, not an execution plan or security attestation.
- The report does not claim anatomical fidelity, calibrated pathways, neural simulation, or physical control.
- A future executable Graph IR must explicitly version stateful segment containers, typed ports, delayed feedback, scheduling, queue state, checkpoint/replay, and safety boundaries before a semantics-preserving adapter can be implemented.

This audit does not close #1358. Canonical executable integration and durable Aquarium event replay remain separate acceptance work.
