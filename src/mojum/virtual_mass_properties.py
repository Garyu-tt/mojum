"""Bounded, opt-in rigid-body mass-property aggregation for synthetic fixtures.

This module is metadata/math only. It never loads a physics engine or controls a body.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

SCHEMA = "mojum.virtual-mass-properties/1"
VISIBILITY = "PUBLIC_SYNTHETIC"
MAX_SEGMENTS = 64
MAX_ID_LENGTH = 64
MAX_MASS_KG = 10_000.0
MAX_TOTAL_MASS_KG = 100_000.0
MAX_POSITION_M = 10_000.0
MAX_INERTIA_KG_M2 = 100_000_000.0
_ROTATION_TOLERANCE = 1e-8
_TENSOR_TOLERANCE = 1e-10


class MassPropertiesError(ValueError):
    """Raised when a mass-property fixture is malformed or exceeds its budget."""


@dataclass(frozen=True)
class MassProperties:
    """Detached aggregate properties in world coordinates and SI units."""

    schema: str
    segment_count: int
    total_mass_kg: float
    center_of_mass_m: tuple[float, float, float]
    inertia_about_com_kg_m2: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]
    input_digest: str
    digest: str

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-shaped representation."""
        return {
            "schema": self.schema,
            "segment_count": self.segment_count,
            "total_mass_kg": self.total_mass_kg,
            "center_of_mass_m": list(self.center_of_mass_m),
            "inertia_about_com_kg_m2": [list(row) for row in self.inertia_about_com_kg_m2],
            "input_digest": self.input_digest,
            "digest": self.digest,
        }


def _fail(message: str) -> None:
    raise MassPropertiesError(message)


def _require_exact_keys(value: dict[str, object], expected: set[str], field: str) -> None:
    # Check key types BEFORE set comparison so hostile non-string keys cannot
    # execute caller-defined equality/hash behavior at this trust boundary.
    keys = tuple(value.keys())
    if any(type(key) is not str for key in keys):
        _fail(f"{field}: keys must be native strings")
    if set(keys) != expected:
        _fail(f"{field}: missing or unknown fields")


def _number(value: object, field: str, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        _fail(f"{field}: expected a native finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        _fail(f"{field}: number is outside the supported range")
    if not math.isfinite(result) or result < minimum or result > maximum:
        _fail(f"{field}: number is outside the supported range")
    return 0.0 if result == 0.0 else result


def _vector(value: object, field: str, *, bound: float) -> tuple[float, float, float]:
    if type(value) is not list or len(value) != 3:
        _fail(f"{field}: expected a three-element native list")
    return tuple(_number(item, f"{field}[{index}]", minimum=-bound, maximum=bound)
                 for index, item in enumerate(value))  # type: ignore[return-value]


def _matrix(value: object, field: str, *, bound: float) -> tuple[tuple[float, float, float], ...]:
    if type(value) is not list or len(value) != 3:
        _fail(f"{field}: expected a 3x3 native list")
    rows = tuple(_vector(row, f"{field}[{index}]", bound=bound) for index, row in enumerate(value))
    return rows


def _determinant3(matrix: tuple[tuple[float, float, float], ...]) -> float:
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _validate_rotation(matrix: tuple[tuple[float, float, float], ...], field: str) -> None:
    # R must be a proper rotation, not merely an orthogonal reflection.
    for row in range(3):
        for col in range(3):
            dot = math.fsum(matrix[row][k] * matrix[col][k] for k in range(3))
            expected = 1.0 if row == col else 0.0
            if abs(dot - expected) > _ROTATION_TOLERANCE:
                _fail(f"{field}: matrix is not orthonormal")
    determinant = _determinant3(matrix)
    if abs(determinant - 1.0) > _ROTATION_TOLERANCE:
        _fail(f"{field}: determinant must be +1")


def _require_positive_semidefinite(
    matrix: tuple[tuple[float, float, float], ...], field: str
) -> None:
    scale = max(1.0, *(abs(item) for row in matrix for item in row))
    tolerance = _TENSOR_TOLERANCE * scale * scale
    for index in range(3):
        if matrix[index][index] < -tolerance:
            _fail(f"{field}: matrix must be positive semidefinite")
    for a, b in ((0, 1), (0, 2), (1, 2)):
        minor = matrix[a][a] * matrix[b][b] - matrix[a][b] * matrix[b][a]
        if minor < -tolerance:
            _fail(f"{field}: matrix must be positive semidefinite")
    if _determinant3(matrix) < -tolerance * scale:
        _fail(f"{field}: matrix must be positive semidefinite")


def _validate_inertia(matrix: tuple[tuple[float, float, float], ...], field: str) -> tuple[tuple[float, float, float], ...]:
    scale = max(1.0, *(abs(item) for row in matrix for item in row))
    for row in range(3):
        for col in range(row + 1, 3):
            if abs(matrix[row][col] - matrix[col][row]) > _TENSOR_TOLERANCE * scale:
                _fail(f"{field}: tensor must be symmetric")
    # Remove accepted floating-point noise in symmetric off-diagonal pairs.
    symmetric = tuple(
        tuple(
            matrix[row][row] if row == col
            else (matrix[row][col] + matrix[col][row]) / 2.0
            for col in range(3)
        )
        for row in range(3)
    )
    _require_positive_semidefinite(symmetric, field)
    # A physical inertia tensor must also obey the principal-moment triangle
    # inequalities. Equivalently, its second-moment matrix must be PSD.
    trace_half = math.fsum(symmetric[index][index] for index in range(3)) / 2.0
    second_moment = tuple(
        tuple((trace_half if row == col else 0.0) - symmetric[row][col] for col in range(3))
        for row in range(3)
    )
    _require_positive_semidefinite(second_moment, f"{field} triangle inequality")
    return symmetric


def _matmul3(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return tuple(
        tuple(math.fsum(left[i][k] * right[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _transpose3(matrix: tuple[tuple[float, float, float], ...]) -> tuple[tuple[float, float, float], ...]:
    return tuple(tuple(matrix[j][i] for j in range(3)) for i in range(3))


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_mass_properties(raw: object) -> MassProperties:
    """Validate and aggregate an explicit PUBLIC_SYNTHETIC rigid-body fixture.

    Each segment supplies its mass, local COM, local COM inertia tensor, and a
    proper local-to-world rigid transform. Inertia is rotated into world space
    and shifted to the aggregate COM using the parallel-axis theorem.
    """
    if type(raw) is not dict:
        _fail("fixture: expected a native dict")
    _require_exact_keys(raw, {"schema", "visibility", "segments"}, "fixture")
    if type(raw["schema"]) is not str or raw["schema"] != SCHEMA:
        _fail("fixture: unsupported schema")
    if type(raw["visibility"]) is not str or raw["visibility"] != VISIBILITY:
        _fail("fixture: PUBLIC_SYNTHETIC visibility is required")
    segments = raw["segments"]
    if type(segments) is not list or not 1 <= len(segments) <= MAX_SEGMENTS:
        _fail(f"segments: expected 1..{MAX_SEGMENTS} native records")

    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    total_mass_parts: list[float] = []
    for index, segment in enumerate(segments):
        field = f"segments[{index}]"
        if type(segment) is not dict:
            _fail(f"{field}: expected a native dict")
        required = {"id", "mass_kg", "center_m", "inertia_kg_m2", "rotation", "translation_m"}
        _require_exact_keys(segment, required, field)
        identifier = segment["id"]
        if type(identifier) is not str or not identifier or len(identifier) > MAX_ID_LENGTH:
            _fail(f"{field}.id: invalid identifier")
        if any(ord(char) < 0x21 or ord(char) > 0x7E for char in identifier):
            _fail(f"{field}.id: identifier must be printable ASCII without spaces")
        if identifier in seen:
            _fail(f"{field}.id: duplicate identifier")
        seen.add(identifier)

        mass = _number(segment["mass_kg"], f"{field}.mass_kg", minimum=1e-12, maximum=MAX_MASS_KG)
        center = _vector(segment["center_m"], f"{field}.center_m", bound=MAX_POSITION_M)
        inertia = _matrix(segment["inertia_kg_m2"], f"{field}.inertia_kg_m2", bound=MAX_INERTIA_KG_M2)
        inertia = _validate_inertia(inertia, f"{field}.inertia_kg_m2")
        rotation = _matrix(segment["rotation"], f"{field}.rotation", bound=1.0)
        _validate_rotation(rotation, f"{field}.rotation")
        translation = _vector(segment["translation_m"], f"{field}.translation_m", bound=MAX_POSITION_M)

        world_center = tuple(
            math.fsum(rotation[row][col] * center[col] for col in range(3)) + translation[row]
            for row in range(3)
        )
        rotated_inertia = _matmul3(_matmul3(rotation, inertia), _transpose3(rotation))
        if not all(math.isfinite(value) for value in (*world_center, *(v for r in rotated_inertia for v in r))):
            _fail(f"{field}: transform overflow")
        normalized.append({
            "id": identifier,
            "mass_kg": mass,
            "center_m": list(center),
            "inertia_kg_m2": [list(row) for row in inertia],
            "rotation": [list(row) for row in rotation],
            "translation_m": list(translation),
        })
        total_mass_parts.append(mass)

    total_mass = math.fsum(total_mass_parts)
    if not math.isfinite(total_mass) or total_mass > MAX_TOTAL_MASS_KG:
        _fail("segments: total mass exceeds the supported budget")

    normalized.sort(key=lambda item: item["id"])
    world_centers: list[tuple[float, float, float]] = []
    rotated_tensors: list[tuple[tuple[float, float, float], ...]] = []
    ordered_masses: list[float] = []
    for item in normalized:
        mass = item["mass_kg"]
        center = item["center_m"]
        rotation = item["rotation"]
        translation = item["translation_m"]
        assert type(mass) is float and type(center) is list and type(rotation) is list and type(translation) is list
        world_centers.append(tuple(
            math.fsum(rotation[row][col] * center[col] for col in range(3)) + translation[row]
            for row in range(3)
        ))
        r = tuple(tuple(row) for row in rotation)
        tensor = tuple(tuple(row) for row in item["inertia_kg_m2"])
        rotated_tensors.append(_matmul3(_matmul3(r, tensor), _transpose3(r)))
        ordered_masses.append(mass)

    center_of_mass = tuple(
        math.fsum(ordered_masses[index] * world_centers[index][axis] for index in range(len(normalized))) / total_mass
        for axis in range(3)
    )
    inertia_terms: list[list[list[float]]] = [[[] for _ in range(3)] for _ in range(3)]
    for index, mass in enumerate(ordered_masses):
        delta = tuple(world_centers[index][axis] - center_of_mass[axis] for axis in range(3))
        distance_squared = math.fsum(value * value for value in delta)
        tensor = rotated_tensors[index]
        for row in range(3):
            for col in range(3):
                parallel_axis = mass * ((distance_squared if row == col else 0.0) - delta[row] * delta[col])
                inertia_terms[row][col].append(tensor[row][col] + parallel_axis)

    aggregate = tuple(
        tuple(math.fsum(inertia_terms[row][col]) for col in range(3))
        for row in range(3)
    )
    values = (total_mass, *center_of_mass, *(value for row in aggregate for value in row))
    if not all(math.isfinite(value) for value in values):
        _fail("result: aggregate overflow")
    # Remove signed zero to keep the canonical representation stable.
    center_of_mass = tuple(0.0 if value == 0.0 else value for value in center_of_mass)
    aggregate = tuple(tuple(0.0 if value == 0.0 else value for value in row) for row in aggregate)

    canonical_input = {
        "schema": SCHEMA,
        "visibility": VISIBILITY,
        "segments": normalized,
    }
    input_digest = hashlib.sha256(_canonical_bytes(canonical_input)).hexdigest()
    result_payload = {
        "schema": SCHEMA,
        "segment_count": len(normalized),
        "total_mass_kg": total_mass,
        "center_of_mass_m": list(center_of_mass),
        "inertia_about_com_kg_m2": [list(row) for row in aggregate],
        "input_digest": input_digest,
    }
    digest = hashlib.sha256(_canonical_bytes(result_payload)).hexdigest()
    return MassProperties(
        schema=SCHEMA,
        segment_count=len(normalized),
        total_mass_kg=total_mass,
        center_of_mass_m=center_of_mass,  # type: ignore[arg-type]
        inertia_about_com_kg_m2=aggregate,  # type: ignore[arg-type]
        input_digest=input_digest,
        digest=digest,
    )
