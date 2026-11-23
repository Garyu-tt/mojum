"""Opt-in ANNarchy/Brian2 rate-population comparison for GENESIS shadow research.

No third-party imports until explicitly invoked. No organism state, journal,
actuator or network in the stdlib path. Run optional engines only in a
resource-capped disposable process. NOT an active neural/motor backend.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import tempfile

SCHEMA = "mojum.neural.rate-shadow-probe/1"
MAX_NEURONS = 16
MAX_STEPS = 512
ANNARCHY_VERSION = "5.0.4.1"
BRIAN2_VERSION = "2.11.0"


class ProbeError(ValueError):
    """Invalid sample, unsafe mode or incompatible backend."""


def _finite(value):
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RateSpec:
    neurons: int = 3
    dt_ms: float = 1.0
    tau_ms: float = 10.0

    def __post_init__(self):
        if type(self.neurons) is not int or not 1 <= self.neurons <= MAX_NEURONS:
            raise ProbeError("neuron count exceeds CPU budget")
        if (not _finite(self.dt_ms) or not _finite(self.tau_ms)
                or not 0.1 <= self.dt_ms <= 1.0
                or not 2.0 <= self.tau_ms <= 1000.0
                or self.dt_ms >= self.tau_ms):
            raise ProbeError("invalid fixed time step or time constant")


def _inputs(frames, spec):
    if type(frames) not in (tuple, list) or not 1 <= len(frames) <= MAX_STEPS:
        raise ProbeError("invalid or unbounded input frames")
    result = []
    for frame in frames:
        if type(frame) not in (tuple, list) or len(frame) != spec.neurons:
            raise ProbeError("wrong population shape")
        if not all(_finite(v) and 0.0 <= v <= 1.0 for v in frame):
            raise ProbeError("invalid synthetic input")
        result.append(tuple(float(v) for v in frame))
    return tuple(result)


class RateShadow:
    """Deterministic Euler reference, with shadow-only observations."""

    def __init__(self, spec=RateSpec()):
        if not isinstance(spec, RateSpec):
            raise ProbeError("rate spec required")
        self.spec = spec
        self._tick = 0
        self._rates = tuple(0.0 for _ in range(spec.neurons))

    def step(self, inputs):
        frame = _inputs([inputs], self.spec)[0]
        if self._tick >= MAX_STEPS:
            raise ProbeError("reference step budget exhausted")
        gain = self.spec.dt_ms / self.spec.tau_ms
        rates = tuple(old + gain * (drive - old)
                      for old, drive in zip(self._rates, frame))
        if not all(_finite(v) and 0 <= v <= 1.0 for v in rates):
            raise ProbeError("numerical instability")
        self._tick += 1
        self._rates = rates
        return {"schema": SCHEMA, "tick": self._tick, "rates": list(rates),
                "backend": "stdlib-euler-reference", "mode": "shadow",
                "motor_intention": None, "physical_contact": False}

    def snapshot(self):
        value = {"schema": SCHEMA, "tick": self._tick,
                 "spec": {"neurons": self.spec.neurons,
                          "dt_ms": self.spec.dt_ms, "tau_ms": self.spec.tau_ms},
                 "rates": list(self._rates)}
        return {**value, "sha256": _digest(value)}

    @classmethod
    def restore(cls, value):
        if type(value) is not dict or set(value) != {"schema", "tick", "spec", "rates", "sha256"}:
            raise ProbeError("invalid shadow snapshot")
        payload = {key: value[key] for key in ("schema", "tick", "spec", "rates")}
        if value["schema"] != SCHEMA or type(value["sha256"]) is not str:
            raise ProbeError("wrong shadow schema")
        try:
            if type(value["spec"]) is not dict or set(value["spec"]) != {"neurons", "dt_ms", "tau_ms"}:
                raise ProbeError("invalid spec")
            spec = RateSpec(**value["spec"])
            tick, rates = value["tick"], value["rates"]
            if (type(tick) is not int or not 0 <= tick <= MAX_STEPS
                    or type(rates) is not list or len(rates) != spec.neurons
                    or not all(_finite(r) and 0.0 <= r <= 1.0 for r in rates)
                    or (tick == 0 and any(r != 0 for r in rates))):
                raise ProbeError("invalid snapshot state")
            if _digest(payload) != value["sha256"]:
                raise ProbeError("shadow snapshot checksum mismatch")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProbeError("invalid shadow snapshot") from exc
        obj = cls(spec)
        obj._tick, obj._rates = tick, tuple(float(r) for r in rates)
        return obj


def _simulate_annarchy(frames, spec):
    try:
        import ANNarchy as ann
    except ImportError as exc:
        raise ProbeError("ANNarchy is optional and not installed") from exc
    if getattr(ann, "__version__", None) != ANNARCHY_VERSION:
        raise ProbeError("ANNarchy version not pinned: expected " + ANNARCHY_VERSION)
    # ANNarchy builds C++: bound the PROCESS outside Python, not just this data.
    with tempfile.TemporaryDirectory(prefix="mojum-annarchy-probe-") as work:
        neuron = ann.Neuron(
            parameters={"tau": float(spec.tau_ms),
                        "drive": ann.Parameter(0.0, locality="local")},
            equations="tau * dr/dt + r = drive : explicit",
        )
        # Match the Euler reference: ANNarchy defaults to a 1 ms network dt.
        net = ann.Network(dt=float(spec.dt_ms))
        pop = net.create(spec.neurons, neuron)
        net.compile(directory=work)
        result = []
        for vector in frames:
            pop.drive = list(vector)
            net.simulate(float(spec.dt_ms))
            result.append(tuple(float(v) for v in pop.r))
    return tuple(result)


def _simulate_brian2(frames, spec):
    try:
        import brian2 as b2
    except ImportError as exc:
        raise ProbeError("Brian2 is optional and not installed") from exc
    if getattr(b2, "__version__", None) != BRIAN2_VERSION:
        raise ProbeError("Brian2 version not pinned: expected " + BRIAN2_VERSION)
    # start_scope and defaultclock are global: disposable process only.
    b2.start_scope()
    previous_dt = b2.defaultclock.dt
    previous_target = b2.prefs.codegen.target
    try:
        b2.defaultclock.dt = spec.dt_ms * b2.ms
        b2.prefs.codegen.target = "numpy"
        group = b2.NeuronGroup(
            spec.neurons,
            "dr/dt = (drive - r) / (tau * ms) : 1\n"
            "drive : 1\n"
            "tau : 1",
            method="euler",
        )
        group.r = 0
        group.tau = spec.tau_ms
        result = []
        for vector in frames:
            group.drive = list(vector)
            b2.run(spec.dt_ms * b2.ms)
            result.append(tuple(float(v) for v in group.r[:]))
        return tuple(result)
    finally:
        b2.defaultclock.dt = previous_dt
        b2.prefs.codegen.target = previous_target


def compare_rate_traces(frames, *, spec=RateSpec(), backend="reference",
                        enabled=False, tolerance=1e-5):
    """Bounded PUBLIC synthetic input replay. No motor commands or private data."""
    if enabled is not True:
        raise ProbeError("explicit opt-in required")
    if backend not in ("reference", "annarchy", "brian2"):
        raise ProbeError("unsupported neural backend")
    if not _finite(tolerance) or not 0.0 <= tolerance <= 0.01:
        raise ProbeError("invalid numerical tolerance")
    if not isinstance(spec, RateSpec):
        raise ProbeError("rate spec required")
    inputs = _inputs(frames, spec)
    fixture = RateShadow(spec)
    expected = tuple(tuple(fixture.step(row)["rates"]) for row in inputs)
    if backend == "reference":
        actual = expected
    elif backend == "annarchy":
        actual = _simulate_annarchy(inputs, spec)
    else:
        actual = _simulate_brian2(inputs, spec)
    if len(actual) != len(expected):
        raise ProbeError("backend trace length mismatch")
    error = 0.0
    for observed, reference in zip(actual, expected):
        if len(observed) != spec.neurons:
            raise ProbeError("backend population shape mismatch")
        for found, wanted in zip(observed, reference):
            if not _finite(found) or not 0 <= found <= 1.0:
                raise ProbeError("backend returned unsafe firing rate")
            error = max(error, abs(float(found) - wanted))
    if error > tolerance:
        raise ProbeError("dynamics diverged from independent Euler reference")
    return {"schema": SCHEMA, "mode": "shadow", "backend": backend,
            "tick_count": len(inputs), "neurons": spec.neurons,
            "dt_ms": spec.dt_ms, "tau_ms": spec.tau_ms,
            "input_sha256": _digest(inputs), "reference_sha256": _digest(expected),
            "observed_sha256": _digest(actual), "final_rates": list(actual[-1]),
            "max_abs_error": error, "motor_intention": None, "physical_contact": False}
