# R0.14 source-visible generated distribution and policy interoperability gate

Status: **Draft research QA only**. No promotion, in-place migration, personal
Noosphere reading, external browser requests, hardware, subjective-valence
signals or physical actuation. Branch is stacked on safety QA PR #114
(currently a child of versioned local-cell PR #112), not on production main.

## Before results: frozen generator and controls

File tests/test_r014_seeded_distribution.py contains a deterministic generator
with explicit seed 0xC0111D3, 24 generated room geometries (dimensions 18 cm
to 1.2 m, 1-4 optional interior obstacles and corners/edges/center/other
starts), three heartbeat sequences per room, and exactly 160 world ticks per
trial. This is 72 paired trials of 3 policies:

1. Existing coarse_v1 Explorer, with its 0.5 m learned zone suppression.
2. New explicitly versioned local_cell_v2, 4 cm own-position cells.
3. Frozen innate motor sequence with NO learning updates.

The generator is declared in source *before inspecting this new test's
results*. It is not independently blinded and becomes a training-visible
development benchmark upon publishing; it is not proof of generalization to
future worlds. The source-visible SHA-256 fixture fingerprint is included in
the report to make replay against this exact set auditable.

The 3 arms get identical initial position, obstacle geometry used solely by
world.advance, virtual tick and observed heartbeat. The policy receives ONLY
typed current Proprioception; no goal, reward, hidden map, future outcome,
coordinate target, pain, distress or motor privilege. Differences from innate
actions can only appear after *that policy* previously measured a collision.

## What is reported (never used as a reward)

For each arm over 72 trials: blocked attempts, non-rest movement attempts,
successful movements, resting time, number of prolonged-rest failures and
aggregate observed positional diversity. Separately count how many trials
experienced *any* measurable collision; distinguish no-contact cases from
actual learning evidence. Compare v2 vs frozen successful motions and report
ALL negative/counterexample case IDs where v2 made fewer successful movements,
more blocked movements or stalled. No selective omission. No result threshold
is added after viewing this suite to declare the favorite winner.

All counters are validated against every deterministic step, with measured
point-room collision, strict snapshot round-trip for updated policies, and
same initial innate intention per arm. Three Python versions are exercised
independently in .github/workflows/r014-seeded-policy-qa.yml.

Reproduction (Python 3.11+, CPU and stdlib only):

    PYTHONPATH=src python -m unittest discover -s tests -p test_r014_seeded_distribution.py -v
    PYTHONPATH=src python tests/test_r014_seeded_distribution.py --report

The report is deliberately aggregate-only: no PRIVATE SQLite, raw frames,
hidden obstacles, personal identifiers, acquired genomes or model snapshots.
No claims of optimality, neural intelligence, MuJoCo simulation, physical
navigation, mobile background hosting or absence of subjective experience.

## Separate blocker: competing v2 implementations

Two independent R0.14 branches currently assign the same human-facing
local_cell_v2 name to *different* persisted contracts.

- PR #112: constructor argument motor_policy="local_cell_v2"; schema
  mojum.developmental.explorer.local-cell/2; version-aware PRIVATE exporter,
  offline viewer lineage, old-v1 fail-closed replay.
- PR #115: argument developmental_policy="local_cell_v2"; schema
  mojum.developmental.local-cell/2; a separately staged canonical replay
  policy from the independent #113 research line.

These **must not** be merged simultaneously as if they were two additive
feature PRs. A version registry/alias plan and explicit persisted-history
compatibility decision is required. A common product label is NOT proof of
wire compatibility, nor should existing v2 SQLite be auto-migrated using
a guessed schema. Keep both alternatives in Draft until independent
review chooses one or a consciously versioned interop path.

The present QA branch tests the #112 contract plus #114's impossible-state
snapshot guard. It does not silently rewrite #115 or pronounce it invalid.

## Acceptance and release boundaries

This finite, source-visible synthetic evaluation can discover defects; it
cannot establish universal capability or treatment of real obstacles.
Independent unknown rooms/device telemetry and separate human review remain
necessary. No main/parent/VM/Android device changes are authorized.
Do not close #106 (rest collapse), #55 (integrated learning), or #51
(organism release). Keep all PRs as review-only unless owner approves.


## First generated-distribution results (after freezing the generator)

Initial green 3.11/3.12/3.13 seeded CI run 44851766401
reported the same fixture hash on the three interpreters:

    3371da882a06b7bb5b03b966887d9378a9656f5ef756de45f7dcbc143e1df20e

This hash was **pinned after** the first run to detect later accidental
generator changes. It was not independently preregistered before running
this experiment. Reusing/tuning against it destroys any pretence that it
is an unseen independent evaluation.

Of 72 simulated 160-tick case/heartbeat combinations, **64** experienced
at least one measured block in at least one of the three controls. Raw
aggregate counts in 11,520 attempted-or-rest ticks per arm:

| Policy | Movement attempts | Blocked | Successful | Rest | Stalled trials |
| --- | ---: | ---: | ---: | ---: | ---: |
| coarse_v1 | 5376 | 238 | 5138 | 6144 | 32 |
| frozen | 9191 | 760 | 8431 | 2329 | 0 |
| local_cell_v2 | 9102 | 376 | 8726 | 2418 | 0 |

Local-cell successful-movement count versus frozen: **44 trials with more,
28 tied, 0 with fewer**, on the published seed and fixed toy-world rules.
The report keeps the list of all worse-case IDs; there were none in this
first sample under its specified indicators. Never infer that no
counterexamples can exist beyond these generated geometries.

These results are evidence of the deterministic synthetic fixture's
behavior, not a validated route-planning ability. Lower collision counts
may reflect different action choices rather than a universally good
control law; the policy still lacks a neural brain, object concepts,
generalization proof, broad physical world and independent reviewers.
