"""Strict offline execution-profile admission for optional MuJoCo CPU runs.

This module validates caller intent only. It never imports MuJoCo or starts a
solver, opens paths, accesses a journal, or grants actuator authority.
"""
from dataclasses import dataclass
import hashlib
import json

SCHEMA = "mojum.mujoco.cpu-profile/1"
MAX_JSON_BYTES = 8 * 1024
MAX_JSON_DEPTH = 8
MIN_TIMESTEP_NS = 1_000
MAX_TIMESTEP_NS = 100_000_000
MAX_STEPS = 10_000_000
MAX_WARMUP_STEPS = 1_000_000
MAX_CPU_THREADS = 16
MAX_SIMULATED_NS = 86_400 * 1_000_000_000
_INTEGRATORS = frozenset(("euler", "implicit", "implicitfast"))
_SOLVERS = frozenset(("newton", "cg"))
_FIELDS = frozenset((
    "schema", "enabled", "timestep_ns", "steps", "warmup_steps",
    "cpu_threads", "integrator", "solver",
))


class ProfileError(ValueError):
    """A malformed or unsupported CPU execution profile."""


@dataclass(frozen=True)
class CpuProfile:
    """Detached, immutable normalized profile; digest is not authentication."""

    enabled: bool
    timestep_ns: int
    steps: int
    warmup_steps: int
    cpu_threads: int
    integrator: str
    solver: str
    digest: str

    def to_mapping(self):
        """Return a new mutable mapping without exposing internal state."""
        return {
            "schema": SCHEMA,
            "enabled": self.enabled,
            "timestep_ns": self.timestep_ns,
            "steps": self.steps,
            "warmup_steps": self.warmup_steps,
            "cpu_threads": self.cpu_threads,
            "integrator": self.integrator,
            "solver": self.solver,
        }


def _canonical_bytes(mapping):
    return json.dumps(
        mapping, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("ascii")


def _validate_depth(text):
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == chr(92):
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ProfileError("profile JSON nesting depth exceeded")
        elif char in "]}" and depth:
            depth -= 1


def _pairs_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("duplicate profile JSON key")
        result[key] = value
    return result


def _reject_constant(_token):
    raise ProfileError("non-finite JSON constants are not supported")


def _normalize(value):
    if type(value) is not dict:
        raise ProfileError("profile must be a native dict")
    if any(type(key) is not str for key in value):
        raise ProfileError("profile keys must be native strings")
    if frozenset(value) != _FIELDS:
        raise ProfileError("profile has missing or unknown fields")
    if type(value["schema"]) is not str or value["schema"] != SCHEMA:
        raise ProfileError("unsupported profile schema")
    if type(value["enabled"]) is not bool:
        raise ProfileError("enabled must be a native bool")

    integer_bounds = {
        "timestep_ns": (MIN_TIMESTEP_NS, MAX_TIMESTEP_NS),
        "steps": (1, MAX_STEPS),
        "warmup_steps": (0, MAX_WARMUP_STEPS),
        "cpu_threads": (1, MAX_CPU_THREADS),
    }
    normalized = {"schema": SCHEMA, "enabled": value["enabled"]}
    for field, (minimum, maximum) in integer_bounds.items():
        number = value[field]
        if type(number) is not int:
            raise ProfileError(f"{field} must be a native integer")
        if not minimum <= number <= maximum:
            raise ProfileError(f"{field} is outside its supported bounds")
        normalized[field] = number

    if normalized["warmup_steps"] > normalized["steps"]:
        raise ProfileError("warmup_steps cannot exceed steps")
    if normalized["steps"] * normalized["timestep_ns"] > MAX_SIMULATED_NS:
        raise ProfileError("requested simulated duration exceeds one day")

    integrator = value["integrator"]
    solver = value["solver"]
    if type(integrator) is not str or integrator not in _INTEGRATORS:
        raise ProfileError("unsupported integrator")
    if type(solver) is not str or solver not in _SOLVERS:
        raise ProfileError("unsupported solver")
    normalized["integrator"] = integrator
    normalized["solver"] = solver
    return normalized


def validate_profile(value):
    """Validate a native mapping and return a detached immutable profile."""
    try:
        normalized = _normalize(value)
        digest = hashlib.sha256(_canonical_bytes(normalized)).hexdigest()
        return CpuProfile(
            enabled=normalized["enabled"],
            timestep_ns=normalized["timestep_ns"],
            steps=normalized["steps"],
            warmup_steps=normalized["warmup_steps"],
            cpu_threads=normalized["cpu_threads"],
            integrator=normalized["integrator"],
            solver=normalized["solver"],
            digest=digest,
        )
    except ProfileError:
        raise
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ProfileError("invalid CPU execution profile") from exc


def loads_profile(raw):
    """Parse bounded JSON text/bytes with duplicate-key and depth rejection."""
    if type(raw) is bytes:
        if len(raw) > MAX_JSON_BYTES:
            raise ProfileError("profile JSON exceeds byte budget")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProfileError("profile JSON must be UTF-8") from exc
    elif type(raw) is str:
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ProfileError("profile JSON contains invalid Unicode") from exc
        if len(encoded) > MAX_JSON_BYTES:
            raise ProfileError("profile JSON exceeds byte budget")
        text = raw
    else:
        raise ProfileError("profile JSON must be native str or bytes")
    _validate_depth(text)
    try:
        parsed = json.loads(
            text, object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ProfileError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ProfileError("malformed profile JSON") from exc
    return validate_profile(parsed)
