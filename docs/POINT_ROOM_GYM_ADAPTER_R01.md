# Opt-in deterministic point-room episode facade (R0.1)

Issue: #690. This is a **CPU stdlib-only fixture**, not a mandatory Gymnasium package, a physics engine, a trainable organism, or a registered RL environment.

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
- An **optional actual Gymnasium wrapper** now exists at `mojum.gymnasium_adapter.make_gymnasium_point_room`. Constructing it requires separate `gymnasium` and `numpy` packages; importing the module or running the core Aquarium does not. It advertises a bounded Box action space and Dict observation space, returns 5-tuples from `step`, inherits `gym.Env`, and retains the zero-reward/fixed-reset fixture semantics. This is not a trained SB3 baseline, reward policy or registered `gymnasium.make()` environment. The smaller square action range sits inside the core's Euclidean displacement budget; complete movement coverage remains in the core facade.

```python
from mojum.gymnasium_adapter import make_gymnasium_point_room
from mojum.world_room import Room
env = make_gymnasium_point_room(Room(4, 4), start=(1, 1))
obs, info = env.reset(seed=1)
obs, reward, terminated, truncated, info = env.step([0.1, 0.0])
```

The optional smoke test skips when Gymnasium is absent. Stable-Baselines3 training, held-out evaluation and real articulated physics remain future work.

Run regression tests with `PYTHONPATH=src python -m unittest tests.test_point_room_env -v` or the normal repository discover suite.
