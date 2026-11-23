"""Offline, opt-in snnTorch 1.0.0 LIF shadow validation; never a motor backend.

The stdlib reference and optional native runner consume only PUBLIC_SYNTHETIC
numeric currents. No journals, model downloads, GPU, execution of user code,
file writes or real sensors are involved. External execution still needs an
OS-level CPU/RAM/walltime watchdog: this module is not a sandbox.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math

SCHEMA = "mojum.neural.snntorch-lif-probe/1"
STATE_SCHEMA = "mojum.neural.snntorch-lif-state/1"
SNNTORCH_VERSION = "1.0.0"
MAX_NEURONS = 8
MAX_STEPS = 128
TOLERANCE = 5e-5


class ProbeError(ValueError):
    """Fail-closed fixture, state, dependency, or differential validation."""


def _real(v: object, lo: float, hi: float) -> float:
    if type(v) not in (float, int):
        raise ProbeError("numeric value required (bool is forbidden)")
    try:
        f = float(v)
    except (OverflowError, ValueError, TypeError) as exc:
        raise ProbeError("non-representable number") from exc
    if not math.isfinite(f) or not lo <= f <= hi:
        raise ProbeError("numeric value outside finite bounds")
    return f


def _int(v: object, low: int, high: int) -> int:
    if type(v) is not int or not low <= v <= high:
        raise ProbeError("invalid exact integer")
    return v


def _sha(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def validate_fixture(fixture: object) -> tuple[float, float, tuple[tuple[float, ...], ...]]:
    if type(fixture) is not dict or set(fixture) != {
        "schema", "visibility", "beta", "threshold", "currents"
    } or fixture["schema"] != SCHEMA or fixture["visibility"] != "PUBLIC_SYNTHETIC":
        raise ProbeError("only exact PUBLIC_SYNTHETIC LIF fixture accepted")
    beta = _real(fixture["beta"], 0.05, 0.95)
    threshold = _real(fixture["threshold"], 0.25, 2.0)
    rows = fixture["currents"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_STEPS:
        raise ProbeError("invalid number of steps")
    if type(rows[0]) is not list or not 1 <= len(rows[0]) <= MAX_NEURONS:
        raise ProbeError("invalid neuron count")
    size = len(rows[0])
    if any(type(row) is not list or len(row) != size for row in rows):
        raise ProbeError("ragged or non-list currents")
    return beta, threshold, tuple(tuple(_real(v, 0.0, 2.0) for v in row) for row in rows)


class LIFReference:
    """snn.Leaky(reset_delay=True, reset_mechanism='subtract') scalar CPU model.

    Previous *membrane threshold*, not previous returned spike, determines
    the reset. A current tick fires when new membrane strictly exceeds threshold.
    """

    def __init__(self, *, beta: float, threshold: float, neurons: int):
        self.beta = _real(beta, 0.05, 0.95)
        self.threshold = _real(threshold, 0.25, 2.0)
        self.neurons = _int(neurons, 1, MAX_NEURONS)
        self.membrane = [0.0] * self.neurons
        self.steps = 0

    def step(self, currents: object) -> tuple[list[int], list[float]]:
        if type(currents) not in (list, tuple) or len(currents) != self.neurons:
            raise ProbeError("invalid current vector")
        row = [_real(x, 0.0, 2.0) for x in currents]
        if self.steps == MAX_STEPS:
            raise ProbeError("step budget exceeded")
        new = [self.beta * old + x - (self.threshold if old > self.threshold else 0.0)
               for old, x in zip(self.membrane, row)]
        # A strict > comparison matches snnTorch's fire() over shifted membrane.
        spikes = [int(v > self.threshold) for v in new]
        self.membrane = new
        self.steps += 1
        return spikes, list(new)

    def snapshot(self) -> dict:
        state = {"schema": STATE_SCHEMA, "beta": self.beta, "threshold": self.threshold,
                 "neurons": self.neurons, "steps": self.steps,
                 "membrane": list(self.membrane)}
        return {**state, "sha256": _sha(state)}

    @classmethod
    def restore(cls, saved: object) -> "LIFReference":
        if type(saved) is not dict or set(saved) != {
            "schema", "beta", "threshold", "neurons", "steps", "membrane", "sha256"
        } or saved["schema"] != STATE_SCHEMA:
            raise ProbeError("incompatible LIF snapshot")
        state = {k: v for k, v in saved.items() if k != "sha256"}
        if type(saved["sha256"]) is not str or len(saved["sha256"]) != 64:
            raise ProbeError("malformed snapshot digest")
        try:
            digest = _sha(state)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProbeError("malformed snapshot") from exc
        if saved["sha256"] != digest:
            raise ProbeError("snapshot digest mismatch")
        obj = cls(beta=saved["beta"], threshold=saved["threshold"],
                  neurons=saved["neurons"])
        obj.steps = _int(saved["steps"], 0, MAX_STEPS)
        values = saved["membrane"]
        if type(values) is not list or len(values) != obj.neurons:
            raise ProbeError("invalid snapshot membrane")
        obj.membrane = [_real(x, -2.0, 256.0) for x in values]
        # Delayed subtract reset can legitimately undershoot zero after a
        # spike, even with nonnegative currents. Only step-zero is exact zero.
        if obj.steps == 0 and any(obj.membrane):
            raise ProbeError("impossible initial membrane state")
        return obj


def reference_trace(fixture: object) -> dict:
    beta, threshold, rows = validate_fixture(fixture)
    simulator = LIFReference(beta=beta, threshold=threshold, neurons=len(rows[0]))
    spikes, membranes = [], []
    for row in rows:
        spk, mem = simulator.step(row)
        spikes.append(spk)
        membranes.append(mem)
    trace = {"schema": SCHEMA, "engine": "stdlib_reference", "steps": len(rows),
             "neurons": len(rows[0]), "spikes": spikes, "membrane": membranes,
             "state_sha256": simulator.snapshot()["sha256"]}
    return {**trace, "trace_sha256": _sha(trace)}


def verify_native_trace(reference: dict, native_spikes: object,
                        native_membrane: object) -> dict:
    """Validate independently obtained native output, never accept a mock as proof."""
    if type(native_spikes) is not list or type(native_membrane) is not list:
        raise ProbeError("native trace shape invalid")
    if len(native_spikes) != reference["steps"] or len(native_membrane) != reference["steps"]:
        raise ProbeError("native trace length mismatch")
    peak = 0.0
    for step, (spk, mem) in enumerate(zip(native_spikes, native_membrane)):
        if type(spk) is not list or type(mem) is not list or len(spk) != reference["neurons"] or len(mem) != reference["neurons"]:
            raise ProbeError("native neuron shape mismatch")
        for i, (s, m) in enumerate(zip(spk, mem)):
            if type(s) is not int or s not in (0, 1) or s != reference["spikes"][step][i]:
                raise ProbeError("native/reference spike disagreement")
            value = _real(m, -2.0, 256.0)
            error = abs(value - reference["membrane"][step][i])
            if error > TOLERANCE:
                raise ProbeError("native/reference membrane disagreement")
            peak = max(peak, error)
    return {"schema": SCHEMA, "status": "SHADOW_ONLY_NUMERIC_MATCH",
            "steps": reference["steps"], "neurons": reference["neurons"],
            "max_abs_membrane_error": peak, "reference_sha256": reference["trace_sha256"],
            "motor_intention": None}


def run_snntorch(fixture: object, *, enabled: bool = False,
                  expected_torch_version: str | None = None) -> dict:
    """Optional local CPU-only torch+snnTorch run, with exact version admission.

    Call from a disposable process with independent memory/CPU/time limits.
    No package installation or external networking is attempted.
    """
    if enabled is not True:
        raise ProbeError("native snnTorch execution requires enabled=True")
    beta, threshold, rows = validate_fixture(fixture)
    if (type(expected_torch_version) is not str or not expected_torch_version
            or len(expected_torch_version) > 64 or any(c not in "0123456789.+cpu" for c in expected_torch_version)):
        raise ProbeError("explicit exact torch version required")
    try:
        torch = importlib.import_module("torch")
        snn = importlib.import_module("snntorch")
    except ImportError as exc:
        raise ProbeError("optional torch/snntorch dependency unavailable") from exc
    if getattr(snn, "__version__", None) != SNNTORCH_VERSION or getattr(torch, "__version__", None) != expected_torch_version:
        raise ProbeError("unpinned snnTorch/torch version")
    reference = reference_trace(fixture)
    spikes, membranes = [], []
    try:
        with torch.no_grad():
            lif = snn.Leaky(beta=beta, threshold=threshold, reset_mechanism="subtract",
                            reset_delay=True, init_hidden=False, surrogate_disable=True)
            mem = torch.zeros((1, len(rows[0])), dtype=torch.float32, device="cpu")
            for row in rows:
                stimulus = torch.tensor((row,), dtype=torch.float32, device="cpu")
                spk, mem = lif(stimulus, mem)
                if spk.device.type != "cpu" or mem.device.type != "cpu":
                    raise ProbeError("native backend left CPU")
                spikes.append([int(x) for x in spk[0].tolist()])
                membranes.append([float(x) for x in mem[0].tolist()])
    except ProbeError:
        raise
    except (RuntimeError, TypeError, ValueError, AttributeError) as exc:
        raise ProbeError("native CPU execution failed") from exc
    result = verify_native_trace(reference, spikes, membranes)
    return {**result, "backend": "snntorch", "snntorch_version": SNNTORCH_VERSION,
            "torch_version": expected_torch_version}


def public_fixture() -> dict:
    """No pseudo-private observations; generated, fixed, bounded public trace."""
    currents = [[0.6 if (t + i) % 5 < 3 else 0.0 for i in range(3)]
                for t in range(24)]
    return {"schema": SCHEMA, "visibility": "PUBLIC_SYNTHETIC",
            "beta": 0.75, "threshold": 1.0, "currents": currents}


if __name__ == "__main__":
    print(json.dumps(reference_trace(public_fixture()), sort_keys=True))
