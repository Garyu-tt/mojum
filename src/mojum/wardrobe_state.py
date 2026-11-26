"""Versioned, deterministic metadata-only wardrobe state transitions.

This module does not simulate cloth, fit, collisions, or physical contact.
"""
from dataclasses import dataclass
import hashlib
import json
import re

SCHEMA = "mojum.wardrobe-state/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_ITEMS = 32
MAX_ID = 96
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SLOTS = frozenset({"head", "torso", "legs", "feet", "hands", "accessory", "outerwear"})


class WardrobeError(ValueError):
    """Invalid, stale, conflicting, or unsupported wardrobe transition."""


@dataclass(frozen=True)
class WardrobeState:
    schema: str
    body_revision: str
    sequence: int
    equipped: tuple[tuple[str, str], ...]
    recent_operations: tuple[str, ...]
    revision: str


@dataclass(frozen=True)
class TransitionReceipt:
    schema: str
    operation_id: str
    sequence: int
    action: str
    slot: str
    garment_id: str | None
    previous_revision: str
    revision: str
    provenance: str = PROVENANCE


def _identifier(value, field):
    if type(value) is not str or not _ID.fullmatch(value):
        raise WardrobeError(f"invalid {field}")
    return value


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _digest(payload):
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _state_payload(body_revision, sequence, equipped, recent_operations):
    return {
        "schema": SCHEMA,
        "provenance": PROVENANCE,
        "body_revision": body_revision,
        "sequence": sequence,
        "equipped": [[slot, garment] for slot, garment in equipped],
        "recent_operations": list(recent_operations),
    }


def _valid_equipped(equipped):
    if type(equipped) is not tuple or len(equipped) > MAX_ITEMS:
        raise WardrobeError("invalid equipped collection")
    seen_slots, seen_garments = set(), set()
    normalized = []
    for pair in equipped:
        if type(pair) is not tuple or len(pair) != 2:
            raise WardrobeError("invalid equipped entry")
        slot, garment = pair
        if type(slot) is not str or slot not in _SLOTS:
            raise WardrobeError("unsupported slot")
        _identifier(garment, "garment id")
        if slot in seen_slots or garment in seen_garments:
            raise WardrobeError("duplicate slot or garment")
        seen_slots.add(slot)
        seen_garments.add(garment)
        normalized.append((slot, garment))
    return tuple(sorted(normalized))


def _make_state(body_revision, sequence, equipped, recent_operations):
    payload = _state_payload(body_revision, sequence, equipped, recent_operations)
    return WardrobeState(SCHEMA, body_revision, sequence, equipped,
                         recent_operations, _digest(payload))


def initial_state(body_revision):
    """Create an empty synthetic wardrobe bound to a caller-owned rig revision."""
    body_revision = _identifier(body_revision, "body revision")
    return _make_state(body_revision, 0, (), ())


def _verify_state(state):
    if (type(state) is not WardrobeState or type(state.schema) is not str
            or state.schema != SCHEMA):
        raise WardrobeError("unsupported state schema")
    _identifier(state.body_revision, "body revision")
    if type(state.sequence) is not int or not 0 <= state.sequence <= 2**63 - 2:
        raise WardrobeError("invalid sequence")
    equipped = _valid_equipped(state.equipped)
    if equipped != state.equipped:
        raise WardrobeError("noncanonical equipped order")
    if type(state.recent_operations) is not tuple or len(state.recent_operations) > MAX_ITEMS:
        raise WardrobeError("invalid operation history")
    if any(type(x) is not str or not _ID.fullmatch(x) for x in state.recent_operations):
        raise WardrobeError("invalid operation history entry")
    if len(set(state.recent_operations)) != len(state.recent_operations):
        raise WardrobeError("duplicate operation history entry")
    if type(state.revision) is not str or _HEX_SHA256.fullmatch(state.revision) is None:
        raise WardrobeError("state revision must be a bounded lowercase SHA-256 digest")
    expected = _digest(_state_payload(state.body_revision, state.sequence,
                                      state.equipped, state.recent_operations))
    if state.revision != expected:
        raise WardrobeError("state revision mismatch")
    return state


def transition(state, *, action, slot, garment_id, operation_id,
               expected_revision, body_revision):
    """Apply one atomic equip/unequip/replace/drop metadata transition.

    Actions: equip requires an empty slot; unequip/drop require a matching
    occupied slot; replace requires an occupied slot and a new garment ID.
    A successful transition advances exactly one sequence and returns a
    detached immutable state plus a deterministic receipt.
    """
    _verify_state(state)
    operation_id = _identifier(operation_id, "operation id")
    body_revision = _identifier(body_revision, "body revision")
    if type(expected_revision) is not str or expected_revision != state.revision:
        raise WardrobeError("stale expected revision")
    if body_revision != state.body_revision:
        raise WardrobeError("body revision mismatch")
    if operation_id in state.recent_operations:
        raise WardrobeError("duplicate operation id")
    # Keep every emitted state valid for at least one subsequent verification.
    if state.sequence >= 2**63 - 2:
        raise WardrobeError("sequence limit reached")
    if type(action) is not str or action not in {"equip", "unequip", "replace", "drop"}:
        raise WardrobeError("unsupported action")
    if type(slot) is not str or slot not in _SLOTS:
        raise WardrobeError("unsupported slot")
    if garment_id is not None:
        _identifier(garment_id, "garment id")

    current = dict(state.equipped)
    old = current.get(slot)
    if action == "equip":
        if old is not None or garment_id is None:
            raise WardrobeError("equip requires an empty slot and garment")
        if garment_id in current.values():
            raise WardrobeError("garment already equipped")
        current[slot] = garment_id
        result_garment = garment_id
    elif action in {"unequip", "drop"}:
        if old is None or garment_id != old:
            raise WardrobeError("unequip/drop must identify the equipped garment")
        del current[slot]
        result_garment = old
    else:  # replace
        if old is None or garment_id is None or garment_id == old:
            raise WardrobeError("replace requires an occupied slot and a different garment")
        if garment_id in current.values():
            raise WardrobeError("replacement garment already equipped")
        current[slot] = garment_id
        result_garment = garment_id

    equipped = tuple(sorted(current.items()))
    history = (state.recent_operations + (operation_id,))[-MAX_ITEMS:]
    next_state = _make_state(state.body_revision, state.sequence + 1,
                             equipped, history)
    receipt = TransitionReceipt(
        SCHEMA, operation_id, next_state.sequence, action, slot,
        result_garment, state.revision, next_state.revision)
    return next_state, receipt
