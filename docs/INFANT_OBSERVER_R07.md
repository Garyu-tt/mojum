# Mojum R0.7 — a visible developmental aquarium (synthetic prototype)

**Status: independent staging experiment built from current main.**
Does not depend on, merge, replace, or approve open PRs #26, #27, or #28.

## Product direction: a Tamagotchi, but with inspectable acquired experience

The default object of the game is **not** a perfect pathfinder, a trained
robot, a chatbot role-play script, or a pre-written personality. The intended
product is a voluntary, local-first habitat where someone can watch a unique
digital organism attempt actions, sense the consequences, accumulate memory,
and gradually develop individual behavior.

This stage deliberately **does not assign a goal point** or reward for
moving toward the developer's preferred answer. It offers five small bounded
actions: rest, X+, X-, Y+, Y-. Each half-meter cell stores the number of
attempts and the empirical average movement effectiveness for each action.
The system explores least-tried actions with a deterministic, replayable seed.

This is goal-free **motor babbling and an online consequence predictor**,
not a brain, autonomous cognition, emotional development, human infancy, or
MuJoCo locomotion. It has no vision yet: it observes only a synthetic
heartbeat and its own point-room position. No other organism's memory is used.

## Explainable observer event contract

Every committed private frame contains a `viewer` record showing:

1. **Perception:** precise synthetic heartbeat pulse, room position and
   world revision (no invented eyes, cameras or subjective experience).
2. **Attention:** which available synthetic inputs were sampled.
3. **Experiment:** chosen motion, whether it was novel in that location,
   predicted effectiveness, requested displacement.
4. **Outcome:** whether the fixture blocked movement, actual position,
   measured effectiveness.
5. **Memory:** how many times that action was attempted locally, updated
   learned prediction, prediction error and total updates.
6. **Inference:** a deliberately simple *empirical annotation* based on
   observed movement. Never claim access to private thoughts.
7. **Assessment:** 0..5 score for **that movement's mechanical effect**
   only, not for the organism, character, intelligence or morality.
   Rest has no score: being still isn't a failure.

The observer trace is recomputed from actual sensor and world events and
stored alongside them, not generated as plausible-looking narrative by an
LLM. Reopening the journal deterministically recomputes each frame and
rejects mismatches. This is the eventual data contract for a spectator UI.

## Run

```bash
# Separate database: no migration of existing aquarium journals.
PYTHONPATH=src python -m mojum.infant_play --db ./dante_infant.sqlite3 --ticks 40 --watch

# Continue the same organism's private trace and learned predictions.
PYTHONPATH=src python -m mojum.infant_play --db ./dante_infant.sqlite3 --ticks 40 --watch

# Run full CPU regression.
PYTHONPATH=src python -m unittest discover -s tests -v
```

`--watch` prints local terminal JSONL (one observer event per tick, followed
by a checkpoint summary). This is **not** a network server or automatically
public spectator feed. Redirecting or sharing that output is an explicit
operator action. Eventually the UI should render spatial movement, attention,
action ratings, before/after prediction, memory timeline, and hypotheses
with exact links back to the underlying events.

## Privacy, safety and pacing

- All SQLite journal records are **PRIVATE**. Ordinary dataset export
  excludes them. Local filesystem access still needs OS protection;
  checksums alone do not implement authentication or encryption.
- The CPU-only point-room worker is authoritative for motion. Movement
  magnitude stays at 2 or 4 cm, physical actuators are absent, and a
  forged motor capability or action is rejected.
- Each tick clones learned state; only after successful SQLite CAS append
  does it update memory and world in RAM. Reopening replays deterministically.
- Existing R0.3 aquarium remains untouched. This is an additive alternate
  simulation, so no migration or compatibility promise is hidden.
- Observer consent is required before sharing identity-sensitive memories
  or social interaction history. A future public feed must be redacted and
  capability-scoped; a raw private Noosphere journal is never a public API.

## Developmental curriculum (future, not present)

**Stage A:** sensing internal change and trying actions. **Stage B:**
learning body/world consequences. **Stage C:** observing external objects,
testing hypotheses and learning persistence. **Stage D:** safe social
interaction with an individual local relationship history. **Stage E:**
objectives, curiosity, reflection and emergent preferences, validated across
contexts rather than scripted as preloaded personality.

No requirement to walk perfectly or beat a benchmark at Stage A. The
engineering acceptance criterion is *credible experience and learning*:
observed events, retained individual memory, causal update, and no fabricated
observer narrative. Distinguish the developmental stage from safety-critical
security and backup requirements, which must be mature from day one.

## Speed of development without breaking independent review

PRs #26 -> #27 -> #28 are stacked and should undergo separate QA rather
than accumulate new experimental work in the same dependency chain. This
R0.7 branch is **based on main** so it can be independently accepted.
For five peers, prioritize independent QA of existing PRs, use fresh
branches and explicit claims, and avoid merging own work without a review.
Reusable tests and an observer schema should be carried forward, not
rewritten after every change.
