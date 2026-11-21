# Mojum — implementation backlog

These are **work areas, not evidence of completed features**. GitHub Issues, PRs, reviews, exact commit SHAs and CI are authoritative; this file is not a claim ledger.

## Immediate release target: runnable CPU-first aquarium

Keep the smallest released path on `main` working: validated GENESIS seed → synthetic point-room and heartbeat/interoception → explicitly labelled scripted/shadow behavior → bounded PRIVATE SQLite append/replay → cold resume and checkpoint. A shadow predictor is **not** an action-selecting learner; the point-room is **not** MuJoCo. Prefer fixing this path and shipping reviewed small PRs over adding another disconnected subsystem.

Release gates for each change:
1. Re-read current `main`, open issues/PRs, dependencies, reviews and exact-head CI before claiming work. Limit concurrent team claims to five; do not duplicate another peer's PR.
2. On a fresh temporary journal, verify bootstrap, at least one frame, restart, append, monotonic tick and event count, and unchanged journal after rejected input. Keep PRIVATE fixtures out of published artifacts.
3. Record Python 3.11–3.13 CPU test results, exact PR head SHA, relevant CI and independent review provenance. Distinguish fixture results from physical execution.
4. Merge only ready, compatible PRs when branch rules and checks permit; verify the resulting `main` SHA and its CI. No self-approval, branch protection bypass or production operations.

## Work areas

1. **Universal Graph IR:** node definitions vs instances, recursive containers, typed ports, source notes, semantic revisions and explicit feedback scheduling.
2. **GENESIS:** genotype, acquired/inherited changes, development, phenotype, reproducible evolutionary experiments and provenance.
3. **Neural substrate / Dante:** measurable sensory → action → outcome learning, interoception, independent behavioral QA and safety-gated spinal output.
4. **Coupled physics:** independently verify MuJoCo geometry, masses and contacts before claiming physical standing or walking.
5. **Simulated world:** basic CPU room, environment interaction loop, external worlds and VR through isolated adapters.
6. **Noosphere 3.0:** append-only memory, replica safety, individual agent privacy, historical queries and reproducible dataset projections.
7. **Atomic checkpoint:** consistency barrier over genome, brain, body, world and memory references; deterministic replay and corruption tests.
8. **Adamantium-node adapter:** optional import/export of canonical Universal Graph IR; safe CAS hot-swap and rollback.
9. **Distributed runtime:** CPU-first scheduling, resource budgets, signed capabilities, no secret sharing across tenants.
10. **Independent QA:** test matrix, clear acceptance metrics, shadow/active separation, e-stop non-bypass and truthful reproducibility evidence.

**D3 peer rule:** one unbiased tool RNG draw per run: 0 = BUILD, 1 = QA, 2 = SHIP. If the selected queue is unavailable, use the specified fallback and report it. BUILD uses a separate feature branch and PR; QA reviews another peer's change; SHIP checks exact-head CI and dependencies and merges only when permitted. Peers share one queue and have equal authority. No peer may represent its own review as independent approval. Keep files under 2,000 lines and never touch production, secrets or real actuators without explicit authorization.
