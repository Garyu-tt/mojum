# R0.14: read-only journal format registry and integrity probe

**Draft QA only.** This helper does not migrate history, grant motor privileges, choose the canonical policy, read user-private databases in CI, or resolve [P0 #116](https://github.com/Garyu-tt/mojum/issues/116).

## Motivation

The same public name `local_cell_v2` means two *different* persisted formats in [#112](https://github.com/Garyu-tt/mojum/pull/112) and [#115](https://github.com/Garyu-tt/mojum/pull/115). Their bootstrap manifest keys, intent sources, outcome schemas and snapshots differ. A future maintenance tool must identify the **journal's actual wire format**, not guess the version from the requested name or silently attach the wrong controller.

[PR #122](https://github.com/Garyu-tt/mojum/pull/122) independently adds a distinct local-cell v3 LRU journal, and this QA scanner keeps it separate from both experimental v2 formats.

## Explicit allowlist — not a migration table

| Scanner result | Persisted format | Allowed action |
| --- | --- | --- |
| `legacy_scripted` | R0.13 scripted Aquarium bootstrap, no developmental policy | Identify only |
| `legacy_coarse_v1` | R0.13 Explorer/1, no extra policy keys | Identify only |
| `local_cell_v2_a_unreleased` | #112 `mojum.developmental.explorer.local-cell/2` + `motor_policy` and `motor_policy_schema` | Identify only; do NOT alias to #115 |
| `local_cell_v2_b_unreleased` | #115 `mojum.developmental.local-cell/2` + `developmental_policy` | Identify only; do NOT alias to #112 |
| `local_cell_v3_b_unreleased` | #122 `mojum.developmental.local-cell/3` + `developmental_policy` | Identify only, no implicit v2 migration |
| Anything else | Unknown, mixed keys, future version, unrecognized source | **Reject** |

The scanner uses Python stdlib SQLite in read-only query mode, walks the **full append-only event chain** in a single read transaction, rejects duplicate/alternate JSON encodings and wrong indexed columns, checks the stored hash links, enforces the exact bootstrap policy identity on every developmental frame, and can optionally compare a separately obtained expected head. Without an independently trusted head, the chain is **not authenticated** against a malicious local DB owner capable of rewriting all events and hashes.

The public JSON report includes only a format label, head, counts and a scope warning — **never seed/genome, event payloads, private trace or agent ID**. No SQLite migration or rewrite API exists. Probing a real journal must still follow user consent and local filesystem permissions. SQLite may use standard WAL auxiliary files when opening a WAL database; this test uses only closed disposable fixtures.

## Reproducible cross-branch CI

The existing [#123](https://github.com/Garyu-tt/mojum/pull/123) harness is extended by the QA-only inspector. Pinned GitHub source checkouts:

- #112: `8d2eaf234c09177ae0b92dfef4bb41950529c912`
- #115: `b755da530634c0d6b8866107e7b4e9e325839175`
- #122: `8b7b12a1ca3fc89067671c8a63f82c0f0a752e31`

The CI creates **temporary, synthetic SQLite events only**. The runner verifies legacy interoperability, distinct v2 self-reopen, distinct v3 self-reopen, exact JSON+full hash-chain+external-head checks on actual journals, rejection of duplicate-key/noncanonical tampered copies, and rejection of mixed/unknown ABI manifests. Only the v2 cross-reopen release gate remains **deliberately red** until #116 has an approved and tested protocol. The 3 diagnostic matrix jobs must not be mistaken for release approval.

## Future integration contract

A consumer may use the scanner as a **preflight** to determine an exact-format handler with explicit permission. It may not reinterpret a journal under another `local_cell_v2`, rewrite existing PRIVATE events, or change default legacy replay. A cross-version migration, if authorized, must be a separate versioned process with preserved source journal, cryptographically grounded provenance where applicable, independent tests, rollback and security review. Real Android, MuJoCo, neural brain, GPU and distributed failover are not assessed here.
