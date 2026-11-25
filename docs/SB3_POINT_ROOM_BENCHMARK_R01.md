# SB3 optional CPU benchmark R0.1 (#690 follow-up)

This is a **stacked Draft** on the existing independent Gymnasium point-room PR #725 (`db70b0ebd3013527eae5eafc6d9e1733b95a0309`), **not** a new point-room simulator. It is separate from the #690 Gymnasium adapter authors. Everything stays opt-in, PUBLIC synthetic and disconnected from actual organisms, Noosphere journals, real motors, network services, and canonical policy persistence.

## Execution

```bash
# Always works with Python 3.11+ and no optional dependencies. Prints plan ONLY.
PYTHONPATH=src python -m mojum.sb3_room_benchmark

# Only in an isolated test environment after reviewing transitive dependencies:
python -m pip install 'gymnasium==1.2.1' 'stable-baselines3==2.9.2'
# SB3 also pulls CPU-capable PyTorch, NumPy and other optional stack components.
# Ensure CPU-only torch wheels for the target platform; no GPU requirement.
PYTHONPATH=src python -m mojum.sb3_room_benchmark --run --timesteps 2048 --seed 20261123
PYTHONPATH=src python -m unittest discover -s tests -p 'test_sb3_room_benchmark.py' -v
```

- PPO `MultiInputPolicy`, `device='cpu'`, one Torch intra-op thread, 4 fixed train rooms in a `DummyVecEnv`, 64 steps per actor rollout, batch size 64, 2 epochs per update, small 32x32 policy/value heads.
- Allowed training sizes: **256..8192 in multiples of 256**, avoiding hidden PPO rollout overshoot. Four disjoint held-out rooms, max 48 simulation steps each. All scenes are embedded public synthetic geometry. Episode `seed` is only a provenance marker because the point-room has no RNG; **the held-out unit is the room geometry, not the numerical seed**.
- Initial untrained PPO policy is evaluated **before training** on held-out rooms, then the *same model weights* are trained on train rooms and re-evaluated on identical held-out rooms using deterministic action selection. Per-scene returns/success/blocked steps and aggregate deltas are printed as JSON. A zero/negative delta is an allowed, honest result; do not imply intelligence or generalization from a tiny fixture.
- No model checkpoints, shell downloads, cloud inference, human/organism observations or disk journals are created. No optional packages are imported by `import mojum.sb3_room_benchmark` or dry-run. Parent `gym_room` and `world_room` semantics are unchanged.
- The baseline can be expensive on some CPU-only hosts because PyTorch dominates the stdlib fixture; use a disposable machine/environment and measure peak RAM/time independently before wider automation.

## License and acceptance

- [Stable-Baselines3 2.9.2](https://pypi.org/project/stable-baselines3/2.9.2/), MIT, published 2026-10-26; [Gymnasium 1.2.1](https://pypi.org/project/gymnasium/1.2.1/), MIT. Their transitive PyTorch/NumPy licenses and platform wheels require separate release review. Version pins are examples for the **opt-in** test environment only; `pyproject.toml` adds **no** new default dependencies.
- Failure modes covered: unavailable optional packages, illegal actions, invalid/huge training budgets, duplicate held-out scenes, deterministic frozen evaluation and non-training dry-run. CI should additionally run the real `--run --timesteps 256` once with pinned wheels and test the `gymnasium.utils.env_checker` on the parent PR; that has **not** been completed in this draft.
- Stacked review requires #725 to be merged first or an independently verified equivalent adapter. QA approval and exact-head CI required; **do not automatically merge to main**.
