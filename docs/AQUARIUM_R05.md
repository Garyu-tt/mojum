# Mojum R0.5 — reward-learning point-room policy (stacked staging)

**State: experimental CPU-only implementation, NOT merged into main.**
This PR is stacked on R0.4 PR #26 and must not bypass its independent review.

## What is actually learned

An opt-in `learned_fixture` mode replaces R0.4's hard-coded axis
choice with a **tiny tabular contextual bandit**, not a neural brain.
The only two actions are a bounded X or Y movement **towards a pinned goal**.
The context is the agent's current 0.5 m discretized point-room cell.
Q values begin at zero. After each world observation:

- A blocked movement gets reward **-1**.
- A successful movement gets bounded normalized Manhattan-distance progress
  toward the goal (typically +1).
- The value for the selected cell+axis is updated online using
  `Q <- Q + 0.25*(reward - Q)`.
- Greedy action choice uses learned Q values. A deterministic LCG chooses
  exploratory actions at 12.5% probability. That PRNG is for repeatable
  experiments **only**, not cryptographic entropy.
- The synthetic heartbeat supplies a 2 cm versus 4 cm fixture step.
  It does not supply motivational biology or an artificial emotional state.

The controller reads no obstacle map before moving. It can learn from a
blocked move, but it does **not** implement global exploration, general
navigation, memory retrieval, planning or guaranteed goal attainment.
A separate earlier `PulsePredictor` continues learning to predict
heartbeats in shadow; its weights are **not** used to drive movement.
The graph's brain placeholder still reports `UNIMPLEMENTED`.

## Commit / rollback boundary

On each tick the aquarium clones the policy, samples an intention, validates
it through a strict **world-only fixture gate**, applies it to the existing
deterministic point-room, then computes a reward from the *returned* world
observation. The frame records the exact proposed action, observation and
model-update digest in the PRIVATE SQLite Noosphere journal.

Only after the journal CAS append succeeds does the aquarium adopt the
new policy weights, exploration RNG, world position and neural observer
state. A failed append never mutates the committed in-memory policy.
Reopening replays every frame, recomputes policy decisions and rewards,
and rejects mismatched events. A manifest pins mode, schema, reward
definition, goal and seed. Silent migration between manual, R0.4
`reactive_fixture` and R0.5 `learned_fixture` is forbidden.

The fixture gate denies non-finite or over-budget movement, extra axes,
stale world revisions, changed goals, malformed timestamps, or a non-null
motor command. It is **not a process security boundary** against malicious
Python code in the same process. No MuJoCo, physical joints, robotics
hardware, network, permission tokens or privileged actuators are called.

## Reproduce on an ordinary CPU

Run from the repository checkout (Python standard library only):

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./dante_learned_r05.sqlite3 --ticks 120 --control-mode learned_fixture
PYTHONPATH=src python -m mojum.aquarium --db ./dante_learned_r05.sqlite3 --ticks 60 --control-mode learned_fixture
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python examples/evaluate_point_room_policies.py --ticks 350
```

Use a new SQLite file. An existing database built with manual or R0.4
mode is not automatically converted. The default mode remains manual,
and previously recorded manual/reactive events retain their exact schemas.

## Fixed-room experimental comparison

The optional CPU-only benchmark runs the R0.4 reactive fixture and R0.5
tabular fixture in two fresh SQLite databases with the same room, goal,
GENESIS heartbeat and number of virtual ticks. It reports blocked steps,
distance from goal, first-goal tick, updates and (for R0.5) total reward.
It does **not** claim that the learned controller is superior, that either
controller generalizes to unseen rooms, or that the numbers measure
consciousness. Results must be interpreted before changing architecture.

## Next independent acceptance

- Show a learning trace where a blocked X motion changes Q(X) from 0 to
  negative, followed by a greedy Y choice. Show this without looking at
  future obstacle geometry.
- Verify deterministic update/PRNG state after process restart and on
  independent replay; SQLite CAS failures must not change RAM policy.
- Reject malicious proposals before a journal commit; reject unsafe
  snapshot shapes and unexpected control-mode changes.
- Track first-goal arrival, blocked-move ratio and episode time in
  controlled experiments **against the R0.4 baseline**, rather than
  declaring training useful merely because Q changes.
- Remain explicit that the replay limit is still 10,000 events and
  that SQLite journal checksums do not provide encryption, signatures
  or reliable two-node replication.

Next research: an independently testable recurrent policy that can
propose actions from richer sensory streams, still behind the
world/motor safety gate, before considering a real MuJoCo joint worker.
