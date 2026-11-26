"""Fail-closed bridge from synthetic CPG frames to the existing MotorGate.

This adapter only creates validated virtual motor intents. It does not step a
physics engine, command hardware, or claim that a waveform is locomotion.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Sequence

from .motor_gate import MotorGate, MotorIntent
from .spinal_cpg import CPGFrame, SensoryFeedback, FRAME_SCHEMA, OUTPUT_MILLI


BRIDGE_SCHEMA = "mojum.spinal-cpg.motor-bridge/1"
MAX_CHANNELS = 128
MAX_TICK = 1_000_000_000
MAX_REVISION = 2**63 - 1
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


class CPGBridgeError(ValueError):
    """A CPG frame, channel map, or feedback sample is not admissible."""


@dataclass(frozen=True, slots=True)
class JointChannel:
    """Map one dimensionless CPG side output to a virtual joint target.

    All calibration values are integer milliradians. The MotorGate remains the
    final authority for the actual configured joint limits.
    """

    joint: str
    side: str
    center_millirad: int
    scale_millirad: int

    def __post_init__(self) -> None:
        if type(self.joint) is not str or not self.joint or len(self.joint) > 128:
            raise CPGBridgeError("joint must be a nonempty bounded string")
        if type(self.side) is not str or self.side not in ("left", "right"):
            raise CPGBridgeError("side must be left or right")
        if type(self.center_millirad) is not int or not -10_000 <= self.center_millirad <= 10_000:
            raise CPGBridgeError("center_millirad is outside the admitted range")
        if type(self.scale_millirad) is not int or not 0 <= self.scale_millirad <= 10_000:
            raise CPGBridgeError("scale_millirad is outside the admitted range")


def _valid_frame(frame: CPGFrame, gate: MotorGate) -> None:
    if type(frame) is not CPGFrame:
        raise CPGBridgeError("an exact CPGFrame is required")
    if frame.schema != FRAME_SCHEMA or frame.waveform != "FIXED_POINT_TRIANGLE":
        raise CPGBridgeError("unsupported CPG frame schema or waveform")
    if frame.physical_actuation is not False:
        raise CPGBridgeError("physical actuation frames are never admitted")
    if type(frame.tick) is not int or not 1 <= frame.tick <= MAX_TICK:
        raise CPGBridgeError("invalid frame tick")
    if type(frame.profile_revision) is not int or not 0 <= frame.profile_revision <= MAX_REVISION:
        raise CPGBridgeError("invalid profile revision")
    if frame.profile_revision != gate.revision:
        raise CPGBridgeError("CPG frame revision does not match MotorGate")
    if type(frame.profile_sha256) is not str or _SHA256.fullmatch(frame.profile_sha256) is None:
        raise CPGBridgeError("invalid CPG profile digest")
    if type(frame.state_sha256) is not str or _SHA256.fullmatch(frame.state_sha256) is None:
        raise CPGBridgeError("invalid CPG state digest")
    for name in ("left_output_milli", "right_output_milli"):
        value = getattr(frame, name)
        if type(value) is not int or not -OUTPUT_MILLI <= value <= OUTPUT_MILLI:
            raise CPGBridgeError(f"invalid {name}")


def intents_from_frame(
    frame: CPGFrame,
    channels: Sequence[JointChannel],
    gate: MotorGate,
) -> tuple[MotorIntent, ...]:
    """Convert a frame to intents, validating every target before returning any.

    The caller must still pass these intents through the normal motor pipeline.
    No actuator or MuJoCo API is invoked here.
    """
    if type(gate) is not MotorGate:
        raise CPGBridgeError("an exact MotorGate is required")
    _valid_frame(frame, gate)
    if type(channels) not in (tuple, list) or not 1 <= len(channels) <= MAX_CHANNELS:
        raise CPGBridgeError("channels must be a nonempty bounded list or tuple")

    seen: set[str] = set()
    pending: list[MotorIntent] = []
    for channel in channels:
        if type(channel) is not JointChannel:
            raise CPGBridgeError("every channel must be an exact JointChannel")
        if channel.joint in seen:
            raise CPGBridgeError("duplicate joint channel")
        seen.add(channel.joint)
        output = frame.left_output_milli if channel.side == "left" else frame.right_output_milli
        target_millirad = channel.center_millirad + (
            output * channel.scale_millirad // OUTPUT_MILLI
        )
        intent = MotorIntent(
            joint=channel.joint,
            target=target_millirad / OUTPUT_MILLI,
            revision=frame.profile_revision,
        )
        try:
            gate.validate(intent)
        except (TypeError, ValueError, PermissionError) as exc:
            raise CPGBridgeError(f"MotorGate rejected joint {channel.joint!r}") from exc
        pending.append(intent)
    return tuple(pending)


def feedback_from_loads(
    *,
    tick: int,
    profile_revision: int,
    left_load_milli: int,
    right_load_milli: int,
    frequency_delta_millihz: int = 0,
) -> SensoryFeedback:
    """Build an explicitly synthetic CPG feedback record from bounded load proxies."""
    try:
        return SensoryFeedback(
            tick=tick,
            profile_revision=profile_revision,
            left_load_milli=left_load_milli,
            right_load_milli=right_load_milli,
            frequency_delta_millihz=frequency_delta_millihz,
            synthetic=True,
        )
    except (TypeError, ValueError) as exc:
        raise CPGBridgeError("invalid synthetic load feedback") from exc
