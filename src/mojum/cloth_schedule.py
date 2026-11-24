"""Deterministic, solver-neutral cloth update scheduling policy."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Final

SCHEMA: Final = "mojum.cloth.schedule/1"

@dataclass(frozen=True, slots=True)
class ClothSchedulePolicy:
    profile: str
    tick_divisor: int
    max_catch_up_steps: int
    max_vertices: int
    max_constraints: int
    enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.profile) is not str or self.profile not in {"OFF", "LOW", "MEDIUM", "HIGH", "CUSTOM"}:
            raise ValueError("invalid profile")
        for name in ("tick_divisor", "max_catch_up_steps", "max_vertices", "max_constraints"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative native int")
        if self.tick_divisor < 1 or self.max_catch_up_steps > 8:
            raise ValueError("invalid cadence or catch-up ceiling")
        if self.max_vertices > 1_000_000 or self.max_constraints > 4_000_000:
            raise ValueError("work ceiling exceeds hard limit")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a native bool")
        if self.profile == "OFF" and self.enabled:
            raise ValueError("OFF profile cannot be enabled")

PROFILES: Final = {
    "OFF": ClothSchedulePolicy("OFF", 1, 0, 0, 0, False),
    "LOW": ClothSchedulePolicy("LOW", 4, 1, 2_000, 8_000),
    "MEDIUM": ClothSchedulePolicy("MEDIUM", 2, 1, 8_000, 32_000),
    "HIGH": ClothSchedulePolicy("HIGH", 1, 2, 32_000, 128_000),
}

@dataclass(frozen=True, slots=True)
class ClothStepPlan:
    schema: str
    logical_tick: int
    run: bool
    reason: str
    admitted_steps: int
    deferred_pending_steps: int
    admitted_vertices: int
    admitted_constraints: int
    dropped_pending_steps: int
    policy_digest: str

def policy_digest(policy: ClothSchedulePolicy) -> str:
    payload = {
        "schema": SCHEMA, "profile": policy.profile,
        "tick_divisor": policy.tick_divisor,
        "max_catch_up_steps": policy.max_catch_up_steps,
        "max_vertices": policy.max_vertices,
        "max_constraints": policy.max_constraints,
        "enabled": policy.enabled,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("ascii")).hexdigest()

def plan_step(policy: ClothSchedulePolicy, logical_tick: int, *,
              pending_steps: int = 1, requested_vertices: int = 0,
              requested_constraints: int = 0, enabled: bool = True) -> ClothStepPlan:
    """Admit bounded fixed-dt updates; explicitly defer or drop every pending step."""
    if not isinstance(policy, ClothSchedulePolicy):
        raise TypeError("policy must be ClothSchedulePolicy")
    for name, value in (("logical_tick", logical_tick), ("pending_steps", pending_steps),
                        ("requested_vertices", requested_vertices),
                        ("requested_constraints", requested_constraints)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative native int")
    if logical_tick < 1 or pending_steps < 1:
        raise ValueError("logical_tick and pending_steps must be positive")
    if requested_vertices > 1_000_000 or requested_constraints > 4_000_000:
        raise ValueError("requested work exceeds hard limit")
    if type(enabled) is not bool:
        raise ValueError("enabled must be a native bool")
    digest = policy_digest(policy)

    def no_admission(reason: str) -> ClothStepPlan:
        return ClothStepPlan(SCHEMA, logical_tick, False, reason, 0, pending_steps,
                             0, 0, 0, digest)

    if not enabled or not policy.enabled:
        return no_admission("disabled")
    if logical_tick % policy.tick_divisor:
        return no_admission("cadence")
    if requested_vertices > policy.max_vertices or requested_constraints > policy.max_constraints:
        return no_admission("work-budget-exceeded")

    admitted = min(pending_steps, policy.max_catch_up_steps)
    dropped = pending_steps - admitted
    reason = "run-with-backlog-dropped" if dropped else "run"
    return ClothStepPlan(SCHEMA, logical_tick, admitted > 0, reason, admitted, 0,
                         requested_vertices, requested_constraints, dropped, digest)
