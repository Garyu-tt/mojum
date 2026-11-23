# RoboPianist optional keyboard benchmark boundary (issue #700, slice b)

**Status: isolated CPU-first adapter + synthetic fixture only.** This adds no real policy, no trained dexterity, no physical piano, no replacement for the `school.py` / `school_activity.py` music catalog and no change to canonical Aquarium, GENESIS, Graph IR, Noosphere or private SQLite journals. It does not claim that the RoboPianist native engine was installed or executed on a physical machine.

## Upstream and dependency review

- Source: [google-research/robopianist](https://github.com/google-research/robopianist), reviewed against `main` commit [`0d9736c64eba5faafdf214ed7d38d648ffbd5c7f`](https://github.com/google-research/robopianist/tree/0d9736c64eba5faafdf214ed7d38d648ffbd5c7f). **Pin this exact commit when deploying** to a disposable benchmark environment; do not install from mutable `main` or let it mutate Mojum's core requirements.
- RoboPianist repository code: Apache-2.0 (`LICENSE`). MuJoCo/Menagerie assets, MIDI/PIG dataset and soundfonts have separate licenses and redistribution constraints. Specifically, upstream says PIG cannot be redistributed on GitHub. **Licenses of every chosen task's assets and transitive dependencies still require independent approval.** This PR does not import any upstream code/assets.
- Upstream uses `suite.load(task)`, the `dm_env` `reset()`/`step()` and `action_spec()` protocol, and `timestep.observation["goal"]`; the first `task.piano.n_keys` values are the immediate key targets in its published tutorial. The repository was archived on 2026-01-06; no expectation of ongoing upstream maintenance.
- Optional backend requires RoboPianist, NumPy, dm_control, MuJoCo and its compatible native/Menagerie dependencies **only in an isolated, manually provisioned environment**. No pip dependency was added to Mojum. Prefer a pinned Python 3.10 benchmark venv for legacy upstream compatibility; core remains Python 3.11+ without these packages. Do not run the optional loader on a production host.

## Local offline exercise

```bash
PYTHONPATH=src python -m mojum.robopianist_bridge --seed 1337 --held-out-seed 7331
PYTHONPATH=src python -m unittest tests.test_robopianist_bridge -v
```

The fixture simulates 12 keys across 16 steps using seeded public/synthetic targets, a *scripted target-knowing* controller, and a frozen all-zero comparator on both a training and distinct held-out seed. Results are hashed for deterministic trace equality. The scripted comparator is deliberately privileged and **is not learning**; its superior synthetic reward proves only scoring and replay integrity. No MIDI/audio is emitted, no hardware is touched and no private observation is read.

The opt-in native boundary is:

```python
from mojum.robopianist_bridge import open_robopianist

# In a separately installed, pinned benchmark venv only:
bridge = open_robopianist("RoboPianist-debug-TwinkleTwinkleLittleStar-v0")
frame = bridge.reset()  # only target_keys, reward, discount, terminal, tick, virtual_only
# A separate virtual controller supplies a bounded action respecting the
# actual installed env.action_spec(). No real motor/hardware signal is accepted.
# result = bridge.step(action_vector)
```

`open_robopianist` validates a suite task name against the installed registry and lazily imports its code. `PianoBridge` also accepts a caller-supplied `dm_env`-style environment for deterministic tests. The optional backend itself has **not** been integration-tested here against installed native RoboPianist. Real environment rewards are backend-reported, not proof of observed physical keypress or dexterity.

## Safety and resource contract

- `mojum.robopianist-frame/1` carries only a bounded integer tick, selected goal key indices, scalar reward/discount, source class, terminal flag and `virtual_only`. It never forwards full observation, images, filenames, piano MIDI, prompt text, personal data, model state or raw actions into returned traces. No private journal writer is attached.
- Bounded per session: at most 64 `step` calls, 256 action DOFs, 88 displayed keys and 1424 upstream goal entries. Action validation is complete **before** external dispatch; rejected inputs cannot call `env.step`.
- Backend failure or malformed post-step observation poisons that session. No automatic retry after ambiguous effects. Real upstream resources/latency remain unmeasured; no claims about thread safety, CPU/RAM at true 88-key MuJoCo scale or Android support.
- Fixture-only budget: 2 seeds × 2 controllers × 16 frames, 12 keys per frame. Zero network, no imports of native engine and no use of a hardware interface. The deterministic test checks invalid action types, bounds, NaN/Inf, terminal/reset and failure isolation, redaction and trace stability.
- All values and events are **virtual**. Future body/school integration needs an explicit authorization boundary, versioned motor observation, asset provenance, measured CPU baseline, actual installed-backend test, independent QA and exact-head CI approval before merge. Nothing from this branch is enabled in released `main` by default.
