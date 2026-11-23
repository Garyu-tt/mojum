"""Bounded offline reducer for caller-supplied synthetic cloth contact measurements.

This module does not simulate cloth or certify physical sensor measurements.
It accepts already measured, PUBLIC_SYNTHETIC integer samples and emits a
detached aggregate without retaining per-sample values.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping

SCHEMA = "mojum.cloth.contact-telemetry/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
CONTACT_KINDS = ("body", "hand", "world")
MAX_SAMPLES = 2048
MAX_TICK = (1 << 53) - 1
MAX_DT_NS = 1_000_000_000
MIN_DT_NS = 1_000
MAX_FORCE_MN = 1_000_000_000
MAX_ABS_STRAIN_UE = 10_000_000
MAX_TOTAL_FORCE_MN = (1 << 63) - 1
_SAMPLE_KEYS = frozenset({"tick", "contact", "normal_force_mn", "strain_ue"})


class ClothTelemetryError(ValueError):
    """Invalid or out-of-budget synthetic cloth telemetry."""


@dataclass(frozen=True)
class ContactAggregate:
    kind: str
    count: int
    normal_force_sum_mn: int
    normal_force_peak_mn: int


@dataclass(frozen=True)
class TelemetryReport:
    schema: str
    classification: str
    sample_count: int
    tick_start: int | None
    tick_end: int | None
    dt_ns: int
    contacts: tuple[ContactAggregate, ...]
    max_abs_strain_ue: int
    input_sha256: str


def _native_int(value: object, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ClothTelemetryError(f"{field} must be an integer in [{low}, {high}]")
    return value


def _sample_copy(item: object, index: int) -> dict[str, object]:
    if not isinstance(item, Mapping) or type(item) is not dict:
        raise ClothTelemetryError(f"sample {index} must be a native dict")
    if set(item) != _SAMPLE_KEYS or any(type(key) is not str for key in item):
        raise ClothTelemetryError(f"sample {index} has an invalid shape")
    tick = _native_int(item["tick"], f"samples[{index}].tick", 0, MAX_TICK)
    kind = item["contact"]
    if type(kind) is not str or kind not in CONTACT_KINDS:
        raise ClothTelemetryError(f"samples[{index}].contact is unsupported")
    force = _native_int(item["normal_force_mn"], f"samples[{index}].normal_force_mn",
                        0, MAX_FORCE_MN)
    strain = _native_int(item["strain_ue"], f"samples[{index}].strain_ue",
                          -MAX_ABS_STRAIN_UE, MAX_ABS_STRAIN_UE)
    return {"tick": tick, "contact": kind,
            "normal_force_mn": force, "strain_ue": strain}


def reduce_contacts(samples: Iterable[Mapping[str, object]], *,
                    dt_ns: int = 10_000_000,
                    classification: str = CLASSIFICATION) -> TelemetryReport:
    """Reduce ordered synthetic samples into stable category-level totals.

    Ticks may repeat (multiple contacts in one simulation step), but may not
    move backwards. Empty input is valid and produces an empty aggregate.
    """
    dt = _native_int(dt_ns, "dt_ns", MIN_DT_NS, MAX_DT_NS)
    if type(classification) is not str or classification != CLASSIFICATION:
        raise ClothTelemetryError("only PUBLIC_SYNTHETIC input is admitted")
    if isinstance(samples, (str, bytes, bytearray, Mapping)):
        raise ClothTelemetryError("samples must be an iterable of sample dicts")

    counts = {kind: 0 for kind in CONTACT_KINDS}
    force_sums = {kind: 0 for kind in CONTACT_KINDS}
    force_peaks = {kind: 0 for kind in CONTACT_KINDS}
    max_strain = 0
    previous_tick = -1
    first_tick = None
    last_tick = None
    canonical_samples = []
    try:
        iterator = iter(samples)
    except TypeError as exc:
        raise ClothTelemetryError("samples must be iterable") from exc

    for index, raw in enumerate(iterator):
        if index >= MAX_SAMPLES:
            raise ClothTelemetryError("sample budget exceeded")
        sample = _sample_copy(raw, index)
        tick = sample["tick"]
        if tick < previous_tick:
            raise ClothTelemetryError("sample ticks must be nondecreasing")
        previous_tick = tick
        if first_tick is None:
            first_tick = tick
        last_tick = tick
        kind = sample["contact"]
        force = sample["normal_force_mn"]
        counts[kind] += 1
        force_sums[kind] += force
        if force_sums[kind] > MAX_TOTAL_FORCE_MN:
            raise ClothTelemetryError("aggregate force exceeds numeric budget")
        force_peaks[kind] = max(force_peaks[kind], force)
        max_strain = max(max_strain, abs(sample["strain_ue"]))
        canonical_samples.append(sample)

    encoded = json.dumps(
        {"schema": SCHEMA, "classification": CLASSIFICATION,
         "dt_ns": dt, "samples": canonical_samples},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    contacts = tuple(
        ContactAggregate(kind, counts[kind], force_sums[kind], force_peaks[kind])
        for kind in CONTACT_KINDS
    )
    return TelemetryReport(
        schema=SCHEMA,
        classification=CLASSIFICATION,
        sample_count=len(canonical_samples),
        tick_start=first_tick,
        tick_end=last_tick,
        dt_ns=dt,
        contacts=contacts,
        max_abs_strain_ue=max_strain,
        input_sha256=hashlib.sha256(encoded).hexdigest(),
    )
