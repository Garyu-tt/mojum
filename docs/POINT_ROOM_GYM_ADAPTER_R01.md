# Opt-in deterministic point-room episode facade (R0.1)

Issue: #690. This is a **CPU stdlib-only fixture**, not a released Gymnasium package, a physics engine, a trainable organism, or a registered RL environment.

The entry point `mojum.point_room_env.PointRoomEnv` presents a Gymnasium-shaped API:

```python
from mojum.point_room_env import PointRoomEnv
from mojum.world_room import Room

env = PointRoomEnv(Room(4, 4), start=(1, 1), max_steps=2)
observation, reset_info = env.reset(seed=0)
observation, reward, terminated, truncated, info = env.step((0.1, 0.0))
```

- The room is the existing bounded, swept-collision, point-only fixture; each action is a finite `(dx,dy)` pair within a 0.25 m radius by default.
- Observations are synthetic `tick/x/y/revision` only, with no hidden map or future measurements. The caller already owns the room geometry.
- `reward=0.0` and `terminated=False` are intentional goal-free defaults. `truncated=True` when the configured step budget is reached. A separate experimental reward policy may be benchmarked later.
- `seed` is validated and retained in reset metadata, but has no effect because the fixture is fully deterministic and has no RNG.
- No Noosphere journal, model, SQLite file, network, GPU, hardware actuation or external import is touched. Each `reset` starts a fresh episode, not an authoritative organism continuation.
- Later integration with `gymnasium.Env` and Stable-Baselines3 belongs in an isolated opt-in adapter after versioned observation/action spaces and held-out baseline evaluation are agreed.

Run regression tests with `PYTHONPATH=src python -m unittest tests.test_point_room_env -v` or the normal repository discover suite.
