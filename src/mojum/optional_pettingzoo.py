"""Explicit PettingZoo 1.27.0 ParallelEnv bridge; import remains stdlib-only."""

from .pettingzoo_pointroom import AGENTS, PointRoomExercise


def make_parallel_env(*, room=None, max_cycles=12):
    """Create optional environment; never installs dependencies or touches files."""
    try:
        import numpy as np
        from gymnasium import spaces
        from pettingzoo import ParallelEnv
    except ImportError as exc:
        raise ImportError(
            "Optional PettingZoo support requires separately installed "
            "pettingzoo==1.27.0, gymnasium and numpy"
        ) from exc

    class _PointRoomParallelEnv(ParallelEnv):
        metadata = {
            "name": "mojum_pointroom_parallel_v0",
            "render_modes": [],
            "is_parallelizable": True,
        }
        possible_agents = list(AGENTS)

        def __init__(self):
            self._core = PointRoomExercise(room=room, max_cycles=max_cycles)
            self.agents = []
            self._action_spaces = {a: spaces.Discrete(5) for a in AGENTS}
            self._observation_spaces = {
                a: spaces.Box(low=0, high=1, shape=(5,), dtype=np.float32)
                for a in AGENTS
            }

        def observation_space(self, agent):
            return self._observation_spaces[agent]

        def action_space(self, agent):
            return self._action_spaces[agent]

        def reset(self, seed=None, options=None):
            # PettingZoo's parallel_api_test passes arbitrary reset options.
            # Our fixture has no configurable reset options: accept the mapping
            # for API compliance, NEVER inspect/forward its keys or values, and
            # explicitly report that it was ignored. Strict core stays unchanged.
            if options is not None and type(options) is not dict:
                raise ValueError("reset options must be a dict or None")
            observations, infos = self._core.reset(seed=seed, options=None)
            if options:
                infos = {a: {**info, "reset_options_ignored": True}
                         for a, info in infos.items()}
            self.agents = list(self._core.agents)
            return {a: np.asarray(v, dtype=np.float32)
                    for a, v in observations.items()}, infos

        def step(self, actions):
            if not self.agents:
                return {}, {}, {}, {}, {}
            if type(actions) is not dict or set(actions) != set(self.agents):
                raise ValueError("actions must match exactly the live agents")
            if any(not self.action_space(a).contains(v) for a, v in actions.items()):
                raise ValueError("invalid PettingZoo discrete action")
            observations, rewards, terminations, truncations, infos = self._core.step(
                {a: int(v) for a, v in actions.items()}
            )
            self.agents = list(self._core.agents)
            return {a: np.asarray(v, dtype=np.float32)
                    for a, v in observations.items()}, rewards, terminations, truncations, infos

        def render(self):
            return None

        def close(self):
            return None

    return _PointRoomParallelEnv()
