# Graph UI handoff spike (#699)

The authoritative `mechanicus.universal-graph/1` validator deliberately rejects unknown root/node/edge keys. Do not inject React Flow positions or Rete sockets into canonical graph records. Store layout separately in a non-authoritative UI projection keyed by graph ID and node ID. Canonical notes/config remain untouched.

## Candidate comparison (evaluation, not dependency approval)

| Criterion | React Flow | Rete.js |
| --- | --- | --- |
| Primary focus | React-based node/edge rendering and interaction | Node-editor framework with plugin-based processing |
| Canonical executor | Never | Never; disable processing plugins |
| Adapter | Map nodes/edges to display models; preserve canonical payload out of band | Map nodes/sockets/connections; preserve canonical payload out of band |
| Risk | React/browser bundle and layout metadata coupling | Plugin ecosystem and accidental second execution engine |
| Mobile/offline | Bundle local assets; measure on actual target | Bundle local assets; measure on actual target |
| License/version | Pin and verify upstream release license before packaging | Pin and verify upstream release license before packaging |

**Provisional direction:** React Flow for a thin optional React view, subject to a measured offline/mobile build. Neither library is introduced by this spike; no unverified license/version claim is made.

## Contract

`export_handoff(graph)` creates a detached copy with canonical graph ID, base revision and SHA-256 of canonical JSON. `import_handoff(envelope, current_graph)` checks exact base revision and hash, validates the proposed canonical graph and refuses same-revision modifications. A successful return is **only a preflight**: persist via the existing authoritative CAS and never write a live organism from an editor callback.

Limitations: the handoff is not signed, not an authorization token, not a durable journal migration, and does not validate external UI layout or nested containers. Current v1 Graph IR does not support arbitrary nested containers or unknown root fields; these require a separately versioned schema, not silent stripping. The original graph's `notes` can retain structured UI metadata without changing the v1 node shape. Do not expose PRIVATE graph content to a browser without an explicit capability/visibility policy.

## Test command

`PYTHONPATH=src python -m unittest tests.test_graph_ui_handoff -v`

Run exact-head Aquarium/Motor/Mesh Python 3.11–3.13 CI and independent review before merging.
