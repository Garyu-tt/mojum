"""Opt-in PUBLIC_SYNTHETIC STDP differential fixture for Brian2.

No authority, observation, motor, memory, or network integration. The stdlib
reference runs without Brian2; the native runner never silently falls back.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Mapping

SCHEMA = "mojum.neural.stdp-differential/1"
VISIBILITY = "PUBLIC_SYNTHETIC"
BRIAN2_VERSION = "2.9.0"
MAX_EVENTS = 256
MAX_TICKS = 4096
PARAMETERS = frozenset({
    "schema", "visibility", "dt_ms", "tau_pre_ms", "tau_post_ms",
    "a_plus", "a_minus", "initial_weight", "min_weight", "max_weight", "events",
})


class STDPInputError(ValueError):
    """Invalid input; never interpret as a PRIVATE organism sample."""


class BackendUnavailable(RuntimeError):
    """The optional pinned native Brian2 backend is unavailable."""


@dataclass(frozen=True)
class STDPEvent:
    tick: int
    kind: str


@dataclass(frozen=True)
class STDPCase:
    dt_ms: float
    tau_pre_ms: float
    tau_post_ms: float
    a_plus: float
    a_minus: float
    initial_weight: float
    min_weight: float
    max_weight: float
    events: tuple[STDPEvent, ...]


def _finite_num(value: Any, name: str, lower: float, upper: float, *, strict=False) -> float:
    if type(value) not in (int, float):
        raise STDPInputError(f"{name} must be a finite number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise STDPInputError(f"{name} outside allowed range") from exc
    if not math.isfinite(value) or not (lower < value <= upper if strict else lower <= value <= upper):
        raise STDPInputError(f"{name} outside allowed range")
    return value


def parse_case(payload: Mapping[str, Any]) -> STDPCase:
    """Accept only fixed-size, explicitly public synthetic numerical events."""
    if type(payload) is not dict or set(payload) != PARAMETERS:
        raise STDPInputError("invalid schema keys; additional fields are forbidden")
    if (type(payload["schema"]) is not str or payload["schema"] != SCHEMA
            or type(payload["visibility"]) is not str or payload["visibility"] != VISIBILITY):
        raise STDPInputError("unsupported schema or non-synthetic visibility")
    dt = _finite_num(payload["dt_ms"], "dt_ms", 0.1, 10)
    tau_pre = _finite_num(payload["tau_pre_ms"], "tau_pre_ms", 0.1, 1000)
    tau_post = _finite_num(payload["tau_post_ms"], "tau_post_ms", 0.1, 1000)
    a_plus = _finite_num(payload["a_plus"], "a_plus", 0, 1)
    a_minus = _finite_num(payload["a_minus"], "a_minus", 0, 1)
    lower = _finite_num(payload["min_weight"], "min_weight", 0, 1)
    upper = _finite_num(payload["max_weight"], "max_weight", 0, 1)
    if not lower < upper:
        raise STDPInputError("weight bounds must be strictly ordered")
    initial = _finite_num(payload["initial_weight"], "initial_weight", lower, upper)
    raw = payload["events"]
    if type(raw) is not list or not 1 <= len(raw) <= MAX_EVENTS:
        raise STDPInputError("events must be a nonempty bounded list")
    events: list[STDPEvent] = []
    previous = -1
    for item in raw:
        if type(item) is not dict or set(item) != {"tick", "kind"}:
            raise STDPInputError("event must contain exactly tick and kind")
        tick = item["tick"]
        if type(tick) is not int or not 1 <= tick <= MAX_TICKS or tick <= previous:
            raise STDPInputError("ticks must be unique, positive and strictly increasing")
        kind = item["kind"]
        if type(kind) is not str or kind not in ("pre", "post"):
            raise STDPInputError("event kind must be pre or post")
        events.append(STDPEvent(tick, kind))
        previous = tick
    return STDPCase(dt, tau_pre, tau_post, a_plus, a_minus, initial, lower, upper, tuple(events))


def public_fixture() -> dict[str, Any]:
    """Frozen zero-identity fixture, not a real neural or personal record."""
    return {
        "schema": SCHEMA, "visibility": VISIBILITY,
        "dt_ms": 1.0, "tau_pre_ms": 20.0, "tau_post_ms": 20.0,
        "a_plus": 0.07, "a_minus": 0.08,
        "initial_weight": 0.5, "min_weight": 0.0, "max_weight": 1.0,
        "events": [
            {"tick": 1, "kind": "pre"}, {"tick": 4, "kind": "post"},
            {"tick": 7, "kind": "post"}, {"tick": 11, "kind": "pre"},
            {"tick": 14, "kind": "post"}, {"tick": 17, "kind": "pre"},
            {"tick": 22, "kind": "post"}, {"tick": 25, "kind": "pre"},
        ],
    }


def _bounded(w: float, case: STDPCase) -> float:
    return max(case.min_weight, min(case.max_weight, w))


def reference_trace(case: STDPCase) -> tuple[float, ...]:
    """Deterministic event-exact analytical trace decay, not Euler steps."""
    pre_trace = post_trace = 0.0
    weight = case.initial_weight
    prev_tick = 0
    result = []
    for event in case.events:
        elapsed = (event.tick - prev_tick) * case.dt_ms
        pre_trace *= math.exp(-elapsed / case.tau_pre_ms)
        post_trace *= math.exp(-elapsed / case.tau_post_ms)
        if event.kind == "pre":
            pre_trace += case.a_plus
            weight = _bounded(weight + post_trace, case)
        else:
            post_trace -= case.a_minus
            weight = _bounded(weight + pre_trace, case)
        if not math.isfinite(weight):
            raise ArithmeticError("nonfinite reference weight")
        result.append(weight)
        prev_tick = event.tick
    return tuple(result)


def brian2_trace(case: STDPCase) -> tuple[float, ...]:
    """Use optional pinned Brian2/Numpy, with no C++ or real-world endpoints.

    Run in an isolated worker because Brian2 has process-global preferences.
    """
    try:
        import brian2 as b2
        import numpy as np
    except ImportError as exc:
        raise BackendUnavailable("install explicit optional brian2==2.9.0; no silent fallback") from exc
    if b2.__version__ != BRIAN2_VERSION:
        raise BackendUnavailable("unsupported Brian2 version; expected " + BRIAN2_VERSION)
    original_target = b2.prefs.codegen.target
    try:
        b2.prefs.codegen.target = "numpy"
        clock = b2.Clock(dt=case.dt_ms * b2.ms)
        pre_ticks = [e.tick for e in case.events if e.kind == "pre"]
        post_ticks = [e.tick for e in case.events if e.kind == "post"]
        pre = b2.SpikeGeneratorGroup(1, [0] * len(pre_ticks), np.asarray(pre_ticks, dtype=float) * case.dt_ms * b2.ms, clock=clock)
        post = b2.SpikeGeneratorGroup(1, [0] * len(post_ticks), np.asarray(post_ticks, dtype=float) * case.dt_ms * b2.ms, clock=clock)
        syn = b2.Synapses(
            pre, post,
            model="""w : 1
            dApre/dt = -Apre / taupre : 1 (event-driven)
            dApost/dt = -Apost / taupost : 1 (event-driven)""",
            on_pre="""Apre += a_plus
            w = clip(w + Apost, w_min, w_max)""",
            on_post="""Apost -= a_minus
            w = clip(w + Apre, w_min, w_max)""",
            namespace={
                "taupre": case.tau_pre_ms * b2.ms,
                "taupost": case.tau_post_ms * b2.ms,
                "a_plus": case.a_plus, "a_minus": case.a_minus,
                "w_min": case.min_weight, "w_max": case.max_weight,
            },
            clock=clock,
        )
        syn.connect(i=0, j=0)
        syn.w = case.initial_weight
        monitor = b2.StateMonitor(syn, "w", record=[0], when="end", clock=clock)
        network = b2.Network(pre, post, syn, monitor)
        network.run((case.events[-1].tick + 1) * case.dt_ms * b2.ms)
        weights = tuple(float(monitor.w[0][e.tick]) for e in case.events)
        if len(weights) != len(case.events) or any(not math.isfinite(w) for w in weights):
            raise ArithmeticError("native backend yielded invalid weight trace")
        return weights
    finally:
        b2.prefs.codegen.target = original_target


def compare_native(case: STDPCase, *, tolerance: float = 1e-8) -> dict[str, Any]:
    """Fail closed on missing native backend or any per-event mismatch."""
    tolerance = _finite_num(tolerance, "tolerance", 0.0, 1e-4, strict=True)
    ref = reference_trace(case)
    native = brian2_trace(case)
    if len(ref) != len(native):
        raise AssertionError("native trace length mismatch")
    error = max(abs(a - b) for a, b in zip(ref, native))
    if error > tolerance:
        raise AssertionError(f"Brian2 STDP differential mismatch: max_error={error:.6g}")
    return {
        "schema": "mojum.neural.stdp-differential-result/1",
        "backend": "brian2", "version": BRIAN2_VERSION,
        "synthetic_only": True, "events": len(case.events),
        "max_abs_weight_error": error, "pass": True,
    }
