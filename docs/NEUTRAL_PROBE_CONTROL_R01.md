# Neutral-probe experimental control R0.1

This is a **bounded, standalone research fixture**, deliberately separate
from the canonical persistent Dante Aquarium. It is not a second brain, a
consciousness test, a physical rabbit, a real MuJoCo body, or a full organism.

## Purpose

Provide a minimal candidate with the *existing* canonical world-room,
proprioception and pulse-driven, goal-free Explorer, and compare it with a
frozen innate-rotation negative control. Both arms start at the same position
and receive identical changing synthetic heartbeat bits. Only the adaptive
arm uses measured blocked/unblocked outcomes to adjust future selection.
The frozen arm takes its preprogrammed sequence without updates.

Neither arm has an explicit pain, distress, aversive-valence, reward,
punishment, biological homeostasis or goals-to-survive representation.
**This cannot establish that any future model is incapable of subjective
experience or suffering.** For now, it constrains the available data and
learning interfaces in this *specific* sandbox.

## Reproduction

On a CPython 3.11+ installation with the repository checked out:

```bash
PYTHONPATH=src python -m mojum.neutral_probe_trial --ticks 120 --mode compare
PYTHONPATH=src python -m mojum.neutral_probe_trial --ticks 120 --mode adaptive
PYTHONPATH=src python -m unittest discover -s tests -p test_neutral_probe_trial.py -v
```

The CLI prints small aggregate counts, not the bounded private per-tick trace.
Library users can call `compare(room=Room(...), start=(x,y), pulses=(...))`
to examine deterministic traces and learned counters in memory. No files
are created, no SQLite journals opened, no network used and no actuators
or privileged motor gates are available through this module.

## What the comparison does and does not show

- A measured difference in blocked counts shows that replayable contact
  feedback changed *this* simple synthetic policy under *this* fixture.
  **Blocked counts alone are invalid as success**: a policy that gives up
  moving can trivially avoid further contacts. The trial therefore records
  rest count, actual movement attempts, successful movements, approximately
  distinct positions and longest idle streak, and warns on prolonged
  action collapse. With the tiny default corner room, the current
  coarse-zone Explorer can enter a rest-collapse regime; this is an
  exposed baseline limitation, not claimed learning progress.
- Equal results do not prove learning failed; inspect action divergence,
  causal trace and test other starting positions/seeds.
- This is **not** a randomized, blinded biological study: both policy arms
  are deterministic and their motion changes later experiences.
- Synthetic motor inhibition is a purely mechanical prediction/safety
  rule. It must not be rebranded as pain or negative emotion.
- Before integrating a new neural backend, repeat this comparison with
  frozen weights, unseen layouts, locked test protocols and independent
  reviewer assessment. Do not copy a sandbox's state to a production agent.

## Integration boundary

This branch intentionally adds only three paths: module, tests and this
research note. It is stacked on the direct-main R0.13 candidate #95 and
does not alter #95, #97–#101 or main. It does not pretend that the
`brain.placeholder` already implements cognition.

No automatic merge or release approval is implied.
