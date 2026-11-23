"""Versioned, solver-neutral cloth material parameter profiles.

All numeric fields are integer fixed-point SI quantities. This module validates
caller-supplied synthetic estimates; it does not calibrate or execute a solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import ClassVar


class ClothMaterialError(ValueError):
    """Raised when a cloth material profile is malformed or out of bounds."""


@dataclass(frozen=True, slots=True)
class ClothMaterialProfile:
    """Immutable material coefficients expressed in documented fixed-point SI units.

    Scales:
      * areal_density_ug_m2: 1e-6 kg/m²
      * thickness_nm: 1e-9 m
      * stretch_stiffness_uN_m: 1e-6 N/m
      * bend_stiffness_uNm: 1e-6 N·m
      * damping_ratio_ppm / friction_ppm: 1e-6 dimensionless
    """

    SCHEMA: ClassVar[str] = "mojum.cloth.material-profile/1"
    CLASSIFICATION: ClassVar[str] = "PUBLIC_SYNTHETIC"

    schema: str
    classification: str
    areal_density_ug_m2: int
    thickness_nm: int
    stretch_stiffness_uN_m: int
    bend_stiffness_uNm: int
    damping_ratio_ppm: int
    static_friction_ppm: int
    dynamic_friction_ppm: int

    _BOUNDS: ClassVar[dict[str, tuple[int, int]]] = {
        # Upper bounds are admission/resource-safety limits, not material claims.
        "areal_density_ug_m2": (1, 1_000_000_000_000),
        "thickness_nm": (1, 1_000_000_000),
        "stretch_stiffness_uN_m": (1, 1_000_000_000_000_000),
        "bend_stiffness_uNm": (0, 1_000_000_000_000),
        "damping_ratio_ppm": (0, 1_000_000),
        "static_friction_ppm": (0, 10_000_000),
        "dynamic_friction_ppm": (0, 10_000_000),
    }

    def __post_init__(self) -> None:
        if type(self.schema) is not str or self.schema != self.SCHEMA:
            raise ClothMaterialError("unsupported material profile schema")
        if type(self.classification) is not str or self.classification != self.CLASSIFICATION:
            raise ClothMaterialError("material profile must be PUBLIC_SYNTHETIC")
        for field, (minimum, maximum) in self._BOUNDS.items():
            value = getattr(self, field)
            if type(value) is not int:
                raise ClothMaterialError(f"{field} must be an exact integer")
            if value < minimum or value > maximum:
                raise ClothMaterialError(f"{field} is outside the supported range")
        if self.dynamic_friction_ppm > self.static_friction_ppm:
            raise ClothMaterialError("dynamic friction cannot exceed static friction")

    @classmethod
    def from_mapping(cls, value: object) -> "ClothMaterialProfile":
        """Validate a plain built-in dict without invoking foreign mapping code."""
        if type(value) is not dict:
            raise ClothMaterialError("profile must be an exact built-in dict")

        keys = tuple(dict.keys(value))
        # Reject non-native keys before hashing/comparing them in set operations.
        if any(type(key) is not str for key in keys):
            raise ClothMaterialError("profile keys must be exact strings")

        expected = (
            "schema",
            "classification",
            "areal_density_ug_m2",
            "thickness_nm",
            "stretch_stiffness_uN_m",
            "bend_stiffness_uNm",
            "damping_ratio_ppm",
            "static_friction_ppm",
            "dynamic_friction_ppm",
        )
        if len(keys) != len(expected) or set(keys) != set(expected):
            raise ClothMaterialError("profile fields do not match the versioned schema")

        # The exact dict check above means these lookups cannot dispatch to user code.
        fields = {key: dict.__getitem__(value, key) for key in expected}
        return cls(**fields)

    def to_mapping(self) -> dict[str, str | int]:
        """Return a detached canonical mapping; callers may mutate it safely."""
        return {
            "schema": self.schema,
            "classification": self.classification,
            "areal_density_ug_m2": self.areal_density_ug_m2,
            "thickness_nm": self.thickness_nm,
            "stretch_stiffness_uN_m": self.stretch_stiffness_uN_m,
            "bend_stiffness_uNm": self.bend_stiffness_uNm,
            "damping_ratio_ppm": self.damping_ratio_ppm,
            "static_friction_ppm": self.static_friction_ppm,
            "dynamic_friction_ppm": self.dynamic_friction_ppm,
        }

    @property
    def fingerprint(self) -> str:
        """Stable unkeyed content digest; it is not a signature or authentication."""
        payload = json.dumps(
            self.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()


def validate_material_profile(value: object) -> ClothMaterialProfile:
    """Public convenience validator for an untrusted in-memory profile."""
    return ClothMaterialProfile.from_mapping(value)
