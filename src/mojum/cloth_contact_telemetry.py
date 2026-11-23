"""Bounded, read-only reduction of explicitly synthetic cloth-contact samples.

This module aggregates caller-supplied measurements. It does not simulate
cloth, infer unobserved contacts, control actuators, or persist any data.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

SCHEMA: Final = "mojum.cloth.contact-telemetry/1"
PUBLIC_SYNTHETIC: Final = "PUBLIC_SYNTHETIC"
SURFACES: Final = ("body", "hand", "world")
MAX_SAMPLES: Final = 4096
MAX_CONTACT_ID_LENGTH: Final = 96
MAX_REVISION_LENGTH: Final = 128
MAX_TICK: Final = 2**63 - 1
MAX_TIME_NS: Final = 2**63 - 1
MAX_FORCE_N: Final = 1_000_000.0
_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_FIELDS: Final = frozenset({
    "tick", "observed_at_ns", "rig_revision", "contact_id", "surface",
    "normal_force_n", "tension_n", "compression_n",
})


class ContactTelemetryError(ValueError):
    """Invalid or incomparable synthetic contact telemetry."""


def _integer(value: object, name: str, maximum: int = MAX_TICK) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ContactTelemetryError(f"{name} must be an integer in [0, {maximum}]")
    return value


def _text(value: object, name: str, pattern: re.Pattern[str], limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise ContactTelemetryError(f"{name} must be nonempty text (<= {limit} chars)")
    if pattern.fullmatch(value) is None:
        raise ContactTelemetryError(f"{name} contains unsupported characters")
    return value


def _force(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ContactTelemetryError(f"{name} must be a finite non-negative Newton value")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ContactTelemetryError(f"{name} exceeds the numeric range") from exc
    if not math.isfinite(number) or not 0.0 <= number <= MAX_FORCE_N:
        raise ContactTelemetryError(
            f"{name} must be finite and in [0, {MAX_FORCE_N:g}] N"
        )
    return number


def _canonical(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise ContactTelemetryError("telemetry cannot be represented as canonical JSON") from exc
    return encoded


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normalise_sample(sample: object, expected_revision: str) -> dict:
    if type(sample) is not dict or set(sample) != _FIELDS:
        raise ContactTelemetryError("each sample must be a plain object with the exact field set")
    tick = _integer(sample["tick"], "tick")
    observed_at_ns = _integer(sample["observed_at_ns"], "observed_at_ns", MAX_TIME_NS)
    revision = _text(
        sample["rig_revision"], "rig_revision", _REVISION_RE, MAX_REVISION_LENGTH
    )
    if revision != expected_revision:
        raise ContactTelemetryError("sample rig_revision does not match the admitted rig")
    contact_id = _text(
        sample["contact_id"], "contact_id", _ID_RE, MAX_CONTACT_ID_LENGTH
    )
    surface = sample["surface"]
    if type(surface) is not str or surface not in SURFACES:
        raise ContactTelemetryError("surface must be body, hand, or world")
    return {
        "tick": tick,
        "observed_at_ns": observed_at_ns,
        "rig_revision": revision,
        "contact_id": contact_id,
        "surface": surface,
        "normal_force_n": _force(sample["normal_force_n"], "normal_force_n"),
        "tension_n": _force(sample["tension_n"], "tension_n"),
        "compression_n": _force(sample["compression_n"], "compression_n"),
    }


@dataclass(frozen=True, slots=True)
class SurfaceSummary:
    """Immutable aggregate for one declared contact surface."""

    surface: str
    contacts: int
    normal_force_sum_n: float
    normal_force_max_n: float
    tension_sum_n: float
    compression_sum_n: float

    def to_dict(self) -> dict:
        return {
            "surface": self.surface,
            "contacts": self.contacts,
            "normal_force_sum_n": self.normal_force_sum_n,
            "normal_force_max_n": self.normal_force_max_n,
            "tension_sum_n": self.tension_sum_n,
            "compression_sum_n": self.compression_sum_n,
        }


@dataclass(frozen=True, slots=True)
class ContactTelemetryReport:
    """Detached, immutable result; digest covers the complete report body."""

    schema: str
    provenance: str
    rig_revision: str
    sample_count: int
    first_tick: int | None
    last_tick: int | None
    first_observed_at_ns: int | None
    last_observed_at_ns: int | None
    surfaces: tuple[SurfaceSummary, ...]
    input_sha256: str
    report_sha256: str

    def _body(self) -> dict:
        return {
            "schema": self.schema,
            "provenance": self.provenance,
            "rig_revision": self.rig_revision,
            "sample_count": self.sample_count,
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
            "first_observed_at_ns": self.first_observed_at_ns,
            "last_observed_at_ns": self.last_observed_at_ns,
            "surfaces": [item.to_dict() for item in self.surfaces],
            "input_sha256": self.input_sha256,
        }

    def to_dict(self) -> dict:
        """Return a fresh JSON-compatible value; mutations cannot alter this report."""
        result = self._body()
        result["report_sha256"] = self.report_sha256
        return result


def reduce_contacts(
    samples: list[dict] | tuple[dict, ...],
    *,
    rig_revision: str,
    provenance: str,
) -> ContactTelemetryReport:
    """Validate and aggregate ordered PUBLIC_SYNTHETIC contact samples.

    Multiple contacts may share a tick/time. Ticks and observed clocks may not
    move backwards. A contact_id may occur only once per tick. The sample list
    must already be ordered; this function never sorts caller evidence.
    """
    admitted_revision = _text(
        rig_revision, "rig_revision", _REVISION_RE, MAX_REVISION_LENGTH
    )
    if type(provenance) is not str or provenance != PUBLIC_SYNTHETIC:
        raise ContactTelemetryError("provenance must be explicitly PUBLIC_SYNTHETIC")
    if type(samples) not in (list, tuple):
        raise ContactTelemetryError("samples must be a bounded list or tuple")
    if len(samples) > MAX_SAMPLES:
        raise ContactTelemetryError(f"samples exceeds {MAX_SAMPLES} records")

    normalised: list[dict] = []
    seen: set[tuple[int, str]] = set()
    previous_tick = -1
    previous_time = -1
    for index, sample in enumerate(samples):
        item = _normalise_sample(sample, admitted_revision)
        if item["tick"] < previous_tick:
            raise ContactTelemetryError(f"samples[{index}].tick moves backwards")
        if item["observed_at_ns"] < previous_time:
            raise ContactTelemetryError(f"samples[{index}].observed_at_ns moves backwards")
        identity = (item["tick"], item["contact_id"])
        if identity in seen:
            raise ContactTelemetryError(f"duplicate contact_id in tick {item['tick']}")
        seen.add(identity)
        previous_tick = item["tick"]
        previous_time = item["observed_at_ns"]
        normalised.append(item)

    input_sha256 = _sha256({
        "schema": SCHEMA,
        "provenance": provenance,
        "rig_revision": admitted_revision,
        "samples": normalised,
    })
    summaries: list[SurfaceSummary] = []
    for surface in SURFACES:
        subset = [item for item in normalised if item["surface"] == surface]
        if not subset:
            continue
        summaries.append(SurfaceSummary(
            surface=surface,
            contacts=len(subset),
            normal_force_sum_n=math.fsum(item["normal_force_n"] for item in subset),
            normal_force_max_n=max(item["normal_force_n"] for item in subset),
            tension_sum_n=math.fsum(item["tension_n"] for item in subset),
            compression_sum_n=math.fsum(item["compression_n"] for item in subset),
        ))

    provisional = ContactTelemetryReport(
        schema=SCHEMA,
        provenance=provenance,
        rig_revision=admitted_revision,
        sample_count=len(normalised),
        first_tick=normalised[0]["tick"] if normalised else None,
        last_tick=normalised[-1]["tick"] if normalised else None,
        first_observed_at_ns=normalised[0]["observed_at_ns"] if normalised else None,
        last_observed_at_ns=normalised[-1]["observed_at_ns"] if normalised else None,
        surfaces=tuple(summaries),
        input_sha256=input_sha256,
        report_sha256="",
    )
    report_sha256 = _sha256(provisional._body())
    return ContactTelemetryReport(
        schema=provisional.schema,
        provenance=provisional.provenance,
        rig_revision=provisional.rig_revision,
        sample_count=provisional.sample_count,
        first_tick=provisional.first_tick,
        last_tick=provisional.last_tick,
        first_observed_at_ns=provisional.first_observed_at_ns,
        last_observed_at_ns=provisional.last_observed_at_ns,
        surfaces=provisional.surfaces,
        input_sha256=provisional.input_sha256,
        report_sha256=report_sha256,
    )
