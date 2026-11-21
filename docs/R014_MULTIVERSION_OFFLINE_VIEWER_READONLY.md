# Offline archive viewer: distinct historical motor policy ABIs

Status: **draft, synthetic-only, read-only presentation**. Does not change
Noosphere SQLite, any writable motor runtime, old journal semantics, or
schema migration. No main or peer branch mutation.

## Why

R0.14 has two incompatible persisted versions using the same human-facing
`local_cell_v2` name. #112 writes v2A with exact frame developmental schema
`mojum.developmental.explorer.local-cell/2` and its learning schema is the
same. #115 writes v2B with distinct frame schema
`mojum.developmental.local-cell/2` and outcome schema
`mojum.developmental.local-cell.outcome/2`; #122 writes v3B with
`mojum.developmental.local-cell/3` and matching outcome/3.

### Immutable display-only registry

| Frame policy schema | Learning outcome schema | Visible label |
| --- | --- | --- |
| mojum.developmental.explorer/1 | same | coarse_v1 · зоны 0,5 м |
| mojum.developmental.explorer.local-cell/2 | same | local_cell_v2A · ячейки 4 см |
| mojum.developmental.local-cell/2 | mojum.developmental.local-cell.outcome/2 | local_cell_v2B · ячейки 4 см |
| mojum.developmental.local-cell/3 | mojum.developmental.local-cell.outcome/3 | local_cell_v3B · ограниченная память |
| no developmental component | not applicable | Внешний сценарий |

The browser accepts the known schemas **only in already-exported local
JSONL**. This table is not a source of permissions to append any SQLite
event, reinterpret the source's old motor model or import foreign source
code. Unknown/new schemas remain rejected until reviewed and tested. A
file with a mismatched learning schema, mixed frame policies, or a new
footer `policy_schema` inconsistent with its frames must fail closed.

## Real original-source acceptance, not a mocked foreign motor

Workflow `.github/workflows/foreign-policy-viewer-smoke.yml` checks out
**two complete, immutable peer source trees**, separately:

- PR #115 at b755da530634c0d6b8866107e7b4e9e325839175 — v2B;
- PR #122 at 8b7b12a1ca3fc89067671c8a63f82c0f0a752e31 — v3B.

In *each source's own Python process*, it starts a fresh disposable
synthetic organism, commits three PRIVATE SQLite frames, closes, cold
reopens with the exact peer policy, compares the entire checkpoint and
frames, and verifies its journal chain against the observed source head.

The smoke then serializes **only that verified synthetic data** as local
JSONL, with the same SHA-256 byte framing and a metadata-only policy
footer as the proposed generic viewer format. The peer branches are
not claimed to ship this particular exporter. It verifies WebCrypto
SHA-256 and exact displayed policy label using **actual Google Chrome**
at both 1280px desktop and emulated 390px mobile width.

Existing real Chrome smoke must also pass for legacy v1 and #112 v2A
and deliberately reject wrong/missing learning schema and unknown
future schemas, even when the in-file frame hash is recomputed.

## Trust and release

SHA-256 of the bytes inside one user-supplied JSONL is not a digital
signature; if the data and its digest are changed together, provenance
cannot be established by this one-file browser. A separate independent
trusted original head or signed checkpoint is needed for authentication.

Viewer support for v2A, v2B and v3B is intentionally *read-only*.
The full original SQLite ABIs remain mutually incompatible in writes.
Issues #116 and #129 are not closed; #123's deliberately red release
gate MUST remain red until a canonical/new version decision is made.
No neural brain, subjective experience, Android deployment, MuJoCo,
distributed continuity, encryption or human independent review is
claimed. No main merge or operator-approved release was performed.
