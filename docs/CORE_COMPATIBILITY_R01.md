# Compatibility matrix R01

Under #1543, consolidated draft #1552 targets `core/v2`. Compare pinned reference core `bd91fa15da9a3399c6ba5752a0dc30cf3755caa3` against the candidate pull-request merge revision (GitHub Actions `GITHUB_SHA`). Both rows in each lane use the same harness, plugin revision and legacy baseline `074b14c152c8c5e3291e6e56595255386a751af9`.

## Current compatibility scope

`compatibility/candidates.json` and `.github/workflows/core-compatibility.yml` define three GENESIS candidate revisions, all version 0.1.0, none released. Two core revisions × three Python versions (3.11, 3.12, 3.13) × three plugin revisions give 18 compatibility jobs, excluding other PR workflows.

| CI lane | GENESIS revision | Harness shared by reference and candidate rows |
| --- | --- | --- |
| `compatibility` | `5b1ba837969eb8bbafd9714ed46701d23bfec130` | Historical checkout `763030e9dab8ee72c25fb7d8a407fc7bf587db87`, `tests/test_genesis_integration.py` |
| `updated-plugin` | `32db9baef692abad68b641f1f70f7bef98897807` | Candidate merge checkout, `integration_tests/test_genesis_updated.py` |
| `atomic-plugin` | `ebfb2410eda297cecd9805b2a01ed4da0784dd41` | Candidate merge checkout, `integration_tests/test_genesis_atomic.py` |

Each lane verifies runtime, harness, plugin and legacy checkout identities against the corresponding commit pins or candidate merge SHA. The harnesses verify plugin blob IDs and exercise projection/acquisition and forged-state rejection. The updated lane adds missing-config rejection; the atomic lane covers unchanged legacy projection and revision hashes, multi-event history, snapshot roundtrip and failure without tick advance. Historical candidate rows additionally execute core unit regressions. The reference core is a control, not a released core; a failing control requires investigation, and is not alone proof of a candidate regression.

Historical pins are intentional. New plugin HEADs are not automatically covered by these lanes: adding or replacing a revision requires an explicit reviewed compatibility decision. Passing these lanes does not establish compatibility with all plugins or historical releases. No plugin branch updates or vendor copies.

## Checkpoint and executor limits

The compatibility adapter computes pure outputs at an explicit tick and publishes a single (tick, history) state reference after all allocations succeed. The public core step and snapshot wire format are unchanged. This migration fixture is limited to 10,000 ticks, not durable crash recovery; a process interruption after publication may leave a committed step whose response was not delivered. Tuple history copies references at each step; not a scalable long-running persistence design.

## Acceptance boundaries

The workflow and two newer harnesses are PR-authored, not independent acceptance evidence or a trusted merge gate. Before automation, configure protected targets and a trusted required workflow or independently verified CI provenance. Keep reference/harness and plugin pins under reviewed release governance; changes to pins or supported compatibility policy must be explicit and independently reviewed. No main/legacy changes or release promotion.
