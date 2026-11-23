"""Opt-in, deterministic two-agent learning exercise over the synthetic room.

No PettingZoo import, journal writes, physical controls or learned-policy claims.
The canonical Aquarium never imports or creates this exercise.
"""

import math
import random

from .world_room import Room, advance, initial_state

SCHEMA = "mojum.pointroom-parallel/1"
AGENTS = ("agent_0", "agent_1")
ACTION_DELTAS = {
    0: (0.0, 0.0),  # rest
    1: (0.25, 0.0),  # east
    2: (-0.25, 0.0),  # west
    3: (0.0, 0.25),  # north
    4: (0.0, -0.25),  # south
}
_STARTS = (
    ((1.0, 2.0), (3.0, 2.0)),
    ((3.0, 2.0), (1.0, 2.0)),
    ((2.0, 1.0), (2.0, 3.0)),
    ((2.0, 3.0), (2.0, 1.0)),
)


class PointRoomExercise:
    """Pure-stdlib simultaneous two-agent meeting task for offline benchmarks.

    Observation: (own_x/w, own_y/h, peer_x/w, peer_y/h, tick/max_cycles).
    All values are bounded in [0, 1]. Reward 1.0 on first meeting, 0.0
    otherwise. Reaching the same virtual point is *not* a physics contact.
    """

    def __init__(self, *, room=None, max_cycles=12):
        if room is None:
            room = Room(4, 4)
        if not isinstance(room, Room):
            raise TypeError("room must be a world_room.Room")
        if type(max_cycles) is not int or not 1 <= max_cycles <= 512:
            raise ValueError("max_cycles must be an integer in [1, 512]")
        self.room = room
        self.max_cycles = max_cycles
        self.agents = []
        self.tick = 0
        self._states = None
        self.seed = None

    def reset(self, *, seed=None, options=None):
        if seed is None:
            seed = 0
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if options is not None and (type(options) is not dict or options):
            raise ValueError("only empty reset options are supported")
        starts = _STARTS[random.Random(seed).randrange(len(_STARTS))]
        # Validate all starts before mutating live state (including obstacle hits).
        states = {a: initial_state(self.room, *p) for a, p in zip(AGENTS, starts)}
        self._states = states
        self.tick = 0
        self.seed = seed
        self.agents = list(AGENTS)
        return self._observations(), {a: {"schema": SCHEMA} for a in AGENTS}

    def _observations(self):
        left = self._states[AGENTS[0]]
        right = self._states[AGENTS[1]]
        obs = {}
        for agent, own, peer in ((AGENTS[0], left, right), (AGENTS[1], right, left)):
            obs[agent] = (
                own.x / self.room.width,
                own.y / self.room.height,
                peer.x / self.room.width,
                peer.y / self.room.height,
                self.tick / self.max_cycles,
            )
        return obs

    def step(self, actions):
        if not self.agents or self._states is None:
            raise RuntimeError("reset before stepping; completed episodes cannot advance")
        # Reject subclassed keys before set equality: str subclasses can run
        # hostile __eq__ implementations during hash-table comparison.
        if type(actions) is not dict or any(type(key) is not str for key in actions):
            raise ValueError("actions must be a plain dict with native string keys")
        if set(actions) != set(self.agents):
            raise ValueError("actions must match exactly the two live agents")
        if any(type(a) is not int or a not in ACTION_DELTAS for a in actions.values()):
            raise ValueError("each action must be a native integer from 0 to 4")
        tick = self.tick + 1
        next_states = {}
        measured = {}
        # Prepare both moves before committing any state (fail-closed on error).
        for agent in AGENTS:
            dx, dy = ACTION_DELTAS[actions[agent]]
            state = self._states[agent]
            next_states[agent], measured[agent] = advance(
                self.room, state, tick=tick, dx=dx, dy=dy,
                expected_revision=state.revision,
            )
        left, right = (next_states[a] for a in AGENTS)
        met = math.hypot(left.x - right.x, left.y - right.y) <= 1e-9
        ended = met or tick >= self.max_cycles
        self._states = next_states
        self.tick = tick
        observations = self._observations()
        rewards = {a: float(met) for a in AGENTS}
        terminations = {a: met for a in AGENTS}
        truncations = {a: tick >= self.max_cycles and not met for a in AGENTS}
        infos = {
            a: {"schema": SCHEMA, "blocked": measured[a]["blocked"],
                "physical_contact": False}
            for a in AGENTS
        }
        if ended:
            self.agents = []
        return observations, rewards, terminations, truncations, infos
