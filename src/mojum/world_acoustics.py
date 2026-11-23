"""Bounded, deterministic, synthetic free-field sound attenuation.

This is an offline geometric sensor, not a room acoustics solver or audio input.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SoundReading:
    distance_m: float
    attenuation_db: float
    received_db: float


def sample_point_source(
    source_xy: tuple[float, float],
    listener_xy: tuple[float, float],
    source_db_at_1m: float,
    *,
    reference_distance_m: float = 1.0,
    minimum_distance_m: float = 0.1,
    maximum_distance_m: float = 1000.0,
) -> SoundReading:
    """Return free-field 20*log10(r/reference) attenuation.

    Distances below minimum_distance_m are clamped to avoid singularities.
    Inputs are explicitly synthetic SI coordinates and a reference dB level.
    No walls, diffraction, absorption, microphone data or propagation delay.
    """
    for name, point in (("source_xy", source_xy), ("listener_xy", listener_xy)):
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            raise ValueError(f"{name} must contain exactly two coordinates")
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1e9 for v in point):
            raise ValueError(f"{name} coordinates must be finite bounded numbers")
    for name, value in (
        ("source_db_at_1m", source_db_at_1m),
        ("reference_distance_m", reference_distance_m),
        ("minimum_distance_m", minimum_distance_m),
        ("maximum_distance_m", maximum_distance_m),
    ):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if not -300 <= source_db_at_1m <= 300:
        raise ValueError("source_db_at_1m outside synthetic safety bounds")
    if not 0 < minimum_distance_m <= reference_distance_m <= maximum_distance_m <= 1e6:
        raise ValueError("invalid distance bounds")
    distance = math.hypot(source_xy[0] - listener_xy[0], source_xy[1] - listener_xy[1])
    if distance > maximum_distance_m:
        raise ValueError("source/listener distance exceeds maximum")
    effective = max(distance, minimum_distance_m)
    attenuation = 20.0 * math.log10(effective / reference_distance_m)
    return SoundReading(
        distance_m=distance,
        attenuation_db=attenuation,
        received_db=source_db_at_1m - attenuation,
    )
