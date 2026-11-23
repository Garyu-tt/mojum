"""Opt-in PUBLIC-synthetic snnTorch LIF reference probe (issue #760).

No dependency imports or simulation authority in the default path. This is not
an organism's brain, a learning test, or a live motor signal source.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import sys
from typing import Sequence

WIRE = "mojum.snntorch_shadow/1"
UPSTREAM_VERSION = "1.0.0"
MAX_NEURONS = 32
MAX_TICKS = 128
MAX_CURRENT = 2.0


class ShadowError(ValueError):
    """Invalid, unauthorized or non-reproducible shadow probe."""


@dataclass(frozen=True)
class ShadowTrace:
    engine: str
    source_digest: str
    spikes: tuple[tuple[int, ...], ...]
    membranes: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class Comparison:
    wire: str
    source_digest: str
    backend: str
    ticks: int
    neurons: int
    mismatched_spikes: int
    max_membrane_delta: float
    passed: bool
    motor_authority: bool = False
    learning_measured: bool = False


def _number(value: object, name: str, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise ShadowError(f"{name} must be a finite number")
    try:
        n = float(value)
    except (OverflowError, ValueError) as exc:
        raise ShadowError(f"{name} outside bounded finite range") from exc
    if not math.isfinite(n) or not minimum <= n <= maximum:
        raise ShadowError(f"{name} outside bounded finite range")
    return n


def _validate(inputs: object, *, enabled: bool, source_class: str,
              beta: float, threshold: float) -> tuple[tuple[tuple[float, ...], ...], str, float, float]:
    if enabled is not True or source_class != "PUBLIC_SYNTHETIC":
        raise ShadowError("explicit opt-in and PUBLIC_SYNTHETIC source class required")
    b = _number(beta, "beta", 0.0, 0.99999)
    th = _number(threshold, "threshold", 0.5, 2.0)
    if not isinstance(inputs, (list, tuple)) or not 1 <= len(inputs) <= MAX_TICKS:
        raise ShadowError("ticks must be a bounded sequence")
    width = None
    validated = []
    for row in inputs:
        if not isinstance(row, (list, tuple)) or not 1 <= len(row) <= MAX_NEURONS:
            raise ShadowError("each tick must have 1..32 neurons")
        if width is None:
            width = len(row)
        if width != len(row):
            raise ShadowError("ragged neural population")
        validated.append(tuple(_number(v, "current", -MAX_CURRENT, MAX_CURRENT) for v in row))
    data = tuple(validated)
    canonical = json.dumps({"wire": WIRE, "beta": b, "threshold": th, "inputs": data},
                           sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return data, digest, b, th


def _run_reference(data: tuple[tuple[float, ...], ...], beta: float,
                   threshold: float) -> tuple[tuple[tuple[int, ...], ...], tuple[tuple[float, ...], ...]]:
    """snn.Leaky(reset_delay=True, reset_mechanism='subtract') scalar reference.

    snnTorch applies previous-state reset, integrates the new current and
    emits a spike only for membrane strictly greater than threshold.
    """
    mem = [0.0] * len(data[0])
    spike_rows = []
    mem_rows = []
    for row in data:
        new_mem = [beta * old + current - (threshold if old > threshold else 0.0)
                   for old, current in zip(mem, row)]
        spike_rows.append(tuple(int(v > threshold) for v in new_mem))
        mem_rows.append(tuple(new_mem))
        mem = new_mem
    return tuple(spike_rows), tuple(mem_rows)


def _run_native(data: tuple[tuple[float, ...], ...], beta: float,
                threshold: float) -> tuple[tuple[tuple[int, ...], ...], tuple[tuple[float, ...], ...]]:
    # Import only on explicit opt-in; never auto-install or download models.
    try:
        import torch
        import snntorch as snn
    except ImportError as exc:
        raise ShadowError("optional CPU torch + snntorch==1.0.0 not installed") from exc
    if getattr(snn, "__version__", None) != UPSTREAM_VERSION:
        raise ShadowError("snnTorch runtime version differs from pinned 1.0.0")
    # Explicit CPU, inference only. No GPU, training, patch execution or I/O.
    neuron = snn.Leaky(beta=beta, threshold=threshold, surrogate_disable=True,
                       reset_mechanism="subtract", reset_delay=True,
                       init_hidden=False).cpu().eval()
    spikes = []
    membranes = []
    with torch.no_grad():
        mem = torch.zeros(len(data[0]), device="cpu", dtype=torch.float32)
        for row in data:
            current = torch.tensor(row, device="cpu", dtype=torch.float32)
            spk, mem = neuron(current, mem)
            spikes.append(tuple(int(v) for v in spk.detach().cpu().tolist()))
            membranes.append(tuple(float(v) for v in mem.detach().cpu().tolist()))
    return tuple(spikes), tuple(membranes)


def _validate_output(spikes: object, membranes: object, ticks: int, neurons: int) -> None:
    if not isinstance(spikes, tuple) or not isinstance(membranes, tuple):
        raise ShadowError("backend returned non-tuple output")
    if len(spikes) != ticks or len(membranes) != ticks:
        raise ShadowError("backend returned wrong number of ticks")
    for srow, mrow in zip(spikes, membranes):
        if not isinstance(srow, tuple) or not isinstance(mrow, tuple) or len(srow) != neurons or len(mrow) != neurons:
            raise ShadowError("backend returned bad neuron shape")
        if any(type(x) is not int or x not in (0, 1) for x in srow):
            raise ShadowError("invalid backend spikes")
        if any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1024 for x in mrow):
            raise ShadowError("invalid backend membrane")


def run_shadow(inputs: Sequence[Sequence[float]], *, enabled: bool = False,
               source_class: str = "", engine: str = "reference", beta: float = 0.6,
               threshold: float = 1.0) -> ShadowTrace:
    """Run a bounded isolated trace; returns data, **never** motor intentions.

    Declared source class is a caller assertion, not cryptographic attestation.
    """
    data, digest, b, th = _validate(inputs, enabled=enabled,
                                    source_class=source_class, beta=beta, threshold=threshold)
    if engine == "reference":
        spikes, membranes = _run_reference(data, b, th)
    elif engine == "snntorch":
        spikes, membranes = _run_native(data, b, th)
    else:
        raise ShadowError("engine must be 'reference' or 'snntorch'")
    _validate_output(spikes, membranes, len(data), len(data[0]))
    return ShadowTrace(engine, digest, spikes, membranes)


def compare_shadow(inputs: Sequence[Sequence[float]], *, enabled: bool = False,
                   source_class: str = "", beta: float = 0.6, threshold: float = 1.0,
                   max_delta: float = 1e-5) -> Comparison:
    """Require fixed spike agreement and bounded potential error vs snnTorch."""
    tol = _number(max_delta, "max_delta", 0.0, 1e-3)
    ref = run_shadow(inputs, enabled=enabled, source_class=source_class,
                     engine="reference", beta=beta, threshold=threshold)
    native = run_shadow(inputs, enabled=enabled, source_class=source_class,
                        engine="snntorch", beta=beta, threshold=threshold)
    if ref.source_digest != native.source_digest:
        raise ShadowError("source digest mismatch")
    errors = sum(a != b for s1, s2 in zip(ref.spikes, native.spikes) for a, b in zip(s1, s2))
    delta = max(abs(a - b) for m1, m2 in zip(ref.membranes, native.membranes)
                for a, b in zip(m1, m2))
    return Comparison(WIRE, ref.source_digest, native.engine,
                      len(ref.spikes), len(ref.spikes[0]), errors, delta,
                      errors == 0 and delta <= tol)


def synthetic_fixture(seed: int = 17, *, ticks: int = 24, neurons: int = 4) -> tuple[tuple[float, ...], ...]:
    """Portable deterministic stimulus; no host clock, system entropy, memory or identity."""
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ShadowError("seed must be uint32")
    if type(ticks) is not int or not 1 <= ticks <= MAX_TICKS:
        raise ShadowError("ticks out of bounds")
    if type(neurons) is not int or not 1 <= neurons <= MAX_NEURONS:
        raise ShadowError("neurons out of bounds")
    state = seed
    rows = []
    for _ in range(ticks):
        row = []
        for _ in range(neurons):
            state = (1664525 * state + 1013904223) & 0xffffffff
            row.append(round((state % 1501) / 1000.0 - 0.3, 3))
        rows.append(tuple(row))
    return tuple(rows)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Shadow-only PUBLIC synthetic snnTorch CPU comparison")
    p.add_argument("--enable", action="store_true", help="explicitly opt in")
    p.add_argument("--native", action="store_true", help="compare to installed CPU snntorch 1.0.0")
    p.add_argument("--seed", type=int, default=17)
    args = p.parse_args(argv)
    try:
        inputs = synthetic_fixture(args.seed)
        if args.native:
            result = compare_shadow(inputs, enabled=args.enable, source_class="PUBLIC_SYNTHETIC")
            summary = result.__dict__
            status = 0 if result.passed else 1
        else:
            trace = run_shadow(inputs, enabled=args.enable, source_class="PUBLIC_SYNTHETIC")
            summary = {"wire": WIRE, "engine": trace.engine, "source_digest": trace.source_digest,
                       "ticks": len(trace.spikes), "neurons": len(trace.spikes[0]),
                       "motor_authority": False, "learning_measured": False}
            status = 0
        print(json.dumps(summary, sort_keys=True))
        return status
    except ShadowError as exc:
        print(f"shadow probe rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
