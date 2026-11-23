"""Bounded, offline inertial metadata validation for synthetic rig links.

This module validates caller-supplied SI metadata only.  It does not infer
anatomy, integrate meshes, run a physics engine, or authorize actuation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any


_SCHEMA = "mojum.rig-inertia/1"
_SCOPE = "PUBLIC_SYNTHETIC"
_LINK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\\Z", re.ASCII)
_MAX_LINKS = 256
_MAX_MASS_KG = 1_000_000.0
_MAX_COM_M = 1_000.0
_MAX_INERTIA_KG_M2 = 1_000_000_000_000.0


class RigInertiaError(ValueError):
    """Raised when an inertial metadata packet is invalid or out of bounds."""


@dataclass(frozen=True, slots=True)
class InertialLink:
    """One detached rigid-link inertial record in SI units."""

    link_id: str
    mass_kg: float
    center_of_mass_m: tuple[float, float, float]
    inertia_kg_m2: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]


@dataclass(frozen=True, slots=True)
class RigInertia:
    """Immutable normalized inventory and consistency digest."""

    schema: str
    links: tuple[InertialLink, ...]
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible copy; callers cannot mutate this value."""
        return {
            "schema": self.schema,
            "scope": _SCOPE,
            "unit_system": "SI",
            "links": [
                {
                    "id": link.link_id,
                    "mass_kg": link.mass_kg,
                    "com_m": list(link.center_of_mass_m),
                    "inertia_kg_m2": [list(row) for row in link.inertia_kg_m2],
                }
                for link in self.links
            ],
            "sha256": self.sha256,
        }


def _finite_number(value: object, *, limit: float, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise RigInertiaError("numeric values must be exact int or float types")
    try:
        if not math.isfinite(value) or abs(value) > limit:
            raise RigInertiaError("numeric value is nonfinite or outside the bound")
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RigInertiaError("numeric value is not representable") from exc
    if positive and result <= 0.0:
        raise RigInertiaError("mass must be strictly positive")
    return result


def _vector3(value: object, *, limit: float, field: str) -> tuple[float, float, float]:
    if type(value) is not list or len(value) != 3:
        raise RigInertiaError(f"{field} must be a list of exactly three numbers")
    return tuple(_finite_number(item, limit=limit) for item in value)  # type: ignore[return-value]


def _determinant3(m: tuple[tuple[float, ...], ...]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _principal_moments(m: tuple[tuple[float, ...], ...]) -> tuple[float, float, float]:
    """Return deterministic Jacobi eigenvalue estimates for a symmetric 3x3 matrix."""
    a = [list(row) for row in m]
    pairs = ((0, 1), (0, 2), (1, 2))
    for _ in range(32):
        p, q = max(pairs, key=lambda pair: abs(a[pair[0]][pair[1]]))
        apq = a[p][q]
        scale = max(abs(a[0][0]), abs(a[1][1]), abs(a[2][2]), 1e-300)
        if abs(apq) <= 1e-15 * scale:
            break
        tau = (a[q][q] - a[p][p]) / (2.0 * apq)
        sign = 1.0 if tau >= 0.0 else -1.0
        tangent = sign / (abs(tau) + math.sqrt(1.0 + tau * tau))
        cosine = 1.0 / math.sqrt(1.0 + tangent * tangent)
        sine = tangent * cosine
        app, aqq = a[p][p], a[q][q]
        a[p][p] = app - tangent * apq
        a[q][q] = aqq + tangent * apq
        a[p][q] = a[q][p] = 0.0
        for r in range(3):
            if r == p or r == q:
                continue
            arp, arq = a[r][p], a[r][q]
            a[r][p] = a[p][r] = cosine * arp - sine * arq
            a[r][q] = a[q][r] = sine * arp + cosine * arq
    return tuple(sorted((a[0][0], a[1][1], a[2][2])))  # type: ignore[return-value]


def _inertia_tensor(value: object) -> tuple[tuple[float, float, float], ...]:
    if type(value) is not list or len(value) != 3:
        raise RigInertiaError("inertia tensor must have exactly three rows")
    rows = []
    for row in value:
        if type(row) is not list or len(row) != 3:
            raise RigInertiaError("inertia tensor rows must have exactly three values")
        rows.append(tuple(
            _finite_number(item, limit=_MAX_INERTIA_KG_M2) for item in row
        ))
    m = tuple(rows)
    if any(m[i][j] != m[j][i] for i in range(3) for j in range(i + 1, 3)):
        raise RigInertiaError("inertia tensor must be exactly symmetric")

    # Sylvester's criterion for strict positive definiteness.
    minor2 = m[0][0] * m[1][1] - m[0][1] * m[1][0]
    determinant = _determinant3(m)
    if m[0][0] <= 0.0 or minor2 <= 0.0 or determinant <= 0.0:
        raise RigInertiaError("inertia tensor must be positive definite")

    # A realizable rigid body's principal moments obey the triangle inequality.
    moments = _principal_moments(m)
    largest = moments[2]
    tolerance = 1e-10 * max(abs(largest), abs(moments[0]), abs(moments[1]), 1e-300)
    if moments[0] <= 0.0 or largest > moments[0] + moments[1] + tolerance:
        raise RigInertiaError("principal moments violate the rigid-body triangle inequality")
    return m  # type: ignore[return-value]


def validate_rig_inertia(payload: object) -> RigInertia:
    """Validate and detach a bounded PUBLIC_SYNTHETIC rigid-link inventory.

    Input schema:
      {"schema": "mojum.rig-inertia/1", "scope": "PUBLIC_SYNTHETIC",
       "unit_system": "SI", "links": [{"id", "mass_kg", "com_m",
       "inertia_kg_m2"}, ...]}

    Link order is normalized by ASCII link ID, so input ordering does not
    affect the digest.  The digest is a consistency checksum, not a signature.
    """
    if type(payload) is not dict or set(payload) != {
        "schema", "scope", "unit_system", "links",
    }:
        raise RigInertiaError("invalid top-level fields")
    if payload["schema"] != _SCHEMA or type(payload["schema"]) is not str:
        raise RigInertiaError("unsupported schema")
    if payload["scope"] != _SCOPE or type(payload["scope"]) is not str:
        raise RigInertiaError("explicit PUBLIC_SYNTHETIC scope is required")
    if payload["unit_system"] != "SI" or type(payload["unit_system"]) is not str:
        raise RigInertiaError("SI units are required")
    raw_links = payload["links"]
    if type(raw_links) is not list or not 1 <= len(raw_links) <= _MAX_LINKS:
        raise RigInertiaError("link inventory must contain 1..256 links")

    links: list[InertialLink] = []
    seen: set[str] = set()
    for raw in raw_links:
        if type(raw) is not dict or set(raw) != {
            "id", "mass_kg", "com_m", "inertia_kg_m2",
        }:
            raise RigInertiaError("invalid link fields")
        link_id = raw["id"]
        if type(link_id) is not str or _LINK_ID.fullmatch(link_id) is None:
            raise RigInertiaError("invalid link ID")
        if link_id in seen:
            raise RigInertiaError("duplicate link ID")
        seen.add(link_id)
        mass = _finite_number(raw["mass_kg"], limit=_MAX_MASS_KG, positive=True)
        center = _vector3(raw["com_m"], limit=_MAX_COM_M, field="com_m")
        inertia = _inertia_tensor(raw["inertia_kg_m2"])
        links.append(InertialLink(link_id, mass, center, inertia))

    links.sort(key=lambda link: link.link_id)
    canonical = {
        "schema": _SCHEMA,
        "scope": _SCOPE,
        "unit_system": "SI",
        "links": [
            {
                "id": link.link_id,
                "mass_kg": link.mass_kg,
                "com_m": list(link.center_of_mass_m),
                "inertia_kg_m2": [list(row) for row in link.inertia_kg_m2],
            }
            for link in links
        ],
    }
    try:
        wire = json.dumps(
            canonical, sort_keys=True, ensure_ascii=True,
            allow_nan=False, separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise RigInertiaError("could not canonicalize inertial inventory") from exc
    return RigInertia(_SCHEMA, tuple(links), hashlib.sha256(wire).hexdigest())
