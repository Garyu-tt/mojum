# Point-room episode adapter R0.1

mojum.point_room_env.PointRoomEnv is a dependency-free, deterministic episode
wrapper over the canonical synthetic mojum.world_room fixture. It exposes a
Gymnasium-like reset(seed=..., options=...) and step((dx, dy)) surface, but
it does not inherit from gymnasium.Env and does not install Gymnasium,
Stable-Baselines3, NumPy or any other optional package.

## Minimal use

```python
from mojum.point_room_env import PointRoomEnv
from mojum.world_room import Room

env = PointRoomEnv(
    Room(5, 5, ((2, 0, 1, 4),)),
    start_position=(1, 1),
    spawn_positions=((1, 1), (1, 4)),
    goal_position=(4, 4),
    max_episode_steps=100,
)
observation, info = env.reset(seed=17)
observation, reward, terminated, truncated, info = env.step((0.1, 0.0))
```

## Contract

- Schema: mojum.point-room-episode/1; synthetic provenance is explicit.
- Actions are finite native numeric (dx, dy) displacements in metres; booleans,
  non-finite values, malformed shapes and actions above the configured Euclidean
  budget are rejected before the world is advanced.
- reset(seed=N) reproduces spawn selection. reset(seed=None) continues the
  existing local PRNG stream. An explicit options.start_position is admitted
  only when it is a valid free point in the canonical room.
- The world state advances exactly one logical tick through world_room.advance,
  retaining its revision check and swept-AABB collision behavior.
- Optional goal tolerance returns terminated=True and reward 1.0 when reached.
  Otherwise reward is 0.0. Exhausting max_episode_steps returns truncated=True.
  Goal termination takes precedence on the final permitted step.
- observation and info are detached dictionaries. Mutating a returned mapping
  cannot mutate environment state.
- A completed or truncated episode requires reset() before another step().

## Resource and safety boundaries

- CPython 3.11+, standard library only; no mandatory dependencies or I/O.
- Up to 100,000 steps per episode and 256 caller-declared spawn positions.
- Room and RoomState remain the canonical fixture; this adapter does not
  modify the room, SQLite journals, Aquarium policy identity, Graph IR, Noosphere,
  hardware, or production runtime.
- Synthetic 2D collision is not physical contact or MuJoCo dynamics. Reward is a
  small task-fixture signal, not a learning-quality or behavioral claim.
- This is not an installed Gymnasium environment. Any future Gymnasium/SB3
  integration must be optional, version-pinned, separately tested, and must
  compare against no-learning controls on held-out seeds.

## Verification

Run the isolated suite with:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_point_room_env.py' -v
```
