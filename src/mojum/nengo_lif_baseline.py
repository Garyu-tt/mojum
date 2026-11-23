"""Opt-in, synthetic-only Nengo LIF research baseline for issue #691.

No Nengo/NumPy import on the default path. This is a bounded independent
reference experiment, NOT a GENESIS brain backend or a physical motor source.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA = "mojum.neural.nengo-lif-fixture/1"
TRACE_SCHEMA = "mojum.neural.nengo-lif-trace/1"
NENGO_PIN = "4.1.0"
MAX_STEPS = 4096
MAX_NEURONS = 32
MAX_CELLS = 32768


@dataclass(frozen=True)
class LIFSpec:
    """Explicit, public/synthetic input current per tick and neuron."""

    currents: tuple[tuple[float, ...], ...]
    dt_s: float = 0.001
    tau_rc_s: float = 0.02
    tau_ref_s: float = 0.002
    schema: str = SCHEMA


@dataclass(frozen=True)
class LIFTrace:
    schema: str
    backend: str
    steps: int
    neurons: int
    events: tuple[tuple[int, ...], ...]
    counts: tuple[int, ...]
    sha256: str


def _float(value: object, name: str, lo: float, hi: float) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be finite real number (not bool)")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite real number (not bool)")
    if not lo <= value <= hi:
        raise ValueError(f"{name} out of range")
    return float(value)


def validate(spec: LIFSpec) -> tuple[int, int]:
    """Fail closed before allocating arrays or importing third-party code."""
    if type(spec) is not LIFSpec or type(spec.schema) is not str or spec.schema != SCHEMA:
        raise ValueError("unsupported Nengo fixture schema")
    dt = _float(spec.dt_s, "dt_s", 0.0005, 0.002)
    tau_rc = _float(spec.tau_rc_s, "tau_rc_s", 0.004, 0.1)
    tau_ref = _float(spec.tau_ref_s, "tau_ref_s", 0.0, 0.02)
    if tau_rc < 4.0 * dt or tau_ref > tau_rc:
        raise ValueError("invalid LIF time constants")
    rows = spec.currents
    if type(rows) is not tuple or not 1 <= len(rows) <= MAX_STEPS:
        raise ValueError("invalid fixture tick count")
    first = rows[0]
    if type(first) is not tuple or not 1 <= len(first) <= MAX_NEURONS:
        raise ValueError("invalid fixture neuron count")
    neurons = len(first)
    if len(rows) * neurons > MAX_CELLS:
        raise ValueError("fixture CPU and memory budget exceeded")
    for row in rows:
        if type(row) is not tuple or len(row) != neurons:
            raise ValueError("fixture must be rectangular")
        for current in row:
            _float(current, "current", 0.0, 5.0)
    return len(rows), neurons


def _trace(backend: str, events: list[tuple[int, ...]], neurons: int) -> LIFTrace:
    counts = tuple(sum(row[i] for row in events) for i in range(neurons))
    stable = json.dumps(events, separators=(",", ":"), ensure_ascii=True)
    return LIFTrace(
        schema=TRACE_SCHEMA,
        backend=backend,
        steps=len(events),
        neurons=neurons,
        events=tuple(events),
        counts=counts,
        sha256=hashlib.sha256(stable.encode("ascii")).hexdigest(),
    )


def run_reference(spec: LIFSpec) -> LIFTrace:
    """Small explicit-Euler LIF control, not claimed equivalent to Nengo's solver."""
    _, neurons = validate(spec)
    voltage = [0.0] * neurons
    refractory = [0.0] * neurons
    events: list[tuple[int, ...]] = []
    dt = float(spec.dt_s)
    alpha = dt / float(spec.tau_rc_s)
    for row in spec.currents:
        spikes = [0] * neurons
        for i, current in enumerate(row):
            if refractory[i] > 0:
                refractory[i] = max(0.0, refractory[i] - dt)
                voltage[i] = 0.0
                continue
            voltage[i] = max(0.0, voltage[i] + alpha * (float(current) - voltage[i]))
            if voltage[i] >= 1.0:
                spikes[i] = 1
                voltage[i] = 0.0
                refractory[i] = float(spec.tau_ref_s)
        events.append(tuple(spikes))
    return _trace("euler-control", events, neurons)


def run_nengo(spec: LIFSpec) -> LIFTrace:
    """Execute the actual Nengo 4.1.0 neuron step, only on explicit call.

    Calls Nengo's low-level LIF.step, not a Nengo Network or Simulator.
    Never imports user plugins or publishes outputs to GENESIS.
    """
    _, neurons = validate(spec)
    try:
        import nengo  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("optional nengo==4.1.0 is not installed") from exc
    if getattr(nengo, "__version__", None) != NENGO_PIN:
        raise RuntimeError("unsupported Nengo version: require exact 4.1.0")
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Nengo's NumPy dependency is missing") from exc
    voltage = np.zeros(neurons, dtype=np.float64)
    refractory = np.zeros(neurons, dtype=np.float64)
    output = np.zeros(neurons, dtype=np.float64)
    neuron = nengo.LIF(tau_rc=float(spec.tau_rc_s), tau_ref=float(spec.tau_ref_s))
    events: list[tuple[int, ...]] = []
    for row in spec.currents:
        output.fill(0.0)
        try:
            neuron.step(float(spec.dt_s), np.asarray(row, dtype=np.float64),
                        output, voltage, refractory)
        except Exception as exc:
            raise RuntimeError("Nengo LIF step failed") from exc
        if (not np.isfinite(output).all() or not np.isfinite(voltage).all()
                or not np.isfinite(refractory).all() or np.any(output < 0)):
            raise ValueError("invalid Nengo LIF state")
        events.append(tuple(int(v > 0) for v in output))
    return _trace("nengo-4.1.0-lif", events, neurons)


def compare(spec: LIFSpec) -> dict[str, object]:
    """Descriptive experimental comparison; NEVER a GENESIS admission gate."""
    reference = run_reference(spec)
    native = run_nengo(spec)
    dt = float(spec.dt_s)
    span = reference.steps * dt
    return {
        "schema": "mojum.neural.nengo-lif-comparison/1",
        "approved": False,
        "reference_sha256": reference.sha256,
        "nengo_sha256": native.sha256,
        "euler_rates_hz": tuple(count / span for count in reference.counts),
        "nengo_rates_hz": tuple(count / span for count in native.counts),
        "max_rate_gap_hz": max(abs(a - b) / span
                               for a, b in zip(reference.counts, native.counts)),
    }


def fixture(steps: int = 1000) -> LIFSpec:
    """Public bounded constant-current stimulus; caller must opt in explicitly."""
    if type(steps) is not int or not 1 <= steps <= MAX_STEPS:
        raise ValueError("invalid steps")
    currents = ((0.0, 0.8, 1.6, 2.4, 3.2),) * steps
    return LIFSpec(currents=currents)
