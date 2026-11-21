# R0.14 held-out QA matrix: goal-free motor exploration (research-only)

Scope: **QA only**, stacked on draft research PR #104. This does not modify the
canonical Aquarium, its developmental policy schema, persisted SQLite history,
physical actuators or the Noosphere.

## Frozen experimental protocol

The predeclared fixtures in `tests/test_neutral_probe_heldout.py` include:
- All four corners of a 16 cm room (the published tiny-corner negative control);
- A small center, left/right sides of a wall, a narrow passage, a wide edge;
- An open 2 m room and two starts in an obstacle-filled 1.2 m room.

Each scene is evaluated with the **same** 120-tick sequence for every arm,
under three predeclared pulse regimes: alternating, all-true and a phase-seven
pattern. Total **12 scenes × 3 pulse regimes = 36 paired comparisons**.
Controls in each comparison:
1. Canonical R0.13 `Explorer` with 0.5 m zones and permanent blocked-action inhibition;
2. Frozen innate pulse-rotating motor control (never updates);
3. Research-only 4 cm `LocalCellPlay` with measured local blocked outcomes.

Only the current typed `Proprioception` (tick, own x/y, revision and heartbeat)
is passed into a learning policy. The ground-truth room geometry is passed to
the world advancement function **not to the policy**. Additional independent
QA spies on the exact type/fields received by both adaptive policies.
First action and pulse streams match. Action divergence must follow a
previous measured collision; oracle/pre-scripted differences fail.

## Metrics and interpretation

For each run: blocked attempts, attempted movements, successful movements,
rest actions, longest rest streak, number of distinct synthetic positions and
an explicit stall flag. All reported metrics are re-derived from the complete
trace in tests. Frozen/no-learning control must remain untrained.

The report intentionally retains **all** counterexamples, including cases
where the new local-cell policy moves less successfully than the frozen arm.
Collision reduction alone is **never** accepted as a win because a nearly
motionless policy can suppress collisions by doing nothing. Neither coverage
nor successful motion is a reward or optimization signal fed into the agent.

Known published tiny-ne/alternating case must still expose the coarse-zone
collapse. This is a regression truthfulness gate, not a claim that 4 cm cells
are optimal or generalize.

## Reproduce on CPU (Python 3.11+; no dependencies)

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_neutral_probe_heldout.py -v
PYTHONPATH=src python tests/test_neutral_probe_heldout.py --report
```

The second command prints only an aggregate deterministic JSON matrix; no
PRIVATE journal, hidden toy identity, model snapshots or trajectories. CI runs
both commands for Python 3.11, 3.12 and 3.13.

## Decisions intentionally deferred

This is an offline synthetic experiment, **not** a brain, navigation system,
emotional state, suffering-proof agent, device test, authenticated log, or
physical motor implementation. For #106's production fix: version any changed
learned-policy semantics and preserve deterministic replay of old R0.13
journals; compare broader held-out performance and have a separate reviewer
assess the method. PR #104, R0.13 #95, P0 #55 and release acceptance #51
must not be merged or closed automatically.

## Explicitly post-hoc forced-contact challenge (not held-out evidence)

Initial 36-case CI matrix showed only **13/36 cases with any measured block**;
23 cases made no contact at all. To test the boundary rather than mislabel
quiet room fixtures as learned navigation, a **separate** file
`tests/test_neutral_probe_contact_challenge.py` now fixes 5 initial
positions inside 4 cm of a wall or room edge, with first pulse TRUE and the
same three pulse streams. This produces **15 intentionally contact-rich**
challenges, after looking at the initial report; they are *not* counted
as held-out generalization. Every arm's first x+ intent must encounter an
actual measured block before any policy update. The challenge reports
all arm motion and stalling outcomes as aggregate diagnostics, without
PRIVATE events or trace publication.

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_neutral_probe_contact_challenge.py -v
PYTHONPATH=src python tests/test_neutral_probe_contact_challenge.py --report
```

Neither the 36-case matrix nor the new 15 stress fixtures establish real
navigation, safe unbounded learning, a full neural brain, hardware actuation,
or absence of subjective experience. Maintain the frozen control and explicit
policy-schema/versioned replay gate before modifying the canonical organism.
