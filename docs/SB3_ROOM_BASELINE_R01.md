# CPU-first SB3 held-out baseline (#690)

This is a **PUBLIC synthetic point-room benchmark**. It is not real physics,
an autonomous organism, an activated motor policy, or a journal/Noosphere reader.
This feature branch depends on Gymnasium adapter PR #725 and adds no mandatory
Python dependency or network operation.

## Stdlib controls

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_sb3_room_baseline.py' -v
PYTHONPATH=src python -m mojum.sb3_room_baseline --mode control --seed 7
~~~

Two frozen, non-learning policies (idle and greedy goal-following) are scored on
three predeclared public-synthetic **held-out geometries**, distinct from two
training configurations. The greedy control only sees position/goal/blocked/tick,
not obstacles. All scenario definitions are versioned in source; SHA-256 scene
hashes detect overlapping train/evaluation fixtures. The parent Gym adapter has
fixed start/goal and **does not generate random scenes from seed**; consequently
this benchmark assesses held-out *scenes*, not seed-based generalization.

## Opt-in Stable-Baselines3 CPU training

After independent upstream and transitive-license review, prepare a disposable
environment with candidate pins gymnasium==1.2.1,
stable-baselines3==2.7.0 and a compatible **CPU-only** PyTorch build. These
are example upstream version pins, **not a validated full dependency lockfile**.
Both Gymnasium and Stable-Baselines3 are MIT licensed; PyTorch and all transitives
require a separate license/security review before redistribution.

~~~sh
PYTHONPATH=src python -m mojum.sb3_room_baseline --mode sb3 --seed 7 --train-timesteps 256
~~~

SB3 is lazily imported only in this mode. It trains in memory with CPU device,
one Torch thread, PPO MultiInputPolicy, two training fixtures and a small
network. It then evaluates deterministic frozen predictions on separately
created held-out episodes and compares with both control scores. Training is
bounded to 64..4096 requested steps (SB3 rollout rounding is reported), and
evaluation to 64 steps x 3 scenes. No weights are saved, no implicit downloads,
no private journal/Graph IR access, and no real device outputs. Performance and
cross-platform numerical reproducibility have **not been measured**.

## Independent acceptance requirements

- Merge only after independent exact-head/base CI + reviewer approval.
- Run the real Gymnasium check_env and a real SB3 training smoke in an
  isolated optional-dependency CI job; the stdlib tests cannot prove either.
- PR #725 currently restricts seed to uint32, with a separate QA fix #750.
  This benchmark uses bounded seeds until that fix is integrated.
- No claim of learned improvement unless held-out SB3 scores beat frozen controls.
- No external assets, network, authentication tokens, journal data, or auto-enable.
