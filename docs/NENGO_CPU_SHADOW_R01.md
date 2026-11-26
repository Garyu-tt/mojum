# Nengo CPU shadow integration probe (issue #691, independent Nengo slice)

**Status:** experimental, PUBLIC SYNTHETIC only, not approved for the organism runtime.

This is a small real Nengo LIF population probe with a stdlib-only independent
leaky-integrate-and-fire control. It does **not** connect to GENESIS, Noosphere,
Aquarium, body/joints, actuator outputs, user audio, external sensors, or the
production scheduler. Its only output is bounded anonymous aggregate metadata:
a total spike count, active tick count and SHA-256 of one count per tick.

## Default and opt-in commands

The normal Mojum Python 3.11+ stdlib installation imports no Nengo dependency.

Run the no-dependency control:

    PYTHONPATH=src python3 -m mojum.nengo_shadow --backend stdlib --execute --steps 128 --neurons 16 --seed 7

Create an **isolated optional** environment yourself, then invoke the actual
engine (no auto-install or download occurs inside the probe):

    python3 -m venv /tmp/mojum-nengo-r01
    /tmp/mojum-nengo-r01/bin/python -m pip install 'nengo==4.1.0' 'numpy<3'
    PYTHONPATH=src /tmp/mojum-nengo-r01/bin/python -m mojum.nengo_shadow --backend nengo --execute --steps 128 --neurons 16 --seed 7

Both commands require --execute. The Python API run_nengo requires enabled=True.
An absent, unexpected or mismatched Nengo distribution fails closed; no
fallback substitutes the stdlib control while claiming to be Nengo.
Tests:

    PYTHONPATH=src python3 -m unittest tests.test_nengo_shadow -v

Repeat with the optional environment interpreter to exercise real Nengo.

## Wire and measured behavior

- Schema: mojum.nengo-shadow-spike-fixture/1
- Source: PUBLIC_SYNTHETIC_ONLY, immutable built-in pulse; no arbitrary
  callbacks, file paths, user prompts, credentials, private data or network.
- Mode: shadow_only, with no motor command or private state export.
- Limits: 1..64 neurons, 1..512 steps, exactly 1 ms per step, exact integer
  seed 0..2^31-1. Maximum raw spike occupancy 32768 neuron-ticks, but
  individual neuron traces are not returned or persisted.
- Model: 1D synthetic stimulus driving a real Nengo LIF Ensemble and read-only
  neural spike Probe. Stimulus is 1 in first half, 0 in second half.
- Stdlib control is a separate simple discrete LIF equation, **not** a
  numerically matched Nengo model and **not** Brian2/ANNarchy validation.
  Distinct seeds alter the stdlib test control's intrinsic gains.
- Determinism: SHA-256 over JSON-encoded per-step aggregate spike bins, never
  a claim of cross-platform bitwise identity. Test replay on the **same**
  interpreter/dependency versions. Nengo engine version identity is explicit.
- No recorded synapse learning, online plasticity, trajectory imitation,
  runtime checkpoint/restore, interoception wiring or active proposals yet.

Observed **local test** on Python 3.13.5 with Nengo 4.1.0 and NumPy 2.5.5:
16 neurons, 128 steps, seed 7 => 227 spike events, 113 active ticks,
SHA-256 b49275dfb83502061381f765ff371c32b7b63691eb354399f7b1ca5163564717.
On the same isolated test host, one fresh-process CLI invocation measured
0.557 seconds wall time and 43,152 KiB child peak RSS (including Python, Nengo
and NumPy imports). These are fixture facts, not a realistic brain throughput
benchmark. Record host-specific CPU/RSS before choosing production sizes; work
is bounded by the caps above. Do not infer that 64 neurons represent the GENESIS
brain or that this can safely drive physical hardware.

## Upstream/authority boundaries

Nengo 4.1.0: https://pypi.org/project/nengo/4.1.0/
Upstream source/license: https://github.com/nengo/nengo
Nengo changed its licensing to GPLv2 in 4.0.0; review the exact bundled
license and all transitive/redistribution obligations **before** integrating
or distributing this dependency. Mojum's optional registry stays
status=planned and license_review=required. No dependency is promoted
or added to pyproject.toml by this PR.

Failure checklist for independent QA: opt-in rejected by default;
booleans/floats, negative/oversized steps/neurons/seeds rejected;
missing or different Nengo version rejected; repeat same-version trace;
verify no Noosphere/Aquarium writes, real actuator calls or network paths.
Do not merge without exact-head 3x3 CI and an independent review.
