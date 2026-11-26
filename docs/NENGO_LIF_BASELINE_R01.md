# Opt-in Nengo LIF CPU research baseline (#691)

**Status:** experimental, isolated, default off. This is a **Nengo alternative** to the peer-owned ANNarchy/Brian2 population shadow work (PR #739) and the typed backend wire (PR #705). It is not a GENESIS brain, policy, checkpoint, Cezar task, authoritative journal, learning claim, or `Aquarium` movement source. No data enters Noosphere.

## How it runs

- `mojum.nengo_lif_baseline` imports **stdlib only** on its reference path. It accepts an explicitly constructed immutable `LIFSpec` with `mojum.neural.nengo-lif-fixture/1`, a rectangular tuple of **synthetic scalar input currents**. No file, network, device, identity, path, agent record, or arbitrary callback is permitted by the interface. Never pass PRIVATE-derived currents or a real sensor stream; numeric-only validation **cannot** establish provenance.
- `run_reference(spec)` is a minimal *explicit-Euler* leaky integrate-and-fire control. Its numerical spike timestamps are **not guaranteed** to equal Nengo's more accurate solver. `run_nengo(spec)` imports **actual Nengo** only on explicit invocation, checks the exact `4.1.0` pin, calls its public `nengo.LIF.step` CPU neuron kernel, and stores bounded 0/1 spike events. It does not build a `nengo.Network`, use `nengo.Simulator`, install dependencies, or pass data back into GENESIS.
- `compare(spec)` reports measured *differences* in output spike rates. It always returns `approved=False`, never automatically selects a backend or labels rate similarity as correctness. Replay-test each engine against itself with the same fixed fixture; no randomized seeds or network weights.
- Bounds: **32 neurons**, **4096 steps**, **32768 neuron-steps**, currents **0..5**, dt **0.5..2 ms**, membrane constant **4..100 ms**, refractory **0..20 ms**. Refuses ragged, non-finite, unexpected-schema and oversized inputs before third-party import. No background execution, timeouts, or OS-level CPU/RAM quotas: an orchestrator must supply an external sandbox and budget before non-test deployment.

## Optional install (dedicated disposable environment only)

Nengo is **NOT a package requirement of Mojum**. For independent license-approved evaluation only:

```sh
python -m venv /tmp/mojum-nengo-test
. /tmp/mojum-nengo-test/bin/activate
python -m pip install 'nengo==4.1.0'
PYTHONPATH=src python -m unittest discover -s tests -p 'test_nengo_lif_baseline.py' -v
PYTHONPATH=src python - <<'PY'
from mojum.nengo_lif_baseline import fixture, compare
print(compare(fixture(1000)))  # synthetic current; never PRIVATE data
PY
```

Nengo 4.1.0 published May 29, 2025, requires Python >=3.8 and declares **GPLv2**. This is a material distribution/combination review gate for Mojum's currently undecided repository license; no vendored third-party code and no automatic install or production enablement. Review [PyPI release](https://pypi.org/project/nengo/4.1.0/), [upstream license](https://github.com/nengo/nengo/blob/main/LICENSE.rst), native dependency graph, CPU budgets, ARM support and QA evidence before promotion. Do not infer that earlier Nengo or NengoDL licenses apply to this pin.

## QA / acceptance limits

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_nengo_lif_baseline.py' -v
```

The native test is **skipped** if Nengo is absent; offline unit tests exercise the optional binding against a small fake to validate the adapter contract, **not** the real native implementation. Real CPU benchmark, exact-head CI, independent QA, backends' numerical tolerances across platforms, licensing approval, complete GENESIS wire mapping, checkpoint/export/import, LIF STDP and a real full Nengo `Simulator` remain explicitly **unverified**. This is an isolated spike, not issue closure.
