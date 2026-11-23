"""Opt-in, deterministic cloth work admission. No solver, mesh mutation or I/O.

The planner does not establish physical accuracy, CPU performance or real-time
safety. The caller supplies real completed-step CPU measurements explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

SCHEMA = "mojum.cloth.lod-state/1"
MODES = ("off", "low", "medium", "high")
CADENCE = {"off": 0, "low": 4, "medium": 2, "high": 1}
SUBSTEPS = {"off": 0, "low": 2, "medium": 2, "high": 2}
MAX_COUNT = 1_000_000
MAX_TICK = 2**63 - 2
RECOVERY_SAMPLES = 4


class ClothLODError(ValueError):
    """Invalid planner input or persisted state."""


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ClothLODError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _mode(value: str) -> str:
    if type(value) is not str or value not in MODES:
        raise ClothLODError("unknown cloth LOD mode")
    return value


@dataclass(frozen=True)
class MeshCost:
    vertices: int
    constraints: int
    estimated_bytes: int

    def __post_init__(self) -> None:
        _integer(self.vertices, "vertices", 0, MAX_COUNT)
        _integer(self.constraints, "constraints", 0, MAX_COUNT * 4)
        _integer(self.estimated_bytes, "estimated_bytes", 0, 1 << 30)


@dataclass(frozen=True)
class ClothLODConfig:
    requested: str = "off"
    cpu_budget_us: int = 8000
    max_vertices: int = 4096
    max_constraints: int = 16384
    max_bytes: int = 32 * 1024 * 1024
    low: MeshCost = MeshCost(256, 512, 262144)
    medium: MeshCost = MeshCost(1024, 2048, 1048576)
    high: MeshCost = MeshCost(4096, 8192, 4194304)

    def __post_init__(self) -> None:
        _mode(self.requested)
        _integer(self.cpu_budget_us, "cpu_budget_us", 1, 60_000_000)
        _integer(self.max_vertices, "max_vertices", 0, MAX_COUNT)
        _integer(self.max_constraints, "max_constraints", 0, MAX_COUNT * 4)
        _integer(self.max_bytes, "max_bytes", 0, 1 << 30)
        if not all(type(v) is MeshCost for v in (self.low, self.medium, self.high)):
            raise ClothLODError("LOD mesh costs must be MeshCost")
        # A coarser LOD must never advertise larger resource requirements.
        for a, b in ((self.low, self.medium), (self.medium, self.high)):
            if any(getattr(a, key) > getattr(b, key)
                   for key in ("vertices", "constraints", "estimated_bytes")):
                raise ClothLODError("LOD mesh costs must grow monotonically")

    def fingerprint(self) -> str:
        d = {"schema": "mojum.cloth.lod-config/1", "requested": self.requested,
             "cpu_budget_us": self.cpu_budget_us, "max_vertices": self.max_vertices,
             "max_constraints": self.max_constraints, "max_bytes": self.max_bytes,
             "costs": [[c.vertices, c.constraints, c.estimated_bytes]
                       for c in (self.low, self.medium, self.high)]}
        return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def admitted(self) -> str:
        for candidate in reversed(MODES[1:MODES.index(self.requested) + 1]):
            cost = getattr(self, candidate)
            if (cost.vertices <= self.max_vertices
                    and cost.constraints <= self.max_constraints
                    and cost.estimated_bytes <= self.max_bytes):
                return candidate
        return "off"


@dataclass(frozen=True)
class ClothLODState:
    policy_sha256: str
    next_tick: int
    effective: str
    safe_samples: int = 0
    pending_measurement: bool = False
    fault_latched: bool = False

    def to_snapshot(self) -> dict:
        return {"schema": SCHEMA, "policy_sha256": self.policy_sha256,
                "next_tick": self.next_tick, "effective": self.effective,
                "safe_samples": self.safe_samples,
                "pending_measurement": self.pending_measurement,
                "fault_latched": self.fault_latched}


@dataclass(frozen=True)
class ClothLODDecision:
    tick: int
    effective: str
    simulate: bool
    cadence_ticks: int
    substeps: int
    reason: str


def restore_state(snapshot: dict, config: ClothLODConfig) -> ClothLODState:
    """Exact-version, exact-policy restoration; no implicit config migration."""
    if type(config) is not ClothLODConfig or type(snapshot) is not dict:
        raise ClothLODError("invalid LOD snapshot or configuration")
    expected = {"schema", "policy_sha256", "next_tick", "effective",
                "safe_samples", "pending_measurement", "fault_latched"}
    # Python callers can pass hostile objects, not just decoded JSON. Validate
    # exact built-in string types before equality can invoke user-defined code.
    if (any(type(key) is not str for key in snapshot)
            or set(snapshot) != expected
            or type(snapshot["schema"]) is not str
            or snapshot["schema"] != SCHEMA):
        raise ClothLODError("unknown LOD snapshot schema or fields")
    if (type(snapshot["policy_sha256"]) is not str
            or snapshot["policy_sha256"] != config.fingerprint()):
        raise ClothLODError("LOD policy mismatch; explicit restart required")
    _integer(snapshot["next_tick"], "next_tick", 0, MAX_TICK)
    _integer(snapshot["safe_samples"], "safe_samples", 0, RECOVERY_SAMPLES - 1)
    _mode(snapshot["effective"])
    if (type(snapshot["pending_measurement"]) is not bool
            or type(snapshot["fault_latched"]) is not bool):
        raise ClothLODError("invalid LOD state booleans")
    if MODES.index(snapshot["effective"]) > MODES.index(config.admitted()):
        raise ClothLODError("active LOD exceeds configured resource limits")
    if ((snapshot["effective"] == "off" and snapshot["pending_measurement"])
            or (snapshot["fault_latched"] and (
                snapshot["effective"] != "off" or snapshot["pending_measurement"]))
            or (snapshot["next_tick"] == 0 and (
                snapshot["pending_measurement"] or snapshot["safe_samples"] != 0))):
        raise ClothLODError("inconsistent LOD state")
    return ClothLODState(snapshot["policy_sha256"], snapshot["next_tick"],
                         snapshot["effective"], snapshot["safe_samples"],
                         snapshot["pending_measurement"], snapshot["fault_latched"])


def start(config: ClothLODConfig) -> ClothLODState:
    if type(config) is not ClothLODConfig:
        raise ClothLODError("invalid LOD configuration")
    return ClothLODState(config.fingerprint(), 0, config.admitted())


def advance(config: ClothLODConfig, state: ClothLODState, tick: int,
            completed_cpu_us: int | None = None) -> tuple[ClothLODDecision, ClothLODState]:
    """Plan one exact sequential tick. Return immutable decision + next state.

    completed_cpu_us applies solely to the immediately preceding *scheduled*
    cloth step, never the current tick. Missing evidence latches OFF. An overrun
    downgrades one mode (LOW -> OFF) and only measured safe *steps*, not skipped
    ticks, may promote one mode after RECOVERY_SAMPLES.
    """
    if type(config) is not ClothLODConfig or type(state) is not ClothLODState:
        raise ClothLODError("invalid LOD planner objects")
    restore_state(state.to_snapshot(), config)
    _integer(tick, "tick", 0, MAX_TICK - 1)
    if tick != state.next_tick:
        raise ClothLODError("nonsequential cloth tick")
    if completed_cpu_us is not None:
        _integer(completed_cpu_us, "completed_cpu_us", 0, 60_000_000)
    if completed_cpu_us is not None and not state.pending_measurement:
        raise ClothLODError("unsolicited CPU measurement")

    effective = state.effective
    safe = state.safe_samples
    fault = state.fault_latched
    reason = "normal"
    if state.pending_measurement and completed_cpu_us is None:
        effective, safe, fault, reason = "off", 0, True, "measurement_missing"
    elif state.pending_measurement and completed_cpu_us is not None:
        if completed_cpu_us > config.cpu_budget_us:
            effective = MODES[MODES.index(effective) - 1]
            safe = 0
            reason = "cpu_over_budget"
            if effective == "off":
                fault = True
        else:
            if effective != config.admitted():
                safe += 1
                if safe >= RECOVERY_SAMPLES:
                    effective = MODES[MODES.index(effective) + 1]
                    safe = 0
                    reason = "cpu_recovery"
            else:
                safe = 0
    if fault:
        effective = "off"
        safe = 0
    cadence = CADENCE[effective]
    simulate = cadence > 0 and tick % cadence == 0
    decision = ClothLODDecision(tick, effective, simulate, cadence,
                                SUBSTEPS[effective] if simulate else 0,
                                reason if reason != "normal" else (
                                    "scheduled" if simulate else "throttled_or_off"))
    next_state = ClothLODState(config.fingerprint(), tick + 1, effective,
                               safe, simulate, fault)
    return decision, next_state
