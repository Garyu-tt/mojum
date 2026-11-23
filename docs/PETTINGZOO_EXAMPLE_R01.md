# PettingZoo multi-agent benchmark example R0.1

Status: opt-in synthetic benchmark example. This is not integrated with the
Mojum Aquarium, canonical Graph IR, SQLite journals, GENESIS, or Noosphere.

## Scope

The default fixture uses only the Python 3.11+ standard library. It models two
agents that must coordinate on a hidden binary target which remains constant
for one episode. Each agent receives only its identity, the logical step, and
the previous team reward. The target is not present in observations or traces.

The adaptive policy is a transparent win-stay/lose-switch baseline: start with
action 0; after a zero reward switch to the other action; after a positive
reward keep the previous action. The frozen control always chooses action 0.
The benchmark runs both policies against the same seed and reports aggregate
rewards and deterministic per-step traces. This is a tiny controlled spike,
not evidence of general multi-agent learning or intelligence.

## Offline execution

From the repository root:

    python -m unittest discover -s tests -p test_pettingzoo_example.py -v
    python examples/pettingzoo/benchmark.py --seed 0 --episodes 20 --horizon 10

The CLI emits one canonical-key-order JSON document. Seeds must be unique;
episodes are limited to 1..1000 and horizon to 1..256. No network, filesystem,
SQLite, private state, device I/O, or third-party imports are used by the
offline path.

## Optional PettingZoo adapter

The adapter is created only when make_pettingzoo_env() is called. The
reference integration is deliberately pinned to:

- pettingzoo==1.24.3 (MIT)
- gymnasium==0.29.1 (MIT)

Install these only in a dedicated optional environment:

    python -m pip install "pettingzoo==1.24.3" "gymnasium==0.29.1"

Then:

    from examples.pettingzoo.benchmark import make_pettingzoo_env

    env = make_pettingzoo_env(horizon=10)
    observations, infos = env.reset(seed=123)
    actions = {agent: 0 for agent in env.agents}
    observations, rewards, terminations, truncations, infos = env.step(actions)
    env.close()

The adapter implements the PettingZoo Parallel API shape, with discrete binary
actions, per-agent observations, rewards, terminations, truncations, and infos.
Run PettingZoo's parallel_api_test in the optional environment before using
it as an upstream integration. This repository's required CI does not install
either optional package and does not claim that upstream conformance test ran.

## Limits and safety

- CPU-only, two-agent, two-action, fully synthetic toy environment.
- Fixed hidden target per episode; no external environment or asset ingestion.
- The optional adapter is not automatically installed or wired into runtime.
- No PRIVATE Noosphere, real body state, host identifiers, credentials,
  network access, physical control, or production deployment.
- Rewards are a benchmark score only, not a welfare, safety, or capability
  metric.
- The seed determines the environment's hidden target; do not treat seed
  secrecy as a security boundary.
- PettingZoo/Gymnasium versions are pinned for reproducibility, not asserted
  to be the latest releases. Reassess upstream license and API before upgrading.
