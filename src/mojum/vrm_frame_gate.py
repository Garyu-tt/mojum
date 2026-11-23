"""Monotonic admission gate for read-only VRM pose visualization.

This module accepts caller-supplied PUBLIC_SYNTHETIC pose packets. It does not
render, interpolate, control actuators, or authenticate the packet producer.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any


SCHEMA = "mojum.vrm-pose-frame/1"
PUBLIC_SYNTHETIC = "PUBLIC_SYNTHETIC"
_MAX_I63 = (1 << 63) - 1
_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")
_EPOCH_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_JOINT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_PACKET_KEYS = frozenset(
    {"schema", "epoch", "sequence", "tick", "timestamp_ns", "body_revision", "joints"}
)


class FrameGateError(ValueError):
    """A pose frame was rejected before it could reach a renderer."""


@dataclass(frozen=True, slots=True)
class PoseFrameReceipt:
    """Detached immutable summary of one accepted synthetic pose frame."""

    schema: str
    epoch: str
    sequence: int
    tick: int
    timestamp_ns: int
    body_revision: str
    joints: tuple[tuple[str, tuple[float, float, float]], ...]
    digest: str


class PoseFrameGate:
    """Fail-closed, bounded temporal admission for a visualization-only stream.

    A body-revision change requires an explicit new epoch via begin_epoch().
    Sequence, logical tick, and source timestamp must each increase strictly.
    """

    def __init__(
        self,
        *,
        max_joints: int = 128,
        max_abs_coordinate: float = 10000.0,
    ) -> None:
        if type(max_joints) is not int or not 1 <= max_joints <= 512:
            raise FrameGateError("invalid max_joints")
        if type(max_abs_coordinate) not in (int, float):
            raise FrameGateError("invalid coordinate limit")
        limit = float(max_abs_coordinate)
        if not math.isfinite(limit) or not 0.0 < limit <= 1_000_000.0:
            raise FrameGateError("invalid coordinate limit")
        self._max_joints = max_joints
        self._max_abs_coordinate = limit
        self._epoch: str | None = None
        self._body_revision: str | None = None
        self._last_sequence: int | None = None
        self._last_tick: int | None = None
        self._last_timestamp_ns: int | None = None

    @staticmethod
    def _identity(value: Any, pattern: re.Pattern[str], label: str) -> str:
        if type(value) is not str or not pattern.fullmatch(value):
            raise FrameGateError(f"invalid {label}")
        return value

    @staticmethod
    def _counter(value: Any, label: str) -> int:
        if type(value) is not int or not 0 <= value <= _MAX_I63:
            raise FrameGateError(f"invalid {label}")
        return value

    def begin_epoch(self, *, epoch: str, body_revision: str) -> None:
        """Start a new stream epoch; never silently continue across body revisions."""
        new_epoch = self._identity(epoch, _EPOCH_RE, "epoch")
        revision = self._identity(body_revision, _REVISION_RE, "body_revision")
        if self._epoch is not None and new_epoch == self._epoch:
            raise FrameGateError("epoch must change on reset")
        self._epoch = new_epoch
        self._body_revision = revision
        self._last_sequence = None
        self._last_tick = None
        self._last_timestamp_ns = None

    def admit(
        self,
        packet: Any,
        *,
        consent: bool,
        source_tag: str,
    ) -> PoseFrameReceipt:
        """Validate and admit one frame, returning detached data for rendering."""
        if type(consent) is not bool or consent is not True:
            raise FrameGateError("explicit consent required")
        if type(source_tag) is not str or source_tag != PUBLIC_SYNTHETIC:
            raise FrameGateError("source must be explicitly PUBLIC_SYNTHETIC")
        if self._epoch is None or self._body_revision is None:
            raise FrameGateError("begin_epoch must be called first")
        if type(packet) is not dict:
            raise FrameGateError("invalid packet shape")

        # A dict may contain str subclasses as keys. Their __eq__ can execute
        # arbitrary code during set/frozenset comparison, so admit only exact
        # built-in strings before any hash/equality-based schema comparison.
        packet_keys = tuple(dict.keys(packet))
        if any(type(key) is not str for key in packet_keys):
            raise FrameGateError("packet keys must be native strings")
        if frozenset(packet_keys) != _PACKET_KEYS:
            raise FrameGateError("invalid packet shape")

        if type(packet["schema"]) is not str or packet["schema"] != SCHEMA:
            raise FrameGateError("unsupported schema")

        epoch = self._identity(packet["epoch"], _EPOCH_RE, "epoch")
        revision = self._identity(packet["body_revision"], _REVISION_RE, "body_revision")
        if epoch != self._epoch:
            raise FrameGateError("unexpected epoch")
        if revision != self._body_revision:
            raise FrameGateError("body revision changed; begin a new epoch")

        sequence = self._counter(packet["sequence"], "sequence")
        tick = self._counter(packet["tick"], "tick")
        timestamp_ns = self._counter(packet["timestamp_ns"], "timestamp_ns")
        for current, previous, label in (
            (sequence, self._last_sequence, "sequence"),
            (tick, self._last_tick, "tick"),
            (timestamp_ns, self._last_timestamp_ns, "timestamp"),
        ):
            if previous is not None and current <= previous:
                raise FrameGateError(f"non-monotonic {label}")

        raw_joints = packet["joints"]
        if type(raw_joints) is not dict or not 1 <= len(raw_joints) <= self._max_joints:
            raise FrameGateError("invalid joint map size")
        normalized: list[tuple[str, tuple[float, float, float]]] = []
        for name, coordinates in raw_joints.items():
            if type(name) is not str or not _JOINT_RE.fullmatch(name):
                raise FrameGateError("invalid joint name")
            if type(coordinates) not in (list, tuple) or len(coordinates) != 3:
                raise FrameGateError("each joint must have three coordinates")
            xyz: list[float] = []
            for value in coordinates:
                if type(value) not in (int, float):
                    raise FrameGateError("coordinate must be a plain number")
                try:
                    number = float(value)
                except (OverflowError, ValueError):
                    raise FrameGateError("coordinate out of bounds") from None
                if not math.isfinite(number) or abs(number) > self._max_abs_coordinate:
                    raise FrameGateError("coordinate out of bounds")
                xyz.append(0.0 if number == 0.0 else number)
            normalized.append((name, (xyz[0], xyz[1], xyz[2])))
        normalized.sort(key=lambda item: item[0])
        detached = tuple(normalized)

        canonical = {
            "schema": SCHEMA,
            "epoch": epoch,
            "sequence": sequence,
            "tick": tick,
            "timestamp_ns": timestamp_ns,
            "body_revision": revision,
            "joints": {name: list(xyz) for name, xyz in detached},
        }
        wire = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        digest = hashlib.sha256(wire).hexdigest()

        # State advances only after every validation and digest operation succeeds.
        self._last_sequence = sequence
        self._last_tick = tick
        self._last_timestamp_ns = timestamp_ns
        return PoseFrameReceipt(
            schema=SCHEMA,
            epoch=epoch,
            sequence=sequence,
            tick=tick,
            timestamp_ns=timestamp_ns,
            body_revision=revision,
            joints=detached,
            digest=digest,
        )
