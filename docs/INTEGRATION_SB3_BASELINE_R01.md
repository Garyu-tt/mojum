# Optional Stable-Baselines3 held-out point-room benchmark R0.1 (#754)

**Status: staged experiment, not a runtime/learning integration.** This subtask is separate from the already claimed #690 / PR #725 Gymnasium adapter. The proposed benchmark is intentionally stacked on `integration/690-gymnasium-room-r01` until that adapter is accepted; `main` must remain stdlib-only. No changes to canonical Aquarium, live graph/GENESIS, Noosphere SQLite journals, real motor signals, or organism identity.

## What is measured

- Fixed `TRAIN_SEEDS=(11,19,23,29)` and disjoint `HELDOUT_SEEDS=(101,103,107,109)` deterministically generate public, synthetic 4×4-metre scenes with distinct start/goal pairs and no private data. Exact 0.25 m actions; max 64 steps per episode. These are *different held-out scenes*, not merely new RNG labels on the same unchanging room. Basic geometry delegates to `mojum.world_room` through PR #725's `PointRoomEpisode` / `make_gymnasium_env`.
- The **frozen no-learning control** uses an independent, deterministic uniform action stream on held-out scenes. PPO trains only on the four train scenes, then the same held-out scenes are evaluated with deterministic model actions. Metrics: reached-goal fraction, average bounded return, blocked actions and per-scene steps/returns. Results never imply genuine learning or transfer unless a separately predeclared QA gate accepts them; a negative delta is reported unchanged.
- No model checkpoint, private prompt, API token, network service, external dataset or locally chosen file path is accepted. CLI reports only public synthetic metrics to stdout. No live motor or simulation control is granted.

## Invocation

Default: `PYTHONPATH=src python -m mojum.sb3_benchmark` (offline, stdlib-only, frozen control; never loads PyTorch).

Native **explicit opt-in** (after installing independently in an isolated venv):

```sh
python -m pip install 'stable-baselines3==2.9.2' 'gymnasium==1.3.0'
PYTHONPATH=src python -m mojum.sb3_benchmark --native-sb3 --timesteps 2048 --policy-seed 2026
```

The CLI refuses an unpinned SB3/Gymnasium version, non-128-multiple training budgets, bool/negative seeds and budgets over 32768 PPO timesteps. PPO requests a single CPU device and one PyTorch worker thread. Default training may not improve goal success: report the frozen-vs-trained comparison as observed, not as a guaranteed win. Models are not saved or published.

## Dependency, license and runtime gate

- Stable-Baselines3 **2.9.2** (MIT), Gymnasium **1.3.0** (MIT). SB3's deep learning engine is PyTorch (BSD-style; transitive licenses and exact wheel hashes remain to be reviewed), plus NumPy. This document records an *assessment*, not Mojum license approval or dependency installation.
- The Python standard library path remains usable on 3.11+ without these packages; the native path has NOT been approved for production or independently performance-certified. Running the native command installs nothing automatically. Pin the remaining transitive build/OS wheels in a CI lock before release acceptance.
- Budget: 128 to 32768 requested training timesteps, 64 eval steps × 4 held-out scenes × 2 policies, one CPU thread; **no enforceable RAM/host CPU maximum**, so run only in a disposable, externally resource-limited sandbox. Native wall/process CPU seconds are reported. The script does not guarantee cross-CPU bit-identical torch results.
- Failure-mode tests cover invalid/overlapping seeds and scene splits, action bool/NaN/range faults, missing or wrong optional package versions, no hidden trained metric in the stdlib run, and deterministic frozen replay. Native Gymnasium/SB3 training and RAM/RSS profiling have **not** been locally verified. Dedicated exact-head CI now attempts both stdlib and native CPU checks; its results and independent QA are required.

## Acceptance commands

```sh
PYTHONPATH=src python -m unittest tests.test_sb3_benchmark -v
PYTHONPATH=src python -m mojum.sb3_benchmark
```

Do not merge this stacked PR into `main` without integrating/reviewing parent #725 and rerunning the tests against exact merged HEAD. Benchmark results are *not* suitable as a release acceptance metric until the environment, algorithm, sample size and pass threshold are predeclared independently.

## Dedicated CI

`.github/workflows/sb3-optional-cpu-qa.yml` runs Python 3.11/3.13 stdlib regression and attempts pinned native `torch==2.9.1` CPU wheel, SB3 2.9.2 and Gymnasium 1.3.0 on Python 3.11 with 128 PPO timesteps. It requests one CPU thread, disables visible CUDA devices and bounds job runtime to 15 minutes. GitHub Actions completion is evidence of the exact tested HEAD, not production-grade reproducibility or host-RAM enforcement.
