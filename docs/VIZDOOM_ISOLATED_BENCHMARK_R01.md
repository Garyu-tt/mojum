# ViZDoom isolated visual-navigation benchmark R0.1 (#700(d))

**Experimental opt-in research only.** This is not Mojum's Aquarium brain,
training, robotics, external game controller, or release-ready integration.
It uses only locally rendered RGB24 frames (160x120) and 3 authorized virtual
actions: `MOVE_FORWARD`, `TURN_LEFT`, `TURN_RIGHT`. `game_variables`,
map/automap, object/sector lists, depth and labels are not policy inputs.
No PRIVATE Noosphere data, canonical SQLite, credentials, telemetry,
live organisms or physical actuation is accessible to this module.
The baseline process does not import ViZDoom or open files/network connections.

## Reproducible CPU fixture (no dependencies)

```sh
PYTHONPATH=src python -m mojum.vizdoom_benchmark --engine fixture --seed 7 --steps 32
PYTHONPATH=src python -m unittest discover -s tests -p test_vizdoom_benchmark.py -v
```

The synthetic fake is an **API test, not the real engine**. A frozen policy
always chooses forward, a hardcoded camera-reactive policy turns when sampled
middle-frame brightness is low. Neither learns. Outputs contain version,
seed, realized ticks, total reward, per-policy SHA-256 trace digest and a
repeat-execution equality gate. No raw frames or game state are persisted.
Failure cases include malformed/oversized screens, nonfinite engine rewards,
invalid seeds, oversized episodes, and guaranteed `close()` after errors.
The only computed visual features are coarse region brightness values,
not privileged geometry.

## Separately installed real-engine smoke (explicit opt-in)

Upstream: https://github.com/Farama-Foundation/ViZDoom

- Pinned test target: `vizdoom==1.3.3` (published 2026-11-22).
- Licensing: original ViZDoom MIT, but bundled ZDoom and scenario/WAD assets
  can involve different licenses. Audit redistribution **separately**. No
  upstream source, WAD or model assets are copied into Mojum.
- Linux: upstream documents binary wheels for Python 3.11+, x86-64/AArch64.
  Installed native engine, its `basic.wad`, Python/OS and CPU availability
  are required for the real smoke only. No automatic download/install occurs.
- Choose an **isolated disposable process/container** with no Mojum PRIVATE
  file mounts and egress denied by the operator, memory/CPU/runtime OS limits
  set externally. In-process `MAX_STEPS` is **not** a native-engine RSS, CPU,
  thread, fork, or wall-clock sandbox. Run only on trusted test hosts.

```sh
# Operator installs a permitted pinned wheel in an isolated environment first.
PYTHONPATH=src python -m mojum.vizdoom_benchmark --engine vizdoom --seed 7 --steps 32
```

The `vizdoom` mode loads only the pinned package's `basic.wad`, locks RGB24
160x120, disables display/audio and privileged buffers, fixes RNG seed, and
limits one episode to 256 single-tic actions max. It rejects missing/unpinned
packages and escapes from the package scenario directory. The two policies
run twice each and **fail** if observations/rewards/actions diverge on the
same host. Native determinism/CPU budget across platforms **not established**
by the synthetic tests; external QA must record CPU time, RSS, game version,
platform, outcome of real-engine runs, and held-out seeds before promotion.

### Remaining acceptance gates

1. Actual ViZDoom 1.3.3 Linux CPU headless smoke on multiple held-out seeds,
   plus wall clock/RSS measurements and nondeterminism diagnosis.
2. Game scenario/license audit and meaningful navigation success/reward
   benchmark against the frozen no-learning control, no cherry-picked seeds.
3. Independent exact-head CI/peer acceptance; no integration into canonical
   organism memory, motor authority or live Aquarium without a new contract.
