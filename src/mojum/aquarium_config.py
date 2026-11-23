"""Strict, offline validation for versioned Aquarium launch profiles.

This module is deliberately a preflight contract only. It never opens paths,
creates an Aquarium, reads environment variables, or touches a journal.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from typing import Final


SCHEMA: Final = "mojum.aquarium.launch-profile/1"
MAX_PROFILE_BYTES: Final = 16_384
MAX_PATH_CHARS: Final = 512
MAX_TICKS: Final = 1_000
MIN_DT_NS: Final = 1_000
MAX_DT_NS: Final = 1_000_000_000

MOTOR_MODES: Final = frozenset({
    "scripted", "developmental_fixture", "local_cell_v2b",
    "local_cell_v3", "local_cell_v3_escape", "local_cell_v4",
    "local_cell_v2", "local_cell_v2_a_archive",
})
NEURAL_MODES: Final = frozenset({"shadow", "disabled"})
BRAIN_MODES: Final = frozenset({"placeholder", "developmental_controller"})
SENSORY_MODES: Final = frozenset({"none", "toy_probe", "toy_probe_v2"})
TEMPORAL_MODES: Final = frozenset({"none", "range_guided"})
PULSE_SUMS: Final = frozenset({"auto", "ordered", "compensated"})

_DEFAULTS: Final = {
    "ticks": 120,
    "dt_ns": 10_000_000,
    "neural_mode": "shadow",
    "motor_mode": "scripted",
    "brain_mode": "placeholder",
    "sensory_mode": "none",
    "temporal_mode": "none",
    "pulse_sum": "auto",
    "read_only": False,
    "room_file": None,
}
_ALLOWED_KEYS: Final = frozenset({"schema", "db_path", *_DEFAULTS})


class AquariumConfigError(ValueError):
    """A launch profile is malformed, unsupported, or unsafe to admit."""


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    """Detached, immutable and normalized Aquarium launch intent."""

    schema: str
    db_path: str
    ticks: int
    dt_ns: int
    neural_mode: str
    motor_mode: str
    brain_mode: str
    sensory_mode: str
    temporal_mode: str
    pulse_sum: str
    read_only: bool
    room_file: str | None
    sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return a fresh detached JSON-shaped representation."""
        result = asdict(self)
        return result

    def runtime_options(self) -> dict[str, object]:
        """Return validated options, excluding schema/path/digest metadata."""
        return {
            "ticks": self.ticks,
            "dt_ns": self.dt_ns,
            "neural_mode": self.neural_mode,
            "motor_mode": self.motor_mode,
            "brain_mode": self.brain_mode,
            "sensory_mode": self.sensory_mode,
            "temporal_mode": self.temporal_mode,
            "pulse_sum": self.pulse_sum,
            "read_only": self.read_only,
            "room_file": self.room_file,
        }


def _reject_constant(value: str) -> None:
    raise AquariumConfigError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AquariumConfigError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_source(source: object) -> dict[str, object]:
    if type(source) is str:
        # UTF-8 requires at least one byte per code point. Bound character
        # count before allocating the encoded copy, then enforce the exact
        # byte budget for multibyte input.
        if len(source) > MAX_PROFILE_BYTES:
            raise AquariumConfigError("profile exceeds the byte budget")
        try:
            raw = source.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise AquariumConfigError("profile text is not valid UTF-8") from exc
        if len(raw) > MAX_PROFILE_BYTES:
            raise AquariumConfigError("profile exceeds the byte budget")
        try:
            value = json.loads(
                source,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except AquariumConfigError:
            raise
        except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
            raise AquariumConfigError("profile is not valid bounded JSON") from exc
    elif type(source) is dict:
        value = source
    else:
        raise AquariumConfigError("profile must be exact dict or JSON text")

    if type(value) is not dict:
        raise AquariumConfigError("profile root must be a JSON object")
    if len(value) > len(_ALLOWED_KEYS):
        raise AquariumConfigError("profile has too many fields")
    for key in value:
        if type(key) is not str or len(key) > 64:
            raise AquariumConfigError("profile keys must be exact bounded strings")
    return value


def _text_path(value: object, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str or not value or len(value) > MAX_PATH_CHARS:
        raise AquariumConfigError(f"{field} must be nonempty text of at most {MAX_PATH_CHARS} characters")
    if value != value.strip():
        raise AquariumConfigError(f"{field} must not have leading or trailing whitespace")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value):
        raise AquariumConfigError(f"{field} contains control or invisible characters")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise AquariumConfigError(f"{field} is not valid UTF-8 text") from exc
    return value


def _exact_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AquariumConfigError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    if type(value) is not str or len(value) > 64 or value not in allowed:
        raise AquariumConfigError(f"unsupported {field}")
    return value


def _canonical(payload: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise AquariumConfigError("profile cannot be represented canonically") from exc


def validate_profile(source: object) -> LaunchProfile:
    """Validate a native mapping or JSON text into a detached launch profile.

    The caller owns all I/O. Path fields are inert text and are never opened.
    """
    data = _read_source(source)
    unknown = set(data) - _ALLOWED_KEYS
    if unknown:
        raise AquariumConfigError(f"unknown profile field: {sorted(unknown)[0]}")
    schema_value = data.get("schema")
    if type(schema_value) is not str or len(schema_value) != len(SCHEMA) or schema_value != SCHEMA:
        raise AquariumConfigError(f"schema must be {SCHEMA}")
    if "db_path" not in data:
        raise AquariumConfigError("db_path is required")

    values: dict[str, object] = dict(_DEFAULTS)
    values.update({key: value for key, value in data.items() if key not in ("schema", "db_path")})

    db_path = _text_path(data["db_path"], "db_path")
    room_file = _text_path(values["room_file"], "room_file", optional=True)
    motor_mode = _enum(values["motor_mode"], "motor_mode", MOTOR_MODES)
    ticks = _exact_int(values["ticks"], "ticks", 0, MAX_TICKS)
    if motor_mode == "local_cell_v2_a_archive":
        if ticks != 0:
            raise AquariumConfigError("local_cell_v2_a_archive requires ticks=0")
    elif ticks == 0:
        raise AquariumConfigError("ticks=0 is reserved for local_cell_v2_a_archive")
    if room_file is not None and motor_mode in {
        "local_cell_v2b", "local_cell_v3", "local_cell_v3_escape",
        "local_cell_v4", "local_cell_v2", "local_cell_v2_a_archive",
    }:
        raise AquariumConfigError("room_file is incompatible with fixed-geometry local-cell motor modes")
    dt_ns = _exact_int(values["dt_ns"], "dt_ns", MIN_DT_NS, MAX_DT_NS)
    read_only = values["read_only"]
    if type(read_only) is not bool:
        raise AquariumConfigError("read_only must be a boolean")

    neural_mode = _enum(values["neural_mode"], "neural_mode", NEURAL_MODES)
    brain_mode = _enum(values["brain_mode"], "brain_mode", BRAIN_MODES)
    sensory_mode = _enum(values["sensory_mode"], "sensory_mode", SENSORY_MODES)
    temporal_mode = _enum(values["temporal_mode"], "temporal_mode", TEMPORAL_MODES)
    pulse_sum = _enum(values["pulse_sum"], "pulse_sum", PULSE_SUMS)

    if brain_mode == "developmental_controller" and motor_mode != "developmental_fixture":
        raise AquariumConfigError("developmental_controller requires developmental_fixture motor mode")
    if sensory_mode == "toy_probe" and motor_mode != "developmental_fixture":
        raise AquariumConfigError("toy_probe requires developmental_fixture motor mode")
    if sensory_mode == "toy_probe_v2" and motor_mode not in ("developmental_fixture", "local_cell_v4"):
        raise AquariumConfigError("toy_probe_v2 requires developmental_fixture or local_cell_v4 motor mode")
    if motor_mode == "local_cell_v4" and sensory_mode == "toy_probe":
        raise AquariumConfigError("local_cell_v4 is incompatible with legacy toy_probe")
    if temporal_mode == "range_guided" and sensory_mode != "toy_probe":
        raise AquariumConfigError("range_guided requires toy_probe sensory mode")
    if pulse_sum != "auto" and neural_mode == "disabled":
        raise AquariumConfigError("explicit pulse_sum requires neural shadow mode")

    normalized: dict[str, object] = {
        "schema": SCHEMA,
        "db_path": db_path,
        "ticks": ticks,
        "dt_ns": dt_ns,
        "neural_mode": neural_mode,
        "motor_mode": motor_mode,
        "brain_mode": brain_mode,
        "sensory_mode": sensory_mode,
        "temporal_mode": temporal_mode,
        "pulse_sum": pulse_sum,
        "read_only": read_only,
        "room_file": room_file,
    }
    digest = hashlib.sha256(_canonical(normalized)).hexdigest()
    return LaunchProfile(
        schema=SCHEMA, db_path=db_path, ticks=ticks, dt_ns=dt_ns,
        neural_mode=neural_mode, motor_mode=motor_mode, brain_mode=brain_mode,
        sensory_mode=sensory_mode, temporal_mode=temporal_mode, pulse_sum=pulse_sum,
        read_only=read_only, room_file=room_file, sha256=digest,
    )


def load_profile_text(text: str) -> LaunchProfile:
    """Explicitly named JSON-text entry point for callers with existing bytes."""
    return validate_profile(text)
