# Constructor SQLite cleanup on the R0.14 v2 stack

This is review-only integration QA. Main-target PR #124 fixes one
confirmed Python 3.13 SQLite unclosed-database failure cause in
LocalNoosphere constructor; this child branch verifies the same source
alongside the much larger Draft R0.14 Aquarium and 72-case seeded QA
chain (#119 → #114 → #112 → #95).

## Auditable provenance
- The entire source src/mojum/noosphere.py is byte-identical to PR #124
  exact head e37249f57eea19021468342ec7f024a062637132.
- tests/test_noosphere_init_close.py, tests/audit_sqlite_resource_warnings.py,
  and .github/workflows/aquarium-integration-ci.yml are copied from that
  same pinned source PR, not reimplemented.
- Base #119 was pinned at bcab9612b7d519b48a6b9c929cb4dd6ea85547fa
  and its original Noosphere file was byte-identical to main before port.
- No changes to R0.14 policy, genome, schema, existing private journals,
  learning semantics, real devices or deployed infrastructure.

## Exact test contract
- Full Aquarium regression on CPython 3.11, 3.12 and 3.13.
- A second full test run on Python 3.13 under ResourceWarning capture and
  forced garbage collection. Any unclosed database warning fails the job,
  rather than being ignored because all assertions passed.
- Real SQLite constructor failed-initialization tests preserve references
  and verify immediate synchronous closure for wrong owner, wrong schema
  and forced setup failure, in addition to normal context-manager closure.
- Independent Motor, Mesh and 72-case seeded synthetic QA jobs remain
  required. Chrome viewer CI is an additional check when triggered.

## Nonclaims and merge order
Keep the stable PR #124 as the primary source of the fix and have it
independently reviewed. This branch is a compatibility rehearsal, not
an instruction to merge stacked R0.14 into main. If #124 is approved,
forward-port it once into the eventual integrated candidate and recheck
exact source and CI. Main, all upstream PRs and private histories remain
untouched. No authenticated/distributed memory, physical Android device,
MuJoCo, neural brain or global freedom from leaks is claimed.

Issue #121 still needs independent review; issue #116's two incompatible
v2 wire schemas remain blocked regardless of this cleanup.
