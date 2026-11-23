"""Opt-in, headless MuJoCo articulated-body measurement fixture (#689).

No import of mujoco at module import time, no control actuators, journal writes,
network, external assets, real hardware, or generated motion scripts. Only a
synthetic kinematic chain is supported until rig review and asset approval.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import statistics
import time
import xml.etree.ElementTree as ET

RIG_SCHEMA = "mojum.body.rig/1"
OBS_SCHEMA = "mojum.physics-observation/1"
BENCH_SCHEMA = "mojum.physics-cpu-benchmark/1"
PINNED_MUJOCO = "3.17.0"
DT = 0.002
NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class RigError(ValueError):
    """Untrusted input, incompatible engine, or invalid physical observation."""


def _number(value, label, lo, hi):
    if type(value) not in (int, float):
        raise RigError(f"{label}: numeric scalar required")
    try:
        valid = math.isfinite(value) and lo <= value <= hi
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise RigError(f"{label}: finite number outside bounds")
    return float(value)


def _keys(obj, keys, label):
    if type(obj) is not dict or set(obj) != set(keys):
        raise RigError(f"{label}: unknown or missing fields")


def _name(value):
    if type(value) is not str or NAME.fullmatch(value) is None:
        raise RigError("joint name must be safe ASCII identifier <= 40 chars")
    return value


def validate_rig(rig):
    """Return a detached, strictly typed, bounded rig; no silent migration."""
    _keys(rig, {"schema", "rig_id", "dt_seconds", "root_height_m", "joints"}, "rig")
    if type(rig["schema"]) is not str or rig["schema"] != RIG_SCHEMA:
        raise RigError("unsupported rig schema")
    _name(rig["rig_id"])
    if type(rig["dt_seconds"]) is not float or rig["dt_seconds"] != DT:
        raise RigError("pinned dt_seconds must be 0.002 float")
    _number(rig["root_height_m"], "root_height_m", 0.2, 2.5)
    joints = rig["joints"]
    if type(joints) is not list or not 1 <= len(joints) <= 32:
        raise RigError("joint count must be 1..32")
    names = set()
    length_sum = 0.0
    for joint in joints:
        _keys(joint, {"name", "type", "axis", "limits_rad", "length_m", "radius_m", "mass_kg"}, "joint")
        name = _name(joint["name"])
        if name in names:
            raise RigError("duplicate joint name")
        names.add(name)
        if type(joint["type"]) is not str or joint["type"] != "hinge":
            raise RigError("only one-DOF hinge joints supported")
        axis = joint["axis"]
        if type(axis) is not list or len(axis) != 3:
            raise RigError("axis must have three entries")
        a = [_number(v, "axis", -1, 1) for v in axis]
        if abs(sum(v*v for v in a) - 1.0) > 1e-6:
            raise RigError("axis must be a unit vector")
        limits = joint["limits_rad"]
        if type(limits) is not list or len(limits) != 2:
            raise RigError("limits_rad must be [min,max]")
        low, high = (_number(v, "limits_rad", -math.pi, math.pi) for v in limits)
        if low >= high or high - low < 0.01 or not low <= 0 <= high:
            raise RigError("limits must contain zero and have positive span")
        length_sum += _number(joint["length_m"], "length_m", 0.05, 1.5)
        radius = _number(joint["radius_m"], "radius_m", 0.005, 0.15)
        if radius >= joint["length_m"] / 2:
            raise RigError("capsule radius must be less than half length")
        _number(joint["mass_kg"], "mass_kg", 0.01, 20)
    if length_sum > 6:
        raise RigError("chain exceeds length budget")
    # Exact schema and strict finite primitives above guarantee lossless JSON.
    return copy.deepcopy(rig)


def fixture_rig():
    return {
        "schema": RIG_SCHEMA, "rig_id": "two_link_cpu_fixture", "dt_seconds": DT,
        "root_height_m": 0.9,
        "joints": [
            {"name": "shoulder_y", "type": "hinge", "axis": [0, 1, 0],
             "limits_rad": [-1.2, 1.2], "length_m": 0.5,
             "radius_m": 0.04, "mass_kg": 0.5},
            {"name": "elbow_y", "type": "hinge", "axis": [0, 1, 0],
             "limits_rad": [-1.4, 1.4], "length_m": 0.5,
             "radius_m": 0.035, "mass_kg": 0.4},
        ],
    }


def rig_revision(rig):
    validated = validate_rig(rig)
    raw = json.dumps(validated, sort_keys=True, separators=(",", ":"),
                     allow_nan=False, ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _f(value):
    return format(float(value), ".17g")


def compile_mjcf(rig):
    """Compile a synthetic articulated chain, no assets or actuator elements."""
    checked = validate_rig(rig)
    root = ET.Element("mujoco", {"model": checked["rig_id"]})
    # MJCF defaults to degrees; the rig contract and joint limits are radians.
    ET.SubElement(root, "compiler", {"angle": "radian"})
    ET.SubElement(root, "option", {"timestep": _f(DT), "gravity": "0 0 -9.81",
                                  "integrator": "implicitfast"})
    ET.SubElement(root, "size", {"memory": "16M"})
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "geom", {"name": "floor", "type": "plane",
                                  "size": "2 2 0.1", "pos": "0 0 0"})
    parent = world
    for idx, j in enumerate(checked["joints"]):
        z = checked["root_height_m"] if idx == 0 else checked["joints"][idx-1]["length_m"]
        body = ET.SubElement(parent, "body", {"name": f"link_{idx}",
                                                  "pos": f"0 0 -{_f(z)}" if idx else f"0 0 {_f(z)}"})
        ET.SubElement(body, "joint", {"name": j["name"], "type": "hinge",
                                       "axis": " ".join(_f(v) for v in j["axis"]),
                                       "limited": "true", "range": " ".join(_f(v) for v in j["limits_rad"]),
                                       "damping": "0.2"})
        ET.SubElement(body, "geom", {"name": f"segment_{idx}", "type": "capsule",
                                      "fromto": f"0 0 0 0 0 -{_f(j['length_m'])}",
                                      "size": _f(j["radius_m"]), "mass": _f(j["mass_kg"])})
        parent = body
    return ET.tostring(root, encoding="unicode", short_empty_elements=True) + "\n"


def _finite_measured(value, label):
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RigError(f"nonfinite {label}") from exc
    if not math.isfinite(numeric):
        raise RigError(f"nonfinite {label}")
    return numeric


class HeadlessBody:
    """Explicit opt-in measured virtual physics. No controls or actuators."""
    def __init__(self, rig, *, mujoco_module=None):
        self.rig = validate_rig(rig)
        self.revision = rig_revision(self.rig)
        if mujoco_module is None:
            try:
                import mujoco as mujoco_module  # optional dependency
            except ImportError as exc:
                raise RuntimeError("MuJoCo optional runtime unavailable") from exc
        self.mj = mujoco_module
        if getattr(self.mj, "__version__", None) != PINNED_MUJOCO:
            raise RuntimeError(f"requires exact MuJoCo {PINNED_MUJOCO}")
        self.model = self.mj.MjModel.from_xml_string(compile_mjcf(self.rig))
        if (self.model.nu != 0 or self.model.nq != len(self.rig["joints"])
                or self.model.nv != len(self.rig["joints"])
                or self.model.nmocap != 0
                or abs(self.model.opt.timestep - DT) > 1e-12):
            raise RigError("unexpected engine topology or timestep")
        self.data = self.mj.MjData(self.model)
        self.tick = 0
        self.faulted = False
        self._observe()  # validate the initial engine result before any step

    def _observe(self):
        joints = []
        for i, joint in enumerate(self.rig["joints"]):
            pos = _finite_measured(self.data.qpos[i], "qpos")
            vel = _finite_measured(self.data.qvel[i], "qvel")
            lo, hi = joint["limits_rad"]
            joints.append({"name": joint["name"], "position_rad": pos,
                           "velocity_rad_s": vel, "limits_rad": [lo, hi],
                           "limit_violation_rad": max(0.0, lo - pos, pos - hi)})
        count = int(self.data.ncon)
        if count < 0 or count > 128:
            raise RigError("contact budget exceeded")
        contacts = []
        for i in range(count):
            contact = self.data.contact[i]
            a = self.mj.mj_id2name(self.model, self.mj.mjtObj.mjOBJ_GEOM, int(contact.geom1))
            b = self.mj.mj_id2name(self.model, self.mj.mjtObj.mjOBJ_GEOM, int(contact.geom2))
            if a is None or b is None:
                raise RigError("unnamed collision geometry")
            contacts.append({"geom_a": a, "geom_b": b,
                             "signed_distance_m": _finite_measured(contact.dist, "contact.distance")})
        contacts.sort(key=lambda c: (c["geom_a"], c["geom_b"], c["signed_distance_m"]))
        # The fixed 2ms solver advances exactly once per exposed virtual tick.
        # A finite but stale/advanced/negative engine clock is still invalid.
        sim_time = _finite_measured(self.data.time, "time")
        expected_time = self.tick * DT
        if sim_time < 0 or not math.isclose(
                sim_time, expected_time, rel_tol=1e-9, abs_tol=1e-9):
            raise RigError("inconsistent virtual simulation clock")
        return {"schema": OBS_SCHEMA, "source": f"mujoco/{PINNED_MUJOCO}",
                "rig_revision": self.revision, "tick": self.tick,
                "sim_time_s": sim_time,
                "dt_seconds": DT, "joints": joints, "contacts": contacts,
                "actuation": "disabled", "hardware_contact": False,
                "contact_provenance": "measured_virtual_solver_contacts_not_hardware"}

    def observe(self):
        if self.faulted:
            raise RigError("physics fault latched")
        try:
            return self._observe()
        except (ValueError, OverflowError, IndexError) as exc:
            self.faulted = True
            raise RigError("invalid engine observation; fault latched") from exc

    def step(self, steps=1):
        if type(steps) is not int or not 1 <= steps <= 10_000:
            raise RigError("step count 1..10000 required")
        if self.faulted:
            raise RigError("physics fault latched")
        try:
            for _ in range(steps):
                self.mj.mj_step(self.model, self.data)
                self.tick += 1
                self._observe()
        except (ValueError, OverflowError, IndexError, FloatingPointError, RuntimeError) as exc:
            self.faulted = True
            raise RigError("invalid engine transition; fault latched") from exc
        return self.observe()


def benchmark(*, steps=100, warmup=10):
    if type(steps) is not int or not 1 <= steps <= 10_000:
        raise RigError("steps must be 1..10000")
    if type(warmup) is not int or not 0 <= warmup <= 2000:
        raise RigError("warmup must be 0..2000")
    body = HeadlessBody(fixture_rig())
    samples = []
    max_contacts = 0
    max_violation = 0.0
    for tick in range(steps + warmup):
        started = time.perf_counter_ns()
        obs = body.step()
        elapsed = time.perf_counter_ns() - started
        if tick >= warmup:
            samples.append(elapsed)
            max_contacts = max(max_contacts, len(obs["contacts"]))
            max_violation = max(max_violation,
                                *(j["limit_violation_rad"] for j in obs["joints"]))
    sorted_ms = sorted(v / 1_000_000 for v in samples)
    try:
        import resource
        import sys
        rss_native = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_kib = int(rss_native / 1024) if sys.platform == "darwin" else int(rss_native)
    except (ImportError, OSError, ValueError):
        rss_kib = None
    return {"schema": BENCH_SCHEMA, "engine": "mujoco", "version": PINNED_MUJOCO,
            "rig_revision": body.revision, "steps": steps, "warmup": warmup,
            "p50_wall_ms_per_step": round(statistics.median(sorted_ms), 6),
            "p95_wall_ms_per_step": round(sorted_ms[math.ceil(0.95*steps)-1], 6),
            "max_solver_contacts": max_contacts,
            "max_rss_kib_process": rss_kib,
            "max_joint_limit_violation_rad": max_violation,
            "note": "virtual solver contacts, wall time not CPU utilization; no external actuation"}


def main(argv=None):
    """Offline XML dry-run, or explicit opt-in pinned MuJoCo simulation."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--print-mjcf", action="store_true")
    mode.add_argument("--benchmark", action="store_true")
    parser.add_argument("--steps", type=int, default=64)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        if args.print_mjcf:
            print(compile_mjcf(fixture_rig()), end="")
            return 0
        if args.benchmark:
            result = benchmark(steps=args.steps, warmup=args.warmup)
        else:
            result = HeadlessBody(fixture_rig()).step(args.steps)
    except (RuntimeError, ValueError) as exc:
        print(f"opt-in MuJoCo body unavailable: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
