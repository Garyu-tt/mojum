# PettingZoo episode admission R01

## Purpose

`mojum.pettingzoo_episode` is an opt-in, dependency-free boundary for a caller-supplied multi-agent episode exported by an external PettingZoo adapter. It does not import PettingZoo, instantiate an environment, run policies, train agents, or establish that an environment is correct.

## Wire contract

Schema: `mojum.pettingzoo-episode/1`

The input is one exact plain dictionary with exactly these keys:

- `schema`: the schema above
- `provenance`: `PUBLIC_SYNTHETIC`
- `seed`: native unsigned 64-bit integer
- `agents`: 1..64 unique non-empty names, at most 64 characters
- `steps`: 1..10,000 step dictionaries

Each step has exactly `tick, observations, actions, rewards, terminations, truncations`. Ticks are strictly increasing native integers in the signed 63-bit non-negative range. Every per-agent map must contain exactly the declared agent set. Observation and action values are finite native scalar numbers or vectors of 1..256 finite native numbers. Rewards are finite native numbers. Termination and truncation values are exact booleans.

Agent names are sorted during normalization. Each accepted frame is detached into tuples; the frozen receipt contains a canonical SHA-256 consistency digest. The digest is not authentication, provenance proof, or a signature.

## Limits and safety

- Python standard library only; no new mandatory dependencies.
- Aggregate admission is capped at 100,000 scalar values across the complete episode, in addition to per-vector, per-agent and per-step limits.
- Caller passes an in-memory object; this module performs no filesystem or network access.
- Exact built-in containers and primitive numeric types only; booleans are not numeric values.
- Rejected input does not mutate caller-owned objects.
- The validator does not infer missing agents, pad observations, reorder action semantics, or synthesize rewards.
- This record is not wired into SQLite journals, GENESIS, policy identity, or canonical Graph IR.
- No PRIVATE Noosphere records or real hardware/control signals are accepted.

## Test

```sh
PYTHONPATH=src python -m unittest tests.test_pettingzoo_episode -v
```

The suite exercises deterministic normalization/digests, detachment, malformed and hostile inputs, exact agent coverage, numeric bounds, monotonic ticks, and input immutability.

## Upstream and license note

The adapter boundary is API-shaped for PettingZoo's multi-agent environment family, but this change vendors no PettingZoo code and adds no dependency. If a future adapter installs PettingZoo, pin and review the exact release and its upstream license independently before distribution. This module makes no compatibility claim for any particular PettingZoo release.
