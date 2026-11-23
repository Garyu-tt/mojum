# PettingZoo isolated parallel point-room exercise R0.1 (#700)

**Research-only and opt-in.** `mojum.pettingzoo_pointroom.PointRoomExercise` is a stdlib-only two-agent meeting exercise reusing released `world_room.Room`, `initial_state` and swept-wall `advance`. It is **not** connected to the canonical Aquarium, GENESIS, school, Noosphere/SQLite, Universal Graph IR, real motors or PRIVATE organism state. A scripted demonstration does not prove learning or general coordination.

## Contract

- Schema `mojum.pointroom-parallel/1`; two agents `agent_0` and `agent_1`.
- Each synchronous tick uses Discrete(5): 0 rest, 1 east, 2 west, 3 north, 4 south, maximum 0.25 m commanded per agent. Existing wall/obstacle collision applies, without agent-agent physical contact simulation.
- Observation: only normalized current own/peer XY and elapsed fraction, five floats in [0,1]. No future observations, hidden geometry, IDs, hardware data or PRIVATE memory.
- Shared reward `1.0` only on the first meeting, `0.0` otherwise. Termination on a virtual meeting; truncation on `max_cycles` (1..512). `info.blocked` is virtual wall outcome, `physical_contact` is always false.
- Explicit uint32 seed chooses one of four mirrored initial layouts with a private RNG. Invalid options/actions are rejected without partially stepping either agent. No filesystem, network, real clock, or SQLite.

## Standalone stdlib tests

```sh
PYTHONPATH=src python -m unittest tests.test_pettingzoo_pointroom -v
```

Run the real optional PettingZoo integration (separately installed research environment, not a base dependency):

```sh
python -m pip install 'pettingzoo==1.27.0' 'gymnasium==1.2.2' 'numpy==2.2.6'
PYTHONPATH=src python -m unittest tests.test_pettingzoo_pointroom -v
PYTHONPATH=src python - <<'PY'
from mojum.optional_pettingzoo import make_parallel_env
from pettingzoo.test import parallel_api_test
env = make_parallel_env()
parallel_api_test(env, num_cycles=30)
observations, infos = env.reset(seed=42)
while env.agents:
    observations, rewards, terminations, truncations, infos = env.step({a: 0 for a in env.agents})
print(rewards, terminations, truncations)
env.close()
PY
```

Explicit factory `make_parallel_env` lazily imports optional libraries and creates a real `pettingzoo.ParallelEnv` subclass, with Gymnasium `Discrete(5)` and `Box(0..1, shape=(5,), dtype=float32)` spaces. Importing Mojum alone does not import PettingZoo or NumPy. No auto-install, registry activation, remote integration or persistence. Missing optional modules fail with an explicit `ImportError`.

## Reproducibility and acceptance limits

- Two agents, CPU only, <=512 steps per episode, O(2) agent state, O(2 * obstacle count) swept obstacle checks per step. Room fixture caps obstacles at 4096.
- Local tests cover reproducible trajectory, held-out fixed seeds against a frozen rest control, virtual obstacle rejection, strict input bounds/atomicity, and restart/truncation. Real `parallel_api_test` runs only when the optional package is installed; green stdlib tests alone **do not** prove the external API.
- Dedicated optional CI matrix on Python 3.11/3.12/3.13 pins PettingZoo **1.27.0** (2026-08-13, MIT) with Gymnasium 1.2.2 and NumPy 2.2.6. Verify all transitive/asset licenses and exact-head CI before acceptance. No model training, benchmark speed/accuracy claims or actual robot contact.

Upstream contract: https://pettingzoo.farama.org/api/parallel/ . Package and MIT license evidence: https://pypi.org/project/pettingzoo/1.27.0/ .
