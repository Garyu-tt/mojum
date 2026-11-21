# R0.13 slice B — observed toy contact in the canonical Aquarium

This is an **experimental, opt-in, offline CPU-only** extension to the **same organism**, clock and PRIVATE SQLite event journal as the canonical Aquarium R0.13 slice A. The original normal `scripted` mode and `goal_free` **without** toy contact preserve their original bootstrap and event format.

## Real signal flow within this *synthetic fixture*

1. `Aquarium` computes the current GENESIS/heartbeat and the goal-free movement from numeric proprioception, reusing slice A.
2. A separately modeled static `ToyScene` from R0.9 synthesizes a numeric near-field observation **at the current committed world position/tick**. The learner gets only visible, RGB reflectance, 10-cm range bucket, world revision and time. **Experimenter-only toy IDs, coordinates and softness do not enter the learner's sensory input or movement decision.**
3. An innate, bounded probe intention is triggered when an object is near and the numeric RGB cue is *not yet measured*, or once per 7 ticks to resample. After the first **actual synthetic contact**, the next attempt and its current prediction depend on the acquired evidence, rather than a hardcoded material label. A probe is read-only and has no physical actuator.
4. Only when the probe is requested does `ToyScene.probe` return a measured numerical deflection. The unchanged R0.9 `ContactAssociations` learns from this `cue -> measured contact response` pair. Distinct hidden materials under otherwise identical numeric observations lead to **different acquired predictions**, while the observations and motor policy are identical.
5. The Aquarium appends one PRIVATE Noosphere event with heartbeat, developmental action, numeric observation, prediction, probe intention, measured outcome and association-state digest. **Only after a successful SQLite CAS append** are both exploration and sensory models adopted. Reopening recomputes all evidence and checks the exact journal event before replay.

This **does not** create a second Noosphere journal. There is still one `Aquarium.agent_id`, one SQLite DB and one ordered event stream. The sensor never knows hidden toy identity, even though the PRIVATE bootstrap describes synthetic fixture truth for experiment reproducibility.

## Run

From the repository root (Python 3.11+, standard library):

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante_toy_contact.sqlite3 \
  --exploration-mode goal_free --sensory-mode toy_probe \
  --ticks 120
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante_toy_contact.sqlite3 \
  --exploration-mode goal_free --sensory-mode toy_probe \
  --ticks 30
```

Optionally append `--watch` to emit committed per-tick synthetic frames as newline-delimited **PRIVATE** JSON to stdout. Never upload this trace or the SQLite database to public issue comments.

Switching an existing DB from `scripted` or `goal_free` to `toy_probe`, changing the scene/hidden toy parameters, graph, genome, clock or agent is **rejected**. Start a fresh DB for a different experiment. Restarts with the same scene are replay-verified.

## Source reuse and integration dependencies

The bridge starts from the identical QA-hardened R0.9 `src/mojum/sensory_play.py` source in stacked PR #56 (base Git blob `b3d3fb2fd04c13377ff3bde0f6debe818065f837`). Follow-up adversarial QA also removes a **spectator-ID tie-breaker** for equidistant toys so that arbitrary label changes cannot change numeric sensing or measured probe results. This is a narrow fix to the existing sensor, not a forked sensor/probe or association implementation. This new PR is stacked on exact head of R0.13 slice A, PR #59. Review/integrate dependencies **#33 → #56**, and **#59**, before promoting this bridge. Source overlap should be reconciled when merging upstream, not accepted twice or overwritten.

## Tests and limitations

Run `PYTHONPATH=src python -m unittest discover -s tests -v` and inspect the exact PR head's GitHub Actions matrix. New tests cover the single PRIVATE journal, absence of hidden oracle in decisions, prediction and probe changes after contact, controlled different hidden outcomes, CAS failure and after-append crash replay, incompatible modes/scenes, event forgery rejection and CLI resume.

**Nonclaims:** no real RGB camera, 3D object recognition, material identification, MuJoCo force/contact dynamics, actual robot actuator, independently authenticated Noosphere, autonomously sustained smartphone service, general cognitive abilities or verified identity continuity on a second device. Synthetic `brain.placeholder` remains `UNIMPLEMENTED`. The measured deflection is experimental fixture data, not proof that Dante understands a material. Continue to label the release as synthetic alpha only.
