# Optional React Flow / Rete.js Graph IR editor bridge (issue #699)

This is the **first isolated integration slice**, not a React/Rete application.
`mojum.graph_editor_bridge` uses only Python stdlib and the **existing
validated** `mechanicus.universal-graph/2` audit wire. It exports a
framework-shaped JSON envelope for React Flow (`@xyflow/react`) or Rete.js
(`rete`), including typed handles/sockets, stable node/edge IDs, notes,
visibility, configuration, revision and **the complete canonical source**.
It never starts an editor, imports third-party JavaScript, touches a journal,
executes a graph, issues a hot swap, or accesses a running organism.

## Reproduce offline (Python >=3.11, zero dependencies)

First upgrade the bundled PUBLIC demo v1 to the separately versioned v2
audit wire; the fixture's default v2 labels are PRIVATE.

```bash
PYTHONPATH=src python -m mojum.graph_audit_cli upgrade \
  --input examples/first_loop.json --output ./first_loop.v2.private.json
PYTHONPATH=src python -m mojum.graph_editor_cli export \
  --input ./first_loop.v2.private.json --backend reactflow \
  --output ./reactflow.private.json --allow-private
```

`export` prints a metadata-only report with the base revision and **source
SHA-256**. Record that report independently before editing. To validate a
changed editor envelope and emit a detached candidate:

```bash
PYTHONPATH=src python -m mojum.graph_editor_cli verify \
  --input ./reactflow-edited.private.json \
  --expected-revision 0.1.0-fixture \
  --expected-sha256 EXACT_64_HEX_FROM_THE_EXPORT_REPORT \
  --new-revision notes-r02 --output ./candidate.private.json --allow-private
```

Omit `--new-revision` for layout-only changes. Choose `--backend rete`
instead to get Rete-shaped nodes/inputs/outputs/connections.
`verify` never accepts the base anchor from an untrusted edited envelope
without independent comparison. The output is deliberately wrapped as
`mojum.graph-editor-candidate/1` and **cannot** be loaded as an executable
v1 graph; it is for human review, not automatic deployment.

No command replaces an existing path. Created outputs are owner-only 0600,
staged then atomically published with no-clobber on a trusted local directory.
Keep all non-PUBLIC graph envelopes private: `--allow-private` is an explicit
opt-in, not declassification or a network permissions mechanism.

## Shapes and guarded round-trip

* `reactflow`: `nodes[].{id,type,position,data}`,
  `edges[].{id,source,sourceHandle,target,targetHandle}`.
* `rete`: `nodes[].{id,label,position,inputs,outputs,data}`,
  `edges[].{id,source,sourceOutput,target,targetInput}`.
  These are *interchange JSON records*, not live Rete.js runtime objects.
* `data` contains original `kind`, `config`, `notes`,
  `visibility` and typed `ports`. The canonical v2 source and its SHA
  live in the same envelope, preventing accidental lossy conversion.
* Identity, edge endpoints, typed ports, kinds, config, visibility, source
  revision and canonical SHA **cannot** be changed by the editor import.
  Unexpected keys, removed/reordered nodes, novel nested containers, invalid
  numeric layout coordinates, unknown edges and changed structure fail closed.
  Only bounded finite `position` and `notes` can be edited.
* A notes edit requires an explicit **different** revision. The adapter
  projects to verified canonical v1, applies only notes, and performs the
  existing v1 -> v2 migration with fresh provenance SHA and the original
  visibility labels. It returns a **candidate**; no running simulation or
  Graph IR runtime adopts it.
* A separate caller-owned compare-and-swap check must pin both original
  revision and 64-character graph SHA. The checksums are NOT signatures or
  privilege grants. No Graph IR hot-swap API is exposed.
* The current canonical graph v2 does not support recursive container nodes
  or arbitrary plugin node kinds. Rather than silently lose them, this bridge
  rejects them. True nested-container round-trip and a real renderer remain
  **future work** in #25/#699.

## Upstream selection and evaluation

| Candidate | Reference evaluated | License | Cost and integration |
| --- | --- | --- | --- |
| React Flow | `@xyflow/react@13.0.2` (npm listing, 2026-11-23) | MIT | React runtime, d3/zoom/state dependencies; good UI projections. No package added here. |
| Rete.js | `rete@2.0.6` (npm listing, 2026-11-23) | MIT | Framework/plugin approach with separate renderer and socket pipeline; good graph editing candidate. No package added here. |

Sources: https://www.npmjs.com/package/@xyflow/react and
https://www.npmjs.com/package/rete ; licenses from those packages'
metadata / https://github.com/retejs/rete/blob/main/LICENSE .
The versions above are **research pins, not installed or tested
dependencies**. Real installed-bundle MB, mobile FPS, live TypeScript
binding compatibility, keyboard/touch usability and native browser memory
**have not been measured**. Do not call this a React/Rete working editor.

Budget for the stdlib bridge: <=512 KiB JSON per input/envelope, 256 canonical
nodes and 2048 edges (from core validator), depth <=256, position magnitude
<=100,000. Import/export uses bounded in-memory copies; no network, GPU,
model inference or data store.

## Validation and independent acceptance

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_graph_editor_bridge.py' -v
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_graph_editor_cli.py' -v
```

Acceptance: independent Python 3.11/3.12/3.13 CPU CI plus exact-head review
before merge. This branch does not modify `main`, Graph v1 execution, journal
formats, Noosphere events, Adamantium-node, or production services.
