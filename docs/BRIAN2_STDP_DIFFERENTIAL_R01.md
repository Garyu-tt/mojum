# Brian2 pair-STDP differential spike benchmark R0.1 (#691)

Status: **experimental, opt-in, PUBLIC_SYNTHETIC only**. Independent from #739's rate-population shadow study. This is a paired spike-timing plasticity proof, not a neural substrate, motor controller, developmental brain, or real organism learning outcome.

## Delivered

- `mojum.brian2_stdp.parse_case`: strict `mojum.neural.stdp-differential/1` wire, immutable validated spike events, finite bounded parameters, PUBLIC_SYNTHETIC-only marker, unknown-field refusal.
- `reference_trace`: pure Python stdlib, deterministic event-exact exponential Apre/Apost decay, bounded weights, separate pre and post clock events.
- `brian2_trace`: explicit separately installed Brian2 `Synapses` and `SpikeGeneratorGroup`, event-driven traces, NumPy codegen, per-event measurement, no native C++ compilation. Missing or mismatched native dependency fails, never silently falls back.
- `compare_native`: compares EVERY spike weight with a default 1e-8 tolerance, not just final weight. Simultaneous pre/post spikes are rejected to avoid scheduler-order bias.

Bounds: 1..256 synthetic spikes, strictly increasing ticks 1..4096, dt 0.1..10 ms, tau 0.1..1000 ms, weight in [0,1]. No input files, command execution, API network, PRIVATE Noosphere access, sensor data, journal migrations, body/motor authority, privileged future state, or snapshot modifications.

## Reproduction

Zero-dependency Python 3.11+ baseline:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_brian2_stdp.py' -v
```

Optional real Brian2 validation in a dedicated virtual environment:

```sh
python -m pip install 'brian2==2.9.0'
PYTHONPATH=src python -m unittest discover -s tests -p 'test_brian2_stdp.py' -v
```

Without Brian2, the two native tests are marked **SKIPPED**: this is NOT native integration proof. The dedicated GitHub Actions workflow installs pinned Brian2 and must complete native tests without skips. No default dependencies were added.

## Upstream/license assessment

Brian2 upstream <https://github.com/brian-team/brian2>; pinned test dependency **2.9.0**, license **CeCILL-2.1**. Consult redistribution and linking/combination obligations before packaging native dependencies. Official public STDP model: <https://brian2.readthedocs.io/en/2.9.0/examples/synapses.STDP.html>. The code is an independent formulation of pair-trace STDP and does not vendor upstream sources or neural models.

The optional runner sets `prefs.codegen.target='numpy'` temporarily. Brian2 preferences are process-global; run the native test in a separately isolated worker, never in a multitenant runtime process. The tested input is PUBLIC_SYNTHETIC and contains no identifying data, model assets, prompts, credentials or paths.

The hard bounds are <=4097 simulated time steps, <=256 events and a single synapse with one monitored weight. They are NOT a measured CPU/RAM budget. Capture peak RSS, wall time and OS/Python version in independent QA on the exact PR HEAD.

## Remaining gates

1. Native Brian2 STDP tests, full CI and independent exact-head QA before merge.
2. No actual GENESIS populations, online synaptic state export/import or Noosphere checkpoint/replay hooks are present.
3. ANNarchy compiled-CPU comparison, nondeterministic scheduler evaluation and further plasticity rules remain separate tasks.
4. This PR must not be treated as permission to run a native backend in a live organism or migrate existing journal data.
