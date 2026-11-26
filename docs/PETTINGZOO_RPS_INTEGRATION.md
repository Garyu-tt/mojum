# PettingZoo RPS benchmark (issue #700, slice c)

**Status:** opt-in research benchmark only. No organism learning, private Noosphere access, hardware signals, real multi-agent fleet or release migration. Never auto-install upstream. Zero new mandatory Mojum dependencies.

## Third-party boundary

- Upstream: [Farama PettingZoo](https://github.com/Farama-Foundation/PettingZoo), **exact `1.27.0`** (2026-08-13 release), [MIT license](https://github.com/Farama-Foundation/PettingZoo/blob/main/LICENSE). PettingZoo's indirect dependencies have independent licenses and must be reviewed before deployment. No upstream code or assets are vendored.
- API: [Parallel](https://pettingzoo.farama.org/api/parallel/) registry entry [`classic/rps-v2`](https://pettingzoo.farama.org/environments/classic/rps/), specifically two simultaneous players, 3 legal integer actions and 15 cycles.
- Optional local setup in a disposable Python 3.11+ venv: `python -m pip install 'pettingzoo[classic]==1.27.0'`. This requires network only to install; benchmark itself has no network calls.
- Explicit run: `python -m mojum.pettingzoo_benchmark --execute`. Without `--execute`, invocation fails closed. The module is never imported by the normal Aquarium launcher.
- Strict version check. Unsupported or missing PettingZoo fails closed, not silently downgraded.

## Experiment, nonclaims and limits

Two frozen and online-transition strategies face the **same scripted cyclic opponent**, on two pinned held-out seeds, `11` and `29`, with **new independent environments per episode**. The online policy builds a previous-opponent-action transition table from observations visible *after* each completed game. It never sees the current opponent's upcoming action, secret global state, environment `state()`, or reward oracle. Frozen control always plays action 0. The loop validates action spaces, agent count/order, reset/step signatures, zero-sum finite game rewards, terminal flags, step limit and scalar observation contract. Outputs contain only score, count, identifiers for this public benchmark and SHA-256 of public actions/rewards/terminal flags. The SHA is reproducibility evidence, **not authentication**.

This is **not a fair unknown-opponent benchmark**: the cycle is intentionally predictable and seeded runs share the same scripted schedule. Better online reward demonstrates an observation-to-action integration path, **not general intelligence, transfer to Mojum, or organism competence**. No private prompts, histories, IDs, journal paths or environment `infos` are serialized. The caller should never inject private state into a user-supplied environment factory. No filesystem writes, no subprocesses and no external connections are performed by this module.

Resource bound per run: 4 episodes × 15 cycles = 60 two-player steps (120 player actions), two short-lived environments per seed, max 64 steps per single episode, no persistent state beyond a 3×3 small integer table and constant-size SHA-256 digest. Actual PettingZoo package footprint, latency and RSS are **not measured** here: measure on target CPU before acceptance. It is not a claim of hard OS sandboxing; execute third-party code in an isolated worker to enforce filesystem/network restrictions.

## Verification

- Offline fixture: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_pettingzoo_benchmark.py' -v`
- Exact-upstream smoke with separately installed dependency: same test command runs the gated real `classic/rps-v2` test and compares reproducible runs and frozen vs online scores.
- Dedicated Actions workflow `pettingzoo-rps-qa.yml` provides stdlib CPython 3.11/3.13 offline tests plus isolated upstream 1.27.0 smoke. CI must be checked on the **exact final head**; draft PR requires independent review before merge.
- Existing SQLite, GENESIS policy, graph schema and canonical Aquarium code are unchanged.
