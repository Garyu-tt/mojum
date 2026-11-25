# Gymnasium-compatible point-room, opt-in R0.1 (#690)

This is an **offline deterministic synthetic point-room** training interface,
not articulated MuJoCo physics, a learned brain, or permission to control hardware.
The canonical `mojum.world_room.Room/advance` implementation is unchanged.

## Stdlib-only adapter

```python
from mojum.gym_room import PointRoomEpisode
from mojum.world_room import Room

episode = PointRoomEpisode(Room(4, 4), start=(1, 1), goal=(3, 3), max_steps=64)
observation, info = episode.reset(seed=123)
observation, reward, terminated, truncated, info = episode.step(1)  # east
```

Actions: 0=no-op, 1=east, 2=west, 3=north, 4=south. One action
moves at most `step_m` meters (default 0.25). Observation contains only
`position`, configured `goal`, previous `blocked` flag and `tick`; no hidden
obstacle map or future state. `info` contains the original revision/provenance,
a physical-contact=false disclaimer, and provided seed. Invalid actions do not
advance state. Reward is +1 at goal, -0.05 on blocked, -0.01 otherwise.
`terminated` indicates within `goal_radius_m` of the goal; `truncated`
indicates the step horizon was reached first. Reset must precede step.

Integer-like actions, including NumPy integers from `Discrete.sample()`, are
accepted through `__index__`. If that conversion calls `reset` or `step` on the
same episode, the nested call raises `RuntimeError` before changing state.
An uncaught conversion error leaves the episode at its previous tick and seed;
the guard is released even when conversion fails. Catching the nested-call error
and returning a valid action permits exactly the outer step. This preserves the
configured horizon and end-of-episode gate without changing action IDs or rewards.

## Optional real Gymnasium

Install separately into an isolated environment, after dependency/license review:

```sh
python -m pip install 'gymnasium==1.2.2'
PYTHONPATH=src python -m unittest tests.test_gym_room -v
```

```python
from mojum.gym_room import make_gymnasium_env
from mojum.world_room import Room

env = make_gymnasium_env(Room(4, 4), start=(1, 1), goal=(3, 3))
observation, info = env.reset(seed=7)
assert env.observation_space.contains(observation)
observation, reward, terminated, truncated, info = env.step(env.action_space.sample())
env.close()
```

Gymnasium is imported **only in the factory**. It is not a default dependency,
and it cannot read/write Noosphere, Graph IR, or the organism's real motor
controller. Test Gymnasium's environment checker and a frozen-policy control
before adding Stable-Baselines3. `dm_control` is research-only.

## Scope and acceptance notes

- Python >=3.11, stdlib baseline; optional Gymnasium transitive NumPy only.
- Upstream references: Farama Gymnasium (MIT license; re-check exact pinned
  release + transitive licenses before distribution), SB3 (MIT; **not used**),
  dm_control (Apache 2.0; **not used**). The integration registry stays `planned`
  until reviewed under its license gates. Pin shown above is an example
  environment lock, matches the dedicated CI matrix, not a production clearance.
- Fixed start and goal mean `seed` is recorded but there is no stochastic
  reset yet; reproducibility is exact for repeated actions and geometry.
- CPU/disk: O(obstacle_count) per step for canonical swept collision;
  no disk writes and no network. Benchmark real hardware separately.
- Tests cover step/reset/termination/truncation, numeric rejection,
  action rollback, obstacles, revision provenance, optional import isolation.
- Independent QA must run exact-head CI and review upstream licenses before
  considering merge.
