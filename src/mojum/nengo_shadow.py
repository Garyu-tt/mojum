"""Opt-in, PUBLIC-synthetic CPU Nengo LIFRate shadow benchmark.

No import of Nengo at module import time. Never loaded by Aquarium/GENESIS,
never emits a motor command, and never reads Noosphere or network services.
The v1 fixture uses rate neurons, not spiking neurons or plasticity.
"""

from __future__ import annotations

import hashlib
import importlib
from importlib import metadata
import json
import math

INPUT_SCHEMA = "mojum.nengo.rate-fixture/1"
OUTPUT_SCHEMA = "mojum.nengo.rate-shadow/1"
PINNED_NENGO = "4.1.0"
DT_NS = 1_000_000
TAU_RC = 0.020
TAU_REF = 0.002
MAX_STIMULI = 16
MAX_NEURONS = 8
MAX_ERROR_HZ = 0.01


class NengoShadowError(ValueError):
    """Invalid input, unavailable backend, or differential test failure."""


def validate_fixture(fixture):
    """Return a detached, strictly bounded, versioned PUBLIC-only fixture."""
    keys = {"schema", "source", "seed", "dt_ns", "inputs_milli", "gain_milli", "bias_milli"}
    if type(fixture) is not dict or set(fixture) != keys:
        raise NengoShadowError("fixture keys are missing or unexpected")
    if fixture["schema"] != INPUT_SCHEMA or fixture["source"] != "PUBLIC_SYNTHETIC":
        raise NengoShadowError("only versioned PUBLIC_SYNTHETIC fixtures are permitted")
    seed, dt_ns = fixture["seed"], fixture["dt_ns"]
    if type(seed) is not int or not 0 <= seed < 2**32 or type(dt_ns) is not int or dt_ns != DT_NS:
        raise NengoShadowError("invalid seed or dt_ns")

    def vector(name, minimum, maximum, maximum_length):
        seq = fixture[name]
        if (type(seq) is not list or not 1 <= len(seq) <= maximum_length
                or any(type(x) is not int or not minimum <= x <= maximum for x in seq)):
            raise NengoShadowError(f"invalid {name}")
        return seq[:]

    samples = vector("inputs_milli", -1000, 1000, MAX_STIMULI)
    gain = vector("gain_milli", 0, 4000, MAX_NEURONS)
    bias = vector("bias_milli", 0, 4000, MAX_NEURONS)
    if len(gain) != len(bias):
        raise NengoShadowError("gain_milli/bias_milli length mismatch")
    return {
        "schema": INPUT_SCHEMA,
        "source": "PUBLIC_SYNTHETIC",
        "seed": seed,
        "dt_ns": dt_ns,
        "inputs_milli": samples,
        "gain_milli": gain,
        "bias_milli": bias,
    }


def _lif_rate(current):
    """Independent closed-form rate of Nengo's default LIFRate neuron (Hz)."""
    if current <= 1.0:
        return 0.0
    return 1.0 / (TAU_REF + TAU_RC * math.log1p(1.0 / (current - 1.0)))


def reference_rates(fixture):
    """Stdlib-only independent expected Nengo LIFRate firing rates."""
    f = validate_fixture(fixture)
    return [
        [_lif_rate(b / 1000 + g / 1000 * x / 1000)
         for g, b in zip(f["gain_milli"], f["bias_milli"])]
        for x in f["inputs_milli"]
    ]


def _pinned_nengo():
    try:
        version = metadata.version("nengo")
    except metadata.PackageNotFoundError as exc:
        raise NengoShadowError("optional Nengo 4.1.0 not installed; opt-in only") from exc
    if version != PINNED_NENGO:
        raise NengoShadowError(f"unreviewed Nengo version {version!r}; require {PINNED_NENGO}")
    try:
        return importlib.import_module("nengo")
    except ImportError as exc:
        raise NengoShadowError("Nengo runtime is incomplete") from exc


def run_shadow(fixture, *, permit_nengo=False):
    """Run an actual Nengo CPU LIFRate ensemble, independent analytic check.

    Explicit permit is mandatory; output is non-authoritative and motor-null.
    """
    f = validate_fixture(fixture)
    if permit_nengo is not True:
        raise NengoShadowError("Nengo execution requires explicit permit_nengo=True")
    nengo = _pinned_nengo()
    expected = reference_rates(f)
    n = len(f["gain_milli"])
    actual = []
    # Independent constant-input simulations avoid temporal cross-talk;
    # four ticks accommodate the graph's initial signal scheduling.
    for x in f["inputs_milli"]:
        with nengo.Network(seed=f["seed"]) as network:
            drive = nengo.Node(output=[x / 1000])
            population = nengo.Ensemble(
                n_neurons=n, dimensions=1, radius=1.0,
                encoders=[[1.0] for _ in range(n)],
                gain=[g / 1000 for g in f["gain_milli"]],
                bias=[b / 1000 for b in f["bias_milli"]],
                neuron_type=nengo.LIFRate(tau_rc=TAU_RC, tau_ref=TAU_REF),
                seed=f["seed"],
            )
            nengo.Connection(drive, population, synapse=None)
            rate_probe = nengo.Probe(population.neurons, "output", synapse=None)
        with nengo.Simulator(network, dt=DT_NS / 1e9, progress_bar=False) as sim:
            sim.run_steps(4, progress_bar=False)
            rates = [float(y) for y in sim.data[rate_probe][-1]]
        if len(rates) != n or not all(math.isfinite(v) and 0 <= v <= 1 / TAU_REF for v in rates):
            raise NengoShadowError("backend returned invalid rate shape or values")
        actual.append(rates)

    errors = [abs(a - b) for row, ref in zip(actual, expected) for a, b in zip(row, ref)]
    maximum = max(errors)
    if maximum > MAX_ERROR_HZ:
        raise NengoShadowError(f"Nengo/analytic LIFRate mismatch: {maximum:.6g} Hz")
    fingerprint = hashlib.sha256(json.dumps(f, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "schema": OUTPUT_SCHEMA,
        "authority": "SHADOW_ONLY",
        "source": "PUBLIC_SYNTHETIC",
        "backend": "nengo.LIFRate",
        "backend_version": PINNED_NENGO,
        "fixture_sha256": fingerprint,
        "dt_ns": DT_NS,
        "rates_hz": [[round(v, 6) for v in row] for row in actual],
        "reference_hz": [[round(v, 6) for v in row] for row in expected],
        "max_abs_error_hz": round(maximum, 6),
        "motor_intention": None,
    }
