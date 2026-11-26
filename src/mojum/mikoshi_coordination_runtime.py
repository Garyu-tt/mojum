"""Deterministic multi-rate bridge for Mikoshi advisory subsystems.

This adapter schedules the existing coordination and autonomic contracts on a
shared logical clock while preserving their independent cadence and state.
It returns advisory values only: it does not write GENESIS chemistry, journals,
motors, physiology, devices, or external services.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Final

from . import autonomic_homeostasis as autonomic
from . import cerebellar_coordination as coordination

SCHEMA: Final = "mojum.mikoshi.coordination-runtime/1"
SNAPSHOT_SCHEMA: Final = "mojum.mikoshi.coordination-runtime-snapshot/1"
MAX_TICK: Final = min(autonomic.MAX_TICK, coordination.MAX_TICK)
MAX_REVISION: Final = min(autonomic.MAX_COUNTER, coordination.MAX_COUNTER)


class RuntimeError(ValueError):
    """Invalid runtime configuration, input, or snapshot."""


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise RuntimeError(f"{name} must be a native integer in [{low}, {high}]")
    return value


def _exact_dict(value: object, fields: set[str], name: str) -> dict[str, object]:
    if type(value) is not dict or len(value) != len(fields):
        raise RuntimeError(f"invalid {name}")
    keys = tuple(value.keys())
    if any(type(key) is not str or len(key) > 64 for key in keys):
        raise RuntimeError(f"invalid {name} keys")
    if set(keys) != fields:
        raise RuntimeError(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """One atomic logical-tick result from both advisory subsystems."""

    schema: str
    tick: int
    revision: int
    coordination: coordination.CoordinationResult
    autonomic: autonomic.AutonomicResult


class MikoshiCoordinationRuntime:
    """Shared-clock scheduler with independent subsystem cadence and snapshots.

    Both transitions are computed from immutable input states before either
    state is committed. A rejected input therefore leaves the runtime wholly
    unchanged. A fault reported by one subsystem is handled by that subsystem
    and does not suppress a valid transition in the other.
    """

    __slots__ = (
        "_coordination_profile",
        "_autonomic_profile",
        "_coordination_state",
        "_autonomic_state",
        "_revision",
        "_next_tick",
    )

    def __init__(
        self,
        coordination_profile: coordination.CoordinationProfile | None = None,
        autonomic_profile: autonomic.AutonomicProfile | None = None,
        *,
        revision: int = 0,
        start_tick: int = 0,
    ) -> None:
        selected_coordination = (
            coordination.default_profile()
            if coordination_profile is None
            else coordination_profile
        )
        selected_autonomic = (
            autonomic.default_profile()
            if autonomic_profile is None
            else autonomic_profile
        )
        # Require exact immutable contract classes before reconstructing
        # through their public validators.
        if type(selected_coordination) is not coordination.CoordinationProfile:
            raise RuntimeError("an exact CoordinationProfile is required")
        if type(selected_autonomic) is not autonomic.AutonomicProfile:
            raise RuntimeError("an exact AutonomicProfile is required")
        self._coordination_profile = coordination.CoordinationProfile(
            **asdict(selected_coordination)
        )
        self._autonomic_profile = autonomic.AutonomicProfile(
            **asdict(selected_autonomic)
        )
        self._revision = _integer(revision, "revision", 0, MAX_REVISION)
        self._next_tick = _integer(start_tick, "start_tick", 0, MAX_TICK)
        self._coordination_state = coordination.initial_state(
            revision=self._revision, start_tick=self._next_tick
        )
        self._autonomic_state = autonomic.initial_state(
            revision=self._revision, start_tick=self._next_tick
        )

    @property
    def next_tick(self) -> int:
        return self._next_tick

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def coordination_state(self) -> coordination.CoordinationState:
        return self._coordination_state

    @property
    def autonomic_state(self) -> autonomic.AutonomicState:
        return self._autonomic_state

    def step(
        self,
        coordination_observation: coordination.CoordinationObservation,
        autonomic_observation: autonomic.AutonomicObservation,
        *,
        tick: int,
    ) -> RuntimeResult:
        now = _integer(tick, "tick", 0, MAX_TICK - 1)
        if now != self._next_tick:
            raise RuntimeError("tick must equal runtime.next_tick")
        # Calculate both immutable transitions first. If either validator
        # rejects its sample, neither component state nor the shared clock moves.
        next_coordination, coordination_result = coordination.step(
            self._coordination_profile,
            self._coordination_state,
            coordination_observation,
            tick=now,
        )
        next_autonomic, autonomic_result = autonomic.step(
            self._autonomic_profile,
            self._autonomic_state,
            autonomic_observation,
            tick=now,
        )
        self._coordination_state = next_coordination
        self._autonomic_state = next_autonomic
        self._next_tick = now + 1
        return RuntimeResult(
            schema=SCHEMA,
            tick=now,
            revision=self._revision,
            coordination=coordination_result,
            autonomic=autonomic_result,
        )

    def snapshot(self) -> dict[str, object]:
        """Return detached, profile-pinned state for deterministic replay."""
        return {
            "schema": SNAPSHOT_SCHEMA,
            "runtime_schema": SCHEMA,
            "revision": self._revision,
            "next_tick": self._next_tick,
            "coordination": coordination.snapshot(
                self._coordination_profile, self._coordination_state
            ),
            "autonomic": autonomic.snapshot(
                self._autonomic_profile, self._autonomic_state
            ),
        }

    @classmethod
    def restore(
        cls,
        coordination_profile: coordination.CoordinationProfile,
        autonomic_profile: autonomic.AutonomicProfile,
        payload: object,
        *,
        minimum_next_tick: int | None = None,
    ) -> "MikoshiCoordinationRuntime":
        envelope = _exact_dict(
            payload,
            {
                "schema",
                "runtime_schema",
                "revision",
                "next_tick",
                "coordination",
                "autonomic",
            },
            "runtime snapshot",
        )
        if envelope["schema"] != SNAPSHOT_SCHEMA or envelope["runtime_schema"] != SCHEMA:
            raise RuntimeError("unsupported runtime snapshot schema")
        revision = _integer(envelope["revision"], "revision", 0, MAX_REVISION)
        next_tick = _integer(envelope["next_tick"], "next_tick", 0, MAX_TICK)
        if minimum_next_tick is not None:
            floor = _integer(minimum_next_tick, "minimum_next_tick", 0, MAX_TICK)
            if next_tick < floor:
                raise RuntimeError("runtime snapshot would roll back live time")

        instance = cls(
            coordination_profile,
            autonomic_profile,
            revision=revision,
            start_tick=next_tick,
        )
        c_state = coordination.restore(
            instance._coordination_profile,
            envelope["coordination"],
            minimum_next_tick=next_tick,
        )
        a_state = autonomic.restore(
            instance._autonomic_profile,
            envelope["autonomic"],
            minimum_next_tick=next_tick,
        )
        if c_state.next_tick != next_tick or a_state.next_tick != next_tick:
            raise RuntimeError("subsystem snapshot clocks disagree with runtime")
        if c_state.revision != revision or a_state.revision != revision:
            raise RuntimeError("subsystem snapshot revisions disagree with runtime")
        instance._coordination_state = c_state
        instance._autonomic_state = a_state
        return instance


def snapshot_json(runtime: MikoshiCoordinationRuntime) -> str:
    """Canonical UTF-8-compatible JSON representation of a runtime snapshot."""
    if type(runtime) is not MikoshiCoordinationRuntime:
        raise RuntimeError("an exact MikoshiCoordinationRuntime is required")
    return json.dumps(
        runtime.snapshot(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
