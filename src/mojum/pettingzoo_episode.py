"""Bounded, offline admission for PUBLIC_SYNTHETIC PettingZoo episode traces.

This module does not import PettingZoo or execute an environment. It validates
an in-memory export supplied by an explicitly configured external adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA = "mojum.pettingzoo-episode/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_AGENTS = 64
MAX_STEPS = 10_000
MAX_VECTOR = 256
MAX_NAME = 64
MAX_SEED = 2**64 - 1
MAX_TOTAL_SCALARS = 100_000


@dataclass(frozen=True)
class EpisodeReceipt:
    """Detached immutable normalized episode and its consistency digest."""

    schema: str
    seed: int
    agents: tuple[str, ...]
    steps: tuple[tuple, ...]
    step_count: int
    digest: str


def _plain_dict(value, name):
    if type(value) is not dict:
        raise ValueError(name + " must be a plain dict")
    return value


def _exact_keys(value, expected, name):
    if len(value) != len(expected):
        raise ValueError(name + " has unexpected or missing fields")
    keys = tuple(value.keys())
    if any(type(key) is not str or len(key) > MAX_NAME for key in keys):
        raise ValueError(name + " field names must be bounded native strings")
    if set(keys) != expected:
        raise ValueError(name + " has unexpected or missing fields")


def _number(value, name):
    if type(value) not in (int, float):
        raise ValueError(name + " must be a finite native number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(name + " must be a finite native number")
    return value


def _vector(value, name):
    if type(value) in (int, float):
        return (_number(value, name),)
    if type(value) not in (list, tuple) or not 1 <= len(value) <= MAX_VECTOR:
        raise ValueError(name + " must be a scalar or bounded numeric vector")
    return tuple(_number(item, name) for item in value)


def _agent_map(value, agents, name, converter, budget):
    mapping = _plain_dict(value, name)
    if len(mapping) != len(agents):
        raise ValueError(name + " must contain exactly the declared agents")
    keys = tuple(mapping.keys())
    if any(type(key) is not str or len(key) > MAX_NAME for key in keys):
        raise ValueError(name + " keys must be bounded native agent names")
    if set(keys) != set(agents):
        raise ValueError(name + " must contain exactly the declared agents")
    result = []
    for agent in agents:
        item = mapping[agent]
        cost = len(item) if type(item) in (list, tuple) else 1
        if cost > MAX_VECTOR or budget[0] < cost:
            raise ValueError("episode exceeds aggregate scalar budget")
        budget[0] -= cost
        result.append((agent, converter(item, name + "." + agent)))
    return tuple(result)


def admit_episode(record) -> EpisodeReceipt:
    """Validate and detach a bounded multi-agent trace; never mutates input.

    Input keys: schema, provenance, seed, agents, steps. Each step contains
    tick, observations, actions, rewards, terminations and truncations.
    Agent maps must cover exactly the declared agents at every step.
    """
    root = _plain_dict(record, "record")
    _exact_keys(root, {"schema", "provenance", "seed", "agents", "steps"}, "record")
    if (type(root["schema"]) is not str or type(root["provenance"]) is not str
            or root["schema"] != SCHEMA or root["provenance"] != PROVENANCE):
        raise ValueError("unsupported schema or provenance")
    seed = root["seed"]
    if type(seed) is not int or not 0 <= seed <= MAX_SEED:
        raise ValueError("seed must be a native unsigned 64-bit integer")

    raw_agents = root["agents"]
    if type(raw_agents) not in (list, tuple) or not 1 <= len(raw_agents) <= MAX_AGENTS:
        raise ValueError("agents must be a non-empty bounded sequence")
    agents = tuple(raw_agents)
    if any(type(a) is not str or not a or len(a) > MAX_NAME for a in agents):
        raise ValueError("invalid agent name")
    if len(set(agents)) != len(agents):
        raise ValueError("duplicate agent name")
    agents = tuple(sorted(agents))

    raw_steps = root["steps"]
    if type(raw_steps) not in (list, tuple) or not 1 <= len(raw_steps) <= MAX_STEPS:
        raise ValueError("steps must be a non-empty bounded sequence")

    normalized = []
    scalar_budget = [MAX_TOTAL_SCALARS]
    previous_tick = -1
    for index, raw in enumerate(raw_steps):
        step = _plain_dict(raw, "steps[%d]" % index)
        expected = {"tick", "observations", "actions", "rewards",
                    "terminations", "truncations"}
        _exact_keys(step, expected, "step")
        tick = step["tick"]
        if type(tick) is not int or not 0 <= tick <= 2**63 - 1 or tick <= previous_tick:
            raise ValueError("step ticks must be strictly increasing native integers")
        previous_tick = tick
        observations = _agent_map(step["observations"], agents, "observations", _vector, scalar_budget)
        actions = _agent_map(step["actions"], agents, "actions", _vector, scalar_budget)
        rewards = _agent_map(step["rewards"], agents, "rewards", _number, scalar_budget)
        terminations = _agent_map(
            step["terminations"], agents, "terminations",
            lambda v, n: v if type(v) is bool else (_ for _ in ()).throw(ValueError(n + " must be bool")), scalar_budget)
        truncations = _agent_map(
            step["truncations"], agents, "truncations",
            lambda v, n: v if type(v) is bool else (_ for _ in ()).throw(ValueError(n + " must be bool")))
        normalized.append((tick, observations, actions, rewards,
                           terminations, truncations))

    canonical = {
        "schema": SCHEMA,
        "provenance": PROVENANCE,
        "seed": seed,
        "agents": agents,
        "steps": normalized,
    }
    encoded = json.dumps(canonical, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":"), sort_keys=True).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return EpisodeReceipt(SCHEMA, seed, agents, tuple(normalized),
                          len(normalized), digest)
