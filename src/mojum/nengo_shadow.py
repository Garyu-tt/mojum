"""Opt-in, PUBLIC SYNTHETIC Nengo LIF shadow spike experiment.

Never reads Aquarium, GENESIS state, Noosphere, credentials, or real devices.
This is a replaceable engine *probe*, not an active neural or motor backend.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import random
from typing import Any

SCHEMA = "mojum.nengo-shadow-spike-fixture/1"
PINNED_NENGO = "4.1.0"
DT_SECONDS = 0.001
MAX_STEPS = 512
MAX_NEURONS = 64
MAX_SEED = 2**31 - 1


class NengoUnavailableError(RuntimeError):
    """Optional engine is missing or differs from the reviewed version."""


def _validate(*, seed: int, steps: int, neurons: int) -> None:
    if type(seed) is not int or not 0 <= seed <= MAX_SEED:
        raise ValueError("seed must be an exact int in [0, 2**31 - 1]")
    if type(steps) is not int or not 1 <= steps <= MAX_STEPS:
        raise ValueError("steps must be an exact int in [1, 512]")
    if type(neurons) is not int or not 1 <= neurons <= MAX_NEURONS:
        raise ValueError("neurons must be an exact int in [1, 64]")


def _result(*, engine: str, version: str, seed: int,
            steps: int, neurons: int, bins: list[int]) -> dict[str, Any]:
    if len(bins) != steps or any(
        type(x) is not int or x < 0 or x > neurons for x in bins
    ):
        raise ValueError("invalid bounded spike bins")
    trace = json.dumps(bins, separators=(",", ":"), allow_nan=False)
    return {
        "schema": SCHEMA,
        "source": "PUBLIC_SYNTHETIC_ONLY",
        "mode": "shadow_only",
        "engine": engine,
        "engine_version": version,
        "seed": seed,
        "steps": steps,
        "neurons": neurons,
        "dt_ms": 1,
        "spike_events": sum(bins),
        "active_ticks": sum(value > 0 for value in bins),
        "spike_bins_sha256": hashlib.sha256(trace.encode("ascii")).hexdigest(),
        # No individual neuron state, external sensory signal, or motor command.
    }


def run_reference(*, seed: int = 7, steps: int = 128,
                  neurons: int = 16) -> dict[str, Any]:
    """Tiny, deterministic independent LIF control; not Nengo numerics."""
    _validate(seed=seed, steps=steps, neurons=neurons)
    rng = random.Random(seed)
    volts = [rng.random() * 0.1 for _ in range(neurons)]
    gains = [0.85 + 0.3 * rng.random() for _ in range(neurons)]
    alpha = -math.expm1(-DT_SECONDS / 0.02)
    bins = []
    for tick in range(steps):
        current = 1.5 if tick < steps // 2 else 0.0
        count = 0
        for idx, voltage in enumerate(volts):
            voltage += alpha * (current * gains[idx] - voltage)
            if voltage >= 1.0:
                voltage = 0.0
                count += 1
            volts[idx] = voltage
        bins.append(count)
    return _result(engine="stdlib_lif_control", version="1",
                   seed=seed, steps=steps, neurons=neurons, bins=bins)


def run_nengo(*, enabled: bool = False, seed: int = 7,
              steps: int = 128, neurons: int = 16) -> dict[str, Any]:
    """Run the real Nengo engine only after explicit opt-in and version pin."""
    if enabled is not True:
        raise ValueError("Nengo backend requires explicit enabled=True")
    _validate(seed=seed, steps=steps, neurons=neurons)
    try:
        installed = importlib.metadata.version("nengo")
    except importlib.metadata.PackageNotFoundError as exc:
        raise NengoUnavailableError("optional nengo==4.1.0 is not installed") from exc
    if installed != PINNED_NENGO:
        raise NengoUnavailableError("unreviewed Nengo version; require nengo==4.1.0")

    import nengo  # type: ignore[import-not-found]  # optional dependency only

    if nengo.__version__ != PINNED_NENGO:
        raise NengoUnavailableError("Nengo module/distribution version mismatch")

    # Fixed synthetic drive only; no caller-supplied callbacks or real signals.
    midpoint = (steps // 2) * DT_SECONDS

    def pulse(time: float) -> float:
        return 1.0 if time <= midpoint else 0.0

    with nengo.Network(label="mojum-public-synthetic-shadow", seed=seed) as net:
        stimulus = nengo.Node(output=pulse)
        population = nengo.Ensemble(
            n_neurons=neurons, dimensions=1, neuron_type=nengo.LIF(),
            seed=seed,
        )
        nengo.Connection(stimulus, population, synapse=None)
        spike_probe = nengo.Probe(population.neurons, synapse=None)

    with nengo.Simulator(net, dt=DT_SECONDS, seed=seed,
                         progress_bar=False) as sim:
        sim.run_steps(steps, progress_bar=False)
        matrix = sim.data[spike_probe]
        if len(matrix) != steps:
            raise RuntimeError("Nengo returned an unexpected timestep count")
        bins = []
        for row in matrix:
            if len(row) != neurons:
                raise RuntimeError("Nengo returned an unexpected population size")
            if any(not math.isfinite(float(value)) or float(value) < 0 for value in row):
                raise RuntimeError("Nengo returned invalid spike values")
            bins.append(sum(float(value) > 0.0 for value in row))

    return _result(engine="nengo_lif", version=PINNED_NENGO,
                   seed=seed, steps=steps, neurons=neurons, bins=bins)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("stdlib", "nengo"), required=True)
    parser.add_argument("--execute", action="store_true",
                        help="required for any synthetic simulation")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--steps", type=int, default=128)
    parser.add_argument("--neurons", type=int, default=16)
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("--execute is required")
    try:
        if args.backend == "nengo":
            output = run_nengo(enabled=True, seed=args.seed,
                               steps=args.steps, neurons=args.neurons)
        else:
            output = run_reference(seed=args.seed,
                                   steps=args.steps, neurons=args.neurons)
    except (ValueError, NengoUnavailableError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
