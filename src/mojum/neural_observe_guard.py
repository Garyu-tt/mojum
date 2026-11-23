"""Strict opt-in PUBLIC_SYNTHETIC ingress guard for PulsePredictor.observe.

This adapter validates and detaches a tiny allowlisted sensory frame before
calling the existing learner. It does not authenticate caller provenance or
change the learner's model/update semantics.
"""
from .neural import PulsePredictor

SCHEMA = "mojum.neural.observe-guard/1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"
MAX_TIMESTAMP_NS = (1 << 63) - 1
_MAX_BEAT_INDEX = (1 << 63) - 1
_SENSORY_KEYS = frozenset(("kind", "observed_at_ns", "heartbeat"))
_HEARTBEAT_KEYS = frozenset(("beat_index", "phase_milli", "pulse", "synthetic"))


class NeuralObserveGuardError(ValueError):
    """Raised when an opt-in synthetic sensory frame is not admissible."""


def _exact_keys(value, expected, label):
    if type(value) is not dict:
        raise NeuralObserveGuardError(f"{label} must be a native dict")
    # Check native key types before set/equality; hostile str subclasses may
    # execute user code from hashing or equality operations.
    for key in dict.keys(value):
        if type(key) is not str:
            raise NeuralObserveGuardError(f"{label} keys must be native strings")
    if set(dict.keys(value)) != expected:
        raise NeuralObserveGuardError(f"{label} has missing or unknown fields")


def observe_public_synthetic(predictor, sensory, *, visibility):
    """Validate one explicit public-synthetic interoception frame and observe.

    Scalar fields are copied into fresh builtin dictionaries. This avoids
    passing caller-owned containers, subclasses, or custom mapping methods to
    the canonical predictor. The visibility string is a caller assertion, not
    proof of provenance, consent, or permission to access private data.
    """
    if type(predictor) is not PulsePredictor:
        raise NeuralObserveGuardError("exact PulsePredictor instance required")
    if type(visibility) is not str or visibility != PUBLIC_SYNTHETIC:
        raise NeuralObserveGuardError("explicit PUBLIC_SYNTHETIC declaration required")

    _exact_keys(sensory, _SENSORY_KEYS, "sensory frame")
    if dict.__getitem__(sensory, "kind") != "interoception":
        raise NeuralObserveGuardError("interoception frame required")
    observed_at_ns = dict.__getitem__(sensory, "observed_at_ns")
    if type(observed_at_ns) is not int or not 0 <= observed_at_ns <= MAX_TIMESTAMP_NS:
        raise NeuralObserveGuardError("observed_at_ns must be a bounded nonnegative integer")

    heartbeat = dict.__getitem__(sensory, "heartbeat")
    _exact_keys(heartbeat, _HEARTBEAT_KEYS, "heartbeat")
    beat_index = dict.__getitem__(heartbeat, "beat_index")
    phase_milli = dict.__getitem__(heartbeat, "phase_milli")
    pulse = dict.__getitem__(heartbeat, "pulse")
    synthetic = dict.__getitem__(heartbeat, "synthetic")

    if type(beat_index) is not int or not 0 <= beat_index <= _MAX_BEAT_INDEX:
        raise NeuralObserveGuardError("beat_index must be a bounded nonnegative integer")
    if type(phase_milli) is not int or not 0 <= phase_milli < 1000:
        raise NeuralObserveGuardError("phase_milli must be an integer in [0, 1000)")
    if type(pulse) is not bool:
        raise NeuralObserveGuardError("pulse must be a native bool")
    if type(synthetic) is not bool or synthetic is not True:
        raise NeuralObserveGuardError("heartbeat must be explicitly synthetic")

    admitted = {
        "kind": "interoception",
        "observed_at_ns": observed_at_ns,
        "heartbeat": {
            "beat_index": beat_index,
            "phase_milli": phase_milli,
            "pulse": pulse,
            "synthetic": True,
        },
    }
    return predictor.observe(admitted)
