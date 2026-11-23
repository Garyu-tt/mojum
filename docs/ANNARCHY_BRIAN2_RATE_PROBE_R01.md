# ANNarchy / Brian2 CPU rate-population shadow spike (issue #691)

**Status: experimental, opt-in, NOT a working GENESIS backend.** This branch
adds a disposable neural-rate **numerical proof**, not organism behavior. It
does not replace existing neural.py, touch canonical Noosphere journals,
modify graph/motor contracts, compile on normal import, make network requests,
install a dependency, actuate hardware or teach the organism.

## Scope and version pins (review required)

- [ANNarchy 5.0.4.1](https://pypi.org/project/ANNarchy/5.0.4.1/),
  released 2026-09-15, GNU GPL v2 or later, generated C++/OpenMP compiler.
- [Brian2 2.11.0](https://pypi.org/project/Brian2/2.11.0/),
  released 2026-10-23, CeCILL 2.1, reference CPU numpy target.
  Brian2 2.11 needs **Python 3.13+**, unlike Mojum core Python 3.11+.
- Third-party dependencies are NEVER added to pyproject.toml. Their precise
  upstream and transitive licenses require independent compatibility approval
  before deployment; the optional integration registry remains "planned".
- ANNarchy 5.0.4.1 uses a simple compiled leaky-rate equation. Brian2
  2.11.0 uses the same forward-Euler ODE. Neither is a validated spiking
  STDP implementation or real biological model.

## Contract

Module: src/mojum/annarchy_rate_probe.py

Version: mojum.neural.rate-shadow-probe/1

- Fixed dt 0.1..1 ms, tau 2..1000 ms; dt < tau.
- At most 16 independent scalar rate neurons, 512 samples; every synthetic
  drive must be a finite real in [0,1], with exact population width.
- Each step uses **explicit Euler**:
  r_next = r + (dt/tau)*(drive - r). No randomness or hidden future state.
- Only "shadow" outputs. No learned policy, motor command, spatial state,
  game/simulator authorization or production identity.
- Default is off. Caller must pass enabled=True and supply synthetic public
  numeric frames; no generic untrusted dicts or private observations accepted.
- Reference state snapshot has a SHA-256 checksum and a strict schema. This
  protects accidental corruption, not adversarial authorship (no HMAC).
- Native ANNarchy and Brian2 are optional separate runners and reject versions
  other than exact pins. Max absolute numerical deviation defaults to 1e-5.
- This slice does NOT implement STDP, compiler watchdogs, a GENESIS adapter,
  a production restore wire, private-data admission or fully trusted backend
  isolation. These remain acceptance gates for #691.

## Offline stdlib verification

    PYTHONPATH=src python -m unittest discover -s tests -p test_annarchy_rate_probe.py -v

Example using no external simulator:

    PYTHONPATH=src python -c "from mojum.annarchy_rate_probe import compare_rate_traces; print(compare_rate_traces([[0.0,0.5,1.0]]*20, enabled=True))"

No virtual environment, compiler or network needed. Unit tests check
fixed-seed replay, checkpoint integrity, strict caps, fail-closed missing
versions, a fake ANNarchy API boundary and adversarial output divergence.
Fake ANNarchy is NOT proof that real ANNarchy 5.0.4.1 compiles.

## Optional native benchmark (NOT in CI)

Run only in a **disposable, unprivileged, offline container/process** with
CPU, RSS, wall-clock, filesystem and compiler sandbox limits. Bind no host
tokens, organism state, journal, real sensors or hardware. Do not use the
Aquarium process. ANNarchy's JIT C++ compilation can consume CPU/RAM beyond
the bounded input arrays, and these limits are NOT enforceable by the module.

For ANNarchy 5.0.4.1 with a native compiler installed, or Brian2 2.11.0
on Python 3.13+, an operator can explicitly call:

    PYTHONPATH=src python -c "from mojum.annarchy_rate_probe import compare_rate_traces; print(compare_rate_traces([[0.0,0.5,1.0]]*20, enabled=True, backend='annarchy'))"

Change backend to brian2 only in a separate disposable process. Evidence
must include exact Python/library/compiler versions, peak RSS, compile/setup
time, 1/2/4/8/16-neuron wall times, emitted trace hashes, tolerance, and
frozen/no-learning comparisons before considering a real GENESIS interface.

## Acceptance remaining

Actual ANNarchy+Brian2 numerical runs, STDP and online plasticity controls,
serializable native backend state, checkpoint/replay across engine versions,
compiler resource watchdog, privacy/identity audit, and independent CI + QA.
A passing stdlib test is not proof of those features.
