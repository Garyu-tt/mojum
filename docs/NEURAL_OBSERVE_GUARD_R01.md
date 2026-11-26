# Neural observe ingress guard R0.1

## Purpose

The module mojum.neural_observe_guard is an opt-in, standard-library-only admission wrapper for the existing PulsePredictor.observe boundary. It accepts one explicitly caller-declared PUBLIC_SYNTHETIC interoception frame, checks its complete allowlisted shape and scalar types, copies it into detached native dictionaries, and delegates to the unchanged predictor.

## Contract

- Requires the exact PulsePredictor class, not a subclass or duck-typed object.
- Requires an explicit visibility="PUBLIC_SYNTHETIC" argument. There is no default.
- Outer frame keys are exactly kind, observed_at_ns, and heartbeat.
- Heartbeat keys are exactly beat_index, phase_milli, pulse, and synthetic.
- Rejects dict subclasses, non-native keys/scalars, unknown/missing fields, bool-as-int, invalid ranges and timestamps outside the signed-64-bit nonnegative range.
- Does not retain or mutate caller-owned frame dictionaries. The existing predictor remains the authority for monotonic timestamp admission and learning math.

## Example

~~~python
from mojum.neural import PulsePredictor
from mojum.neural_observe_guard import observe_public_synthetic

predictor = PulsePredictor()
frame = {
    "kind": "interoception",
    "observed_at_ns": 0,
    "heartbeat": {
        "beat_index": 0,
        "phase_milli": 0,
        "pulse": True,
        "synthetic": True,
    },
}
result = observe_public_synthetic(
    predictor, frame, visibility="PUBLIC_SYNTHETIC"
)
~~~

## Security and interpretation limits

PUBLIC_SYNTHETIC is a caller assertion, not authenticated provenance, consent, or a cryptographic trust proof. The adapter does not read or write SQLite, PRIVATE Noosphere records, files, network, devices or hardware. It is not integrated into Aquarium and is not an active neural policy. A valid frame does not establish consciousness, cognition, biological physiology or safe motor authority.

## Verification

Run:

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neural_observe_guard.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neural.py' -v
~~~

Exact-head GitHub Actions CI and independent review remain required before merge.
