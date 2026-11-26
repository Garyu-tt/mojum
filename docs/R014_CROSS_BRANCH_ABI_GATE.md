# Mojum R0.14: executable cross-branch SQLite ABI release gate

**State: release blocked pending a unique policy schema.** This separate QA branch does not change either runtime, their histories, `main`, or any deploy. It exists to turn [#116](https://github.com/Garyu-tt/mojum/issues/116) into an auditable test rather than a naming discussion.

## Reproducible, pinned subjects

- [#112](https://github.com/Garyu-tt/mojum/pull/112) at `8d2eaf234c09177ae0b92dfef4bb41950529c912`: `motor_policy=local_cell_v2`, `mojum.developmental.explorer.local-cell/2`.
- [#115](https://github.com/Garyu-tt/mojum/pull/115) at `b755da530634c0d6b8866107e7b4e9e325839175`: `developmental_policy=local_cell_v2`, `mojum.developmental.local-cell/2`.
- Both define historical default **legacy**; neither is released on `main`.

GitHub Actions checks out the immutable exact SHAs into *different* folders. The Python stdlib harness creates only synthetic SQLite files inside a disposable temporary directory, with their own normal source-local genome and graph. It never connects to real personal Noosphere data, services, network nodes, virtual machines, or Android.

Before the cross-journal comparison, the harness runs `git rev-parse --verify HEAD`
inside each supplied source and requires the exact SHA above. A separate
regression creates a disposable real Git checkout and verifies both acceptance
of its exact HEAD and rejection of a wrong expected SHA. This prevents a manual
source-path mistake from being reported under a trusted historical #112/#115
label.

## Test matrix and what its statuses mean

1. **Backward compatible legacy**: birth 4 ticks in A; cold-reopen in B; append one tick in B; reopen in A and check exact hash-head and tick. Repeat B→A. Existing journals must not be silently modified on a zero-tick reopen.
2. **Each new v2**: birth 4 ticks and cold-reopen in its original runtime with exact checkpoint and row digest unchanged.
3. **Foreign new v2**: open an A v2 journal in B using B's own `--developmental-policy local_cell_v2`, and the reverse using A's own `--motor-policy local_cell_v2`. No accidental argparse error can count as a proper rejection. On mismatch: fail closed and compare full SQLite row digest/count/head before and after.
4. Compare the actual **bootstrap policy keys, state and event schemas, snapshot fields, learning outcome schema, and intent source** from real persisted records (never from developer claims).

The *characterization jobs* (Python 3.11, 3.12, 3.13) can be **green** when both v2 foreign histories fail closed: this means the mismatch is correctly detected and the data are preserved. They print `BLOCKED_INCOMPATIBLE_V2_WIRE` with metadata only. **Green characterization is not green release.**

The *release ABI gate* intentionally **fails/red** until both directions reopen and the complete versioned wire identities agree. This is expected for the pinned incompatible branches and must not be treated as a flaky CI. It should turn green only after the release candidate refs and schemas are deliberately reconciled, reviewed and re-pinned.

## Acceptance boundaries

- This is synthetic CPU-first, not Android on-device, neural cognition, MuJoCo, encrypted cross-host replication or general motor-policy superiority.
- An incompatible v2 journal must **never** be auto-migrated or rewritten. If any experimental v2 journals already exist, preserve the originating schema and plan an explicit audited importer. If no persisted consumers exist, select one unique canonical v2 schema/name and reject the other name loudly.
- Make selection explicit, not by agent race or PR merge order. The separate causal integrity fixes [#114](https://github.com/Garyu-tt/mojum/pull/114) and [#118](https://github.com/Garyu-tt/mojum/pull/118) do not solve this collision.
- Independent review, main-head tests and explicit owner release decision remain mandatory. Keep #116, #106, #55 and #51 open.

Manual invocation after sourcing both checkouts:

```sh
python tests/qa_r014_cross_branch_journal.py --source-a sources/policy112 --source-b sources/policy115
python tests/qa_r014_cross_branch_journal.py --source-a sources/policy112 --source-b sources/policy115 --require-compatible
```
