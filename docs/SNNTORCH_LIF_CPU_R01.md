# #691 optional snnTorch CPU LIF research spike, R0.1

**Separate ownership:** ANNarchy/Brian2 population-rate shadow PR #739, Nengo LIF and Brian2 STDP are separate peer tasks. This slice alone tests a potential *alternate* snnTorch-based CPU spiking reference, and does not activate the GENESIS motor policy.

## Dependency and model contract

- Upstream: `snntorch==1.0.0` (MIT license, PyPI release 2026-06-29), built on PyTorch. Source/docs: https://snntorch.readthedocs.io/en/latest/snn.neurons_leaky.html ; https://pypi.org/project/snntorch/ . No code/weights copied into Mojum. PyTorch/transitive dependencies need separate platform/licensing/security review; no automatic pip install.
- Off by default, zero core dependencies. `reference_trace()` uses stdlib only. `run_snntorch(..., enabled=True, expected_torch_version="<installed exact version>")` *lazily* imports existing local packages and refuses mismatched pinned versions.
- Inputs: exact `mojum.neural.snntorch-lif-probe/1`, `PUBLIC_SYNTHETIC`, 1–128 time steps, 1–8 neurons, finite scalar currents 0–2, beta 0.05–0.95, threshold 0.25–2. All other fields, including any motor/sensor payload, rejected. Fixed discrete time, no physical biological time inferred. No model training, persistence, audio, files or network.
- LIF dynamics: `snntorch.Leaky` with `reset_delay=True`, `reset_mechanism="subtract"`, `surrogate_disable=True`, exact tick order, strict threshold. Reference uses Python binary64; native uses CPU torch float32. Every spike must agree exactly, each membrane within `5e-5` absolute; otherwise reject. This is not ANNarchy equivalence or a trained brain.
- State is a **checksum-only, not authenticated** `mojum.neural.snntorch-lif-state/1` checkpoint. It cannot authorize a canonical Noosphere resume, and offers no protection against malicious rewriting/re-hashing. Sampled tests prove deterministic synthetic replay and tamper detection, not arbitrary journal-safe persistence.
- Resource budget per call: at most `128 x 8 = 1024` scalar neuron-steps, 1 forward-only CPU neuron layer, no model I/O, no writes. Python limits are not a RAM/time sandbox: production/spike runner **must** execute in a disposable process with OS-enforced CPU, RSS and wall-clock budgets, no egress, no credentials, and a separate acceptance gate. Never run with PRIVATE organism signals.

## Reproduce offline first

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_snntorch_lif_probe.py' -v
PYTHONPATH=src python -m mojum.snntorch_lif_probe
```

An isolated environment may opt into `snntorch==1.0.0` plus an explicitly pinned CPU-only torch wheel, installed **outside this repository** after owner dependency review, to run the conditional native test. The `test_optional_native_if_installed` method otherwise reports SKIP. Only a real installed native pass and exact-head CI would count as native engine evidence. Neither snnTorch nor PyTorch is installed by this PR.

## Failure acceptance

Cases covered: disabled native gate, missing exact PyTorch version, PRIVATE/unknown schema, ragged/oversized/NaN/bool/negative currents, malformed snapshots, budget overflow with unchanged state, tampered checkpoint, native spike mismatch and membrane mismatch. No child-process timeout, native engine CI, physical output, STDP or independent QA is claimed. Draft PR should remain unmerged until gate review, safe dependency installation plan and exact-head CI.
