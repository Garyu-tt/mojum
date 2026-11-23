"""Bounded, all-or-reject validation of *synthetic* multi-joint motor intents.

A batch receipt is advisory data: it does not reserve a joint revision, latch a
real e-stop, execute commands, or provide cross-thread/cross-process atomicity.
"""

from dataclasses import dataclass
import hashlib
import json

from .motor_gate import MotorGate, MotorIntent

SCHEMA = "mojum.motor.batch-validation/1"
MAX_BATCH_JOINTS = 128
MAX_JOINT_ID_BYTES = 256


@dataclass(frozen=True)
class ValidatedJoint:
    joint: str
    target: float


@dataclass(frozen=True)
class MotorBatchReceipt:
    schema: str
    revision: int
    commands: tuple[ValidatedJoint, ...]
    sha256: str


def validate_motor_batch(
    gate: MotorGate, intents: list[MotorIntent] | tuple[MotorIntent, ...],
    *, expected_revision: int,
) -> MotorBatchReceipt:
    """Validate every joint at the *same* expected revision, or raise.

    No partial receipt or actuator side effects are emitted. Sorting by joint
    gives a deterministic canonical hash for semantically equal command sets.
    Gate revisions and e-stop are rechecked after all commands, but external
    writers still require their own lock/fencing before actual actuation.
    """
    if type(gate) is not MotorGate:
        raise TypeError("MotorGate required")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid expected revision")
    if type(intents) not in (list, tuple):
        raise TypeError("bounded list or tuple of MotorIntent required")
    if not 1 <= len(intents) <= min(MAX_BATCH_JOINTS, len(gate.limits)):
        raise ValueError("invalid motor batch length")
    # Copy caller-owned list before inspecting it; never iterate arbitrary
    # user-defined iterables which might be infinite or have side effects.
    commands = tuple(intents)
    if gate.estop_latched:
        raise PermissionError("e-stop latched")
    if gate.revision != expected_revision:
        raise ValueError("stale gate revision")

    seen: set[str] = set()
    checked: list[ValidatedJoint] = []
    for intent in commands:
        if type(intent) is not MotorIntent:
            raise TypeError("MotorIntent required")
        if type(intent.joint) is not str:
            raise ValueError("invalid joint identifier")
        # Every valid Unicode codepoint occupies at least one UTF-8 byte.
        # Check the constant-time character count *before* allocating an
        # encoded copy: hostile million-character names must fail boundedly.
        if not 1 <= len(intent.joint) <= MAX_JOINT_ID_BYTES:
            raise ValueError("invalid joint identifier length")
        try:
            if len(intent.joint.encode("utf-8", errors="strict")) > MAX_JOINT_ID_BYTES:
                raise ValueError("invalid joint identifier length")
        except UnicodeError as exc:
            raise ValueError("invalid UTF-8 joint identifier") from exc
        if intent.joint in seen:
            raise ValueError("duplicate joint in motor batch")
        seen.add(intent.joint)
        # The existing fail-closed gate owns numeric, per-joint range, revision,
        # and e-stop checks. Never implement a weaker duplicate validator.
        target = gate.validate(intent)
        checked.append(ValidatedJoint(intent.joint, 0.0 if target == 0 else target))

    if gate.estop_latched:
        raise PermissionError("e-stop latched")
    if gate.revision != expected_revision:
        raise ValueError("gate revision changed during validation")
    ordered = tuple(sorted(checked, key=lambda command: command.joint))
    envelope = {
        "schema": SCHEMA,
        "revision": expected_revision,
        "commands": [{"joint": c.joint, "target": c.target} for c in ordered],
    }
    wire = json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return MotorBatchReceipt(SCHEMA, expected_revision, ordered,
                             hashlib.sha256(wire).hexdigest())
