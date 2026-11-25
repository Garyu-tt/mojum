# Offline visual-navigation example R0.1

This example adds a deterministic, stdlib-only navigation fixture inspired by
ViZDoom's visual-observation research workflow. It is **not** the ViZDoom
engine and makes no claim about Doom rendering, game performance,
reinforcement-learning quality, or sim-to-real transfer.

## Run

From the repository root:

```sh
python examples/vizdoom/offline_navigation.py --seed 7 --policy frozen
python examples/vizdoom/offline_navigation.py --seed 7 --policy wait --max-steps 12
PYTHONPATH=src python -m unittest discover -s tests -p 'test_vizdoom_example.py' -v
```

The fixture generates a bounded 7x7 synthetic maze. The top row and rightmost
column form a guaranteed route; seeded interior obstacles make observations
replayable. The observation is a tuple of ASCII rows (`@` agent, `G` goal,
`#` wall, `.` empty). The frozen baseline uses deterministic breadth-first
search with stable action order UP, RIGHT, DOWN, LEFT. The `wait` control
does not learn or move and should time out. Episodes are capped at 256 steps.

The versioned JSON result is marked `PUBLIC_SYNTHETIC` and includes the seed,
policy, initial observation, per-step actions/rewards/terminal flags and
success/truncation outcome. It contains no user data, filesystem/network
access, host/device identifiers, private journal state or actuator commands.

## Optional upstream engine

The upstream project is [Farama Foundation ViZDoom](https://github.com/Farama-Foundation/ViZDoom).
The release notes list ViZDoom **1.3.1**, released 2026-09-20. The upstream
README states that code original to ViZDoom is MIT-licensed, while ZDoom-derived
components have varying licensing schemes. Review the exact release's bundled
notices and scenario/WAD asset terms before redistribution. This repository
does not bundle ViZDoom, Doom assets, scenarios or model files.

If an operator separately installs a compatible ViZDoom build, the example's
`require_vizdoom()` helper can explicitly import it. That helper is only an
import boundary, not an adapter, scenario launcher, sandbox or security
boundary. No dependency is added to Mojum's core and no automatic download,
installation, engine startup or network access occurs.

## Resource and safety boundaries

- Core fixture: Python 3.11+, standard library only; tiny bounded 7x7 state.
- Episode budget: caller-selected 1-256 steps.
- Seed range: unsigned 32-bit integer.
- Optional ViZDoom resource use depends on the separately selected scenario,
  resolution, frame-skip and engine build; measure it on the target host.
- No production runtime wiring, learning update, PRIVATE Noosphere/Aquarium
  access, physical control, or canonical Graph IR/journal mutation.
- This synthetic BFS baseline is a reference control, not a learned agent.
