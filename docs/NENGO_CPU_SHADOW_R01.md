# Experimental Nengo CPU shadow adapter (issue #753)

Opt-in Nengo candidate, **not** enabled in Mojum Aquarium or canonical GENESIS.
It does not touch the existing delayed pulse learner, ANNarchy/Brian2 #691/#739
or the proposed typed neural-wire PR #705.

## Upstream, licensing and adoption gate

- Upstream: https://github.com/nengo/nengo ; proposed version **4.1.0**.
- Nengo 4.1.0 is **GPLv2**, subject to explicit legal and transitive-dependency
  review before redistribution or production adoption.
- Package is neither vendored nor declared in default pyproject.toml;
  the release stays stdlib-only and offline.
- This is a PUBLIC synthetic neural-rate **shadow only**; no training, PRIVATE
  Noosphere state, network service, motor authority, or organism actuators.

## What the adapter really does

1. Accept only exact mojum.nengo.rate-fixture/1 fields, PUBLIC_SYNTHETIC
   source; maximum 16 stimuli, 8 neurons, bounded milli gain/bias, integer
   seed and fixed 1-ms dt. Reject unknown fields, non-integers, invalid ranges.
2. On explicit permit_nengo=True and exactly installed Nengo 4.1.0, construct
   actual Nengo LIFRate rate-neuron ensembles, feed each constant synthetic
   stimulus through a Node -> Connection -> Ensemble and probe neuron output.
3. Independently check the final sample against the stdlib closed-form
   LIFRate equation. Fail closed above 0.01-Hz absolute mismatch.
4. Return only rounded public test measurements with SHADOW_ONLY authority,
   motor_intention=null, backend version and input fingerprint.

This does not claim spiking behavior, STDP, Brian2 equivalence, learned
behaviors, complete GENESIS nervous system, persistence, or multi-host runtime.

## Offline tests

From the repository root:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_nengo_shadow.py' -v

Stdlib contract tests always run; real Nengo smoke is SKIPPED if the dependency
is absent. A green default test suite **does not** demonstrate real Nengo.

After upstream licensing approval, in a separate experimental Python venv
install nengo==4.1.0 and run:

    from mojum.nengo_shadow import INPUT_SCHEMA, DT_NS, run_shadow
    fixture = {
        "schema": INPUT_SCHEMA, "source": "PUBLIC_SYNTHETIC",
        "seed": 11, "dt_ns": DT_NS,
        "inputs_milli": [-1000, 0, 500, 1000],
        "gain_milli": [1000, 1500, 2000],
        "bias_milli": [2000, 1000, 0],
    }
    result = run_shadow(fixture, permit_nengo=True)
    assert result["motor_intention"] is None
    assert result["max_abs_error_hz"] <= 0.01

Require independent exact-head review and actual runtime on target supported
CPU systems, numerical error inspection, cost benchmark, and license clearance
before changing optional integration registry status from 'planned'.
