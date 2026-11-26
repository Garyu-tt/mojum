"""Optional dm_control CPU benchmark for a bounded synthetic point-room fixture.

This is a benchmark probe, not a drop-in replacement for mojum.world_room.
The default module import path has no dm_control dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import math
import time
import tracemalloc
import xml.etree.ElementTree as ET


SCHEMA = "mojum.dm-control-benchmark/1"
MAX_STEPS = 20_000
MAX_OBSTACLES = 16
MAX_ROOM_EDGE_M = 1_000.0
MAX_REPORT_BYTES = 32 * 1024
MAX_CONTROL = 4.0
POINT_RADIUS_M = 0.03


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _number(value: float) -> str:
    return format(float(value), ".17g")


def _sha256_hex(value: str) -> bool:
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    """Exact, bounded synthetic 2D point-mass workload."""

    width_m: float = 3.0
    height_m: float = 2.4
    start_x_m: float = 0.4
    start_y_m: float = 0.4
    steps: int = 600
    timestep_ns: int = 2_000_000
    control_period: int = 60
    control_pattern: tuple[tuple[float, float], ...] = (
        (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)
    )
    obstacles_m: tuple[tuple[float, float, float, float], ...] = (
        (1.2, 0.25, 0.25, 1.1),
        (2.1, 1.25, 0.55, 0.30),
    )

    def __post_init__(self) -> None:
        for name in ("width_m", "height_m", "start_x_m", "start_y_m"):
            value = getattr(self, name)
            if not _finite_number(value):
                raise ValueError(f"{name} must be a finite native number")
        if (self.width_m <= 2 * POINT_RADIUS_M or self.height_m <= 2 * POINT_RADIUS_M
                or self.width_m > MAX_ROOM_EDGE_M or self.height_m > MAX_ROOM_EDGE_M):
            raise ValueError("room dimensions are outside the bounded point-body profile")
        if not (POINT_RADIUS_M <= self.start_x_m <= self.width_m - POINT_RADIUS_M
                and POINT_RADIUS_M <= self.start_y_m <= self.height_m - POINT_RADIUS_M):
            raise ValueError("start position is outside the valid room interior")
        if type(self.steps) is not int or not 1 <= self.steps <= MAX_STEPS:
            raise ValueError(f"steps must be an integer in [1, {MAX_STEPS}]")
        if type(self.timestep_ns) is not int or not 100_000 <= self.timestep_ns <= 20_000_000:
            raise ValueError("timestep_ns must be an integer in [100000, 20000000]")
        if type(self.control_period) is not int or not 1 <= self.control_period <= 1000:
            raise ValueError("control_period must be an integer in [1, 1000]")
        if type(self.control_pattern) is not tuple or not 1 <= len(self.control_pattern) <= 16:
            raise ValueError("control_pattern must be a bounded tuple")
        frozen_pattern = []
        for pair in self.control_pattern:
            if type(pair) is not tuple or len(pair) != 2 or not all(_finite_number(v) for v in pair):
                raise ValueError("each control must be a native finite (x, y) tuple")
            if any(abs(float(v)) > MAX_CONTROL for v in pair):
                raise ValueError("control exceeds the configured force budget")
            frozen_pattern.append((float(pair[0]), float(pair[1])))
        if type(self.obstacles_m) is not tuple or len(self.obstacles_m) > MAX_OBSTACLES:
            raise ValueError("obstacles_m must be a bounded tuple")
        frozen_obstacles = []
        for rect in self.obstacles_m:
            if type(rect) is not tuple or len(rect) != 4 or not all(_finite_number(v) for v in rect):
                raise ValueError("each obstacle must be a native finite (x, y, width, height) tuple")
            x, y, width, height = (float(v) for v in rect)
            if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > self.width_m or y + height > self.height_m:
                raise ValueError("obstacle is degenerate or outside the room")
            if (x <= self.start_x_m + POINT_RADIUS_M
                    and self.start_x_m - POINT_RADIUS_M <= x + width
                    and y <= self.start_y_m + POINT_RADIUS_M
                    and self.start_y_m - POINT_RADIUS_M <= y + height):
                raise ValueError("start position overlaps an obstacle")
            frozen_obstacles.append((x, y, width, height))
        object.__setattr__(self, "control_pattern", tuple(frozen_pattern))
        object.__setattr__(self, "obstacles_m", tuple(frozen_obstacles))


def _validated_config(config: ProbeConfig) -> ProbeConfig:
    """Revalidate frozen instances at execution and serialization boundaries.

    Frozen dataclasses still permit mutation through object.__setattr__.
    Reconstructing exact native fields prevents forged post-construction values
    from bypassing resource, numeric and geometry limits.
    """
    if type(config) is not ProbeConfig:
        raise ValueError("config must be an exact ProbeConfig")
    try:
        return ProbeConfig(
            width_m=config.width_m,
            height_m=config.height_m,
            start_x_m=config.start_x_m,
            start_y_m=config.start_y_m,
            steps=config.steps,
            timestep_ns=config.timestep_ns,
            control_period=config.control_period,
            control_pattern=config.control_pattern,
            obstacles_m=config.obstacles_m,
        )
    except (AttributeError, TypeError, OverflowError) as exc:
        raise ValueError("config no longer satisfies the bounded point-room profile") from exc


def _config_payload(config: ProbeConfig) -> dict:
    config = _validated_config(config)
    return {
        "schema": "mojum.synthetic.point-room-workload/1",
        "width_m": float(config.width_m),
        "height_m": float(config.height_m),
        "start_x_m": float(config.start_x_m),
        "start_y_m": float(config.start_y_m),
        "steps": config.steps,
        "timestep_ns": config.timestep_ns,
        "control_period": config.control_period,
        "control_pattern": [list(pair) for pair in config.control_pattern],
        "obstacles_m": [list(rect) for rect in config.obstacles_m],
        "point_radius_m": POINT_RADIUS_M,
        "max_control": MAX_CONTROL,
    }


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def build_mjcf_xml(config: ProbeConfig = ProbeConfig()) -> str:
    """Create a deterministic in-memory MJCF model with no external assets."""
    config = _validated_config(config)
    root = ET.Element("mujoco", {"model": "mojum_dm_control_point_room"})
    ET.SubElement(root, "compiler", {"angle": "radian"})
    ET.SubElement(root, "option", {
        "timestep": _number(config.timestep_ns / 1_000_000_000),
        "gravity": "0 0 0",
        "integrator": "implicitfast",
    })
    default = ET.SubElement(root, "default")
    ET.SubElement(default, "joint", {"damping": "1.5", "armature": "0.01"})
    ET.SubElement(default, "geom", {"friction": "0.8 0.1 0.1", "condim": "3"})
    world = ET.SubElement(root, "worldbody")
    point = ET.SubElement(world, "body", {
        "name": "point",
        "pos": f"{_number(config.start_x_m)} {_number(config.start_y_m)} {_number(POINT_RADIUS_M)}",
    })
    ET.SubElement(point, "joint", {
        "name": "slide_x", "type": "slide", "axis": "1 0 0",
        "range": f"{_number(POINT_RADIUS_M - config.start_x_m)} {_number(config.width_m - POINT_RADIUS_M - config.start_x_m)}",
        "limited": "true",
    })
    ET.SubElement(point, "joint", {
        "name": "slide_y", "type": "slide", "axis": "0 1 0",
        "range": f"{_number(POINT_RADIUS_M - config.start_y_m)} {_number(config.height_m - POINT_RADIUS_M - config.start_y_m)}",
        "limited": "true",
    })
    ET.SubElement(point, "geom", {
        "name": "point_geom", "type": "sphere", "size": _number(POINT_RADIUS_M),
        "mass": "1", "contype": "1", "conaffinity": "1",
    })
    for index, (x, y, width, height) in enumerate(config.obstacles_m):
        ET.SubElement(world, "geom", {
            "name": f"obstacle_{index}", "type": "box",
            "pos": f"{_number(x + width / 2)} {_number(y + height / 2)} 0.06",
            "size": f"{_number(width / 2)} {_number(height / 2)} 0.06",
            "contype": "1", "conaffinity": "1",
        })
    actuators = ET.SubElement(root, "actuator")
    for axis in ("x", "y"):
        ET.SubElement(actuators, "motor", {
            "name": f"drive_{axis}", "joint": f"slide_{axis}",
            "ctrllimited": "true", "ctrlrange": f"{_number(-MAX_CONTROL)} {_number(MAX_CONTROL)}",
        })
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _nearest_rank(values: list[int], percentile: int) -> int:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered) / 100) - 1)
    return ordered[index]


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def run_benchmark(config: ProbeConfig = ProbeConfig()) -> dict:
    """Run the optional native MuJoCo/dm_control workload and return a receipt."""
    config = _validated_config(config)
    try:
        from dm_control import mujoco
    except ImportError as exc:
        raise RuntimeError(
            "dm_control is optional; install the pinned benchmark extra from "
            "docs/DM_CONTROL_BENCHMARK_R01.md"
        ) from exc

    mjcf = build_mjcf_xml(config)
    physics = mujoco.Physics.from_xml_string(mjcf)
    if int(physics.model.nq) != 2 or int(physics.model.nu) != 2:
        raise RuntimeError("compiled model does not match the expected 2-DOF/2-actuator contract")
    physics.reset()
    physics.data.qpos[:] = (0.0, 0.0)
    physics.data.qvel[:] = (0.0, 0.0)
    physics.forward()

    tracing_started_here = not tracemalloc.is_tracing()
    if tracing_started_here:
        tracemalloc.start()
    step_costs: list[int] = []
    trace_rows: list[list[float | int]] = []
    steps_with_contact = 0
    maximum_contacts = 0
    total_start = time.perf_counter_ns()
    try:
        for step_index in range(config.steps):
            control = config.control_pattern[(step_index // config.control_period) % len(config.control_pattern)]
            physics.data.ctrl[:] = control
            step_start = time.perf_counter_ns()
            physics.step()
            step_costs.append(time.perf_counter_ns() - step_start)
            position = (float(config.start_x_m + physics.data.qpos[0]),
                        float(config.start_y_m + physics.data.qpos[1]))
            if not all(math.isfinite(value) for value in position):
                raise RuntimeError("native engine returned a non-finite position")
            contacts = int(physics.data.ncon)
            if contacts:
                steps_with_contact += 1
            maximum_contacts = max(maximum_contacts, contacts)
            trace_rows.append([step_index + 1, position[0], position[1], contacts])
        elapsed_ns = time.perf_counter_ns() - total_start
        peak_bytes = tracemalloc.get_traced_memory()[1] if tracing_started_here else None
    finally:
        if tracing_started_here:
            tracemalloc.stop()

    workload_bytes = _canonical_bytes(_config_payload(config))
    body = {
        "schema": SCHEMA,
        "backend": "dm_control.mujoco",
        "dm_control_version": _version("dm-control"),
        "mujoco_version": _version("mujoco"),
        "scenario": "mojum.synthetic.point-room/1",
        "step_count": config.steps,
        "timestep_ns": config.timestep_ns,
        "elapsed_ns": int(elapsed_ns),
        "p50_step_ns": _nearest_rank(step_costs, 50),
        "p95_step_ns": _nearest_rank(step_costs, 95),
        "steps_with_contact": steps_with_contact,
        "max_contacts": maximum_contacts,
        "peak_traced_bytes": peak_bytes,
        "final_position_m": [trace_rows[-1][1], trace_rows[-1][2]],
        "trajectory_sha256": hashlib.sha256(_canonical_bytes({"rows": trace_rows})).hexdigest(),
        "config_sha256": hashlib.sha256(workload_bytes).hexdigest(),
        "mjcf_sha256": hashlib.sha256(mjcf.encode("utf-8")).hexdigest(),
    }
    report = dict(body)
    report["report_sha256"] = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    validate_report(report)
    return report


_REPORT_KEYS = {
    "schema", "backend", "dm_control_version", "mujoco_version", "scenario",
    "step_count", "timestep_ns", "elapsed_ns", "p50_step_ns", "p95_step_ns",
    "steps_with_contact", "max_contacts", "peak_traced_bytes", "final_position_m",
    "trajectory_sha256", "config_sha256", "mjcf_sha256", "report_sha256",
}


def validate_report(report: object) -> dict:
    """Validate and detach a benchmark receipt; hashes are consistency checks only."""
    if type(report) is not dict or len(report) != len(_REPORT_KEYS):
        raise ValueError("report must be a native dict with the exact bounded field count")
    keys = tuple(dict.keys(report))
    if any(type(key) is not str for key in keys):
        raise ValueError("report must be a native dict with native string keys")
    if set(keys) != _REPORT_KEYS:
        raise ValueError("report must have the exact versioned field set")
    for key, expected in (("schema", SCHEMA), ("backend", "dm_control.mujoco"),
                          ("scenario", "mojum.synthetic.point-room/1")):
        if type(report[key]) is not str or report[key] != expected:
            raise ValueError(f"unsupported report {key}")
    for key in ("dm_control_version", "mujoco_version"):
        if type(report[key]) is not str or not 1 <= len(report[key]) <= 64:
            raise ValueError(f"invalid {key}")
    int_ranges = {
        "step_count": (1, MAX_STEPS),
        "timestep_ns": (100_000, 20_000_000),
        "elapsed_ns": (0, 2**63 - 1),
        "p50_step_ns": (0, 2**63 - 1),
        "p95_step_ns": (0, 2**63 - 1),
        "steps_with_contact": (0, MAX_STEPS),
        "max_contacts": (0, 1024),
    }
    detached = dict(report)
    for key, (minimum, maximum) in int_ranges.items():
        value = report[key]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"invalid {key}")
    peak = report["peak_traced_bytes"]
    if peak is not None and (type(peak) is not int or not 0 <= peak <= 2**63 - 1):
        raise ValueError("invalid peak_traced_bytes")
    position = report["final_position_m"]
    if type(position) is not list or len(position) != 2 or not all(_finite_number(v) for v in position):
        raise ValueError("invalid final_position_m")
    for key in ("trajectory_sha256", "config_sha256", "mjcf_sha256", "report_sha256"):
        if not _sha256_hex(report[key]):
            raise ValueError(f"invalid {key}")
    if report["steps_with_contact"] > report["step_count"]:
        raise ValueError("steps_with_contact exceeds step_count")
    if report["p95_step_ns"] < report["p50_step_ns"]:
        raise ValueError("p95_step_ns cannot be below p50_step_ns")
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    if hashlib.sha256(_canonical_bytes(body)).hexdigest() != report["report_sha256"]:
        raise ValueError("report digest mismatch")
    detached["final_position_m"] = [position[0], position[1]]
    return detached


def decode_report(payload: str) -> dict:
    """Decode bounded strict JSON, rejecting duplicate keys and non-finite values."""
    if type(payload) is not str or len(payload) > MAX_REPORT_BYTES:
        raise ValueError("report payload must be bounded native text")
    if len(payload) > MAX_REPORT_BYTES // 4:
        raise ValueError("report payload exceeds the maximum possible UTF-8 character budget")

    def pairs_no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if type(key) is not str:
                raise ValueError("JSON object key must be a native string")
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("non-finite JSON constant")

    try:
        parsed = json.loads(payload, object_pairs_hook=pairs_no_duplicates,
                            parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid benchmark report JSON") from exc
    return validate_report(parsed)


__all__ = ["ProbeConfig", "build_mjcf_xml", "run_benchmark", "validate_report", "decode_report"]
