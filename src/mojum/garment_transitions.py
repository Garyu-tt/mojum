"""Deterministic, offline garment equip/remove state machine.

This module owns no physics, renderer, assets, or authoritative organism state.
Consumers may journal accepted transitions and replay them from an empty state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

SCHEMA = "mojum.garment.transitions/1"
MAX_GARMENTS = 32
MAX_ID_LENGTH = 96
VALID_SLOTS = frozenset({"head", "torso", "hands", "legs", "feet"})
VALID_LAYERS = ("base", "middle", "outer")
_LAYER_RANK = {name: index for index, name in enumerate(VALID_LAYERS)}


class GarmentTransitionError(ValueError):
    """An invalid or non-replayable garment transition."""


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_ID_LENGTH:
        raise GarmentTransitionError(f"{name} must be a bounded nonempty string")
    if not all(c.isascii() and (c.isalnum() or c in "_-.") for c in value):
        raise GarmentTransitionError(f"{name} contains unsupported characters")
    return value


@dataclass(frozen=True)
class Garment:
    garment_id: str
    slot: str
    layer: str

    def __post_init__(self) -> None:
        _identifier(self.garment_id, "garment_id")
        if self.slot not in VALID_SLOTS:
            raise GarmentTransitionError("unsupported garment slot")
        if self.layer not in _LAYER_RANK:
            raise GarmentTransitionError("unsupported garment layer")


@dataclass(frozen=True)
class Transition:
    sequence: int
    action: str
    garment: Garment

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise GarmentTransitionError("sequence must be a positive integer")
        if self.action not in ("equip", "remove"):
            raise GarmentTransitionError("unsupported transition action")
        if not isinstance(self.garment, Garment):
            raise GarmentTransitionError("garment must be a Garment")


@dataclass(frozen=True)
class Wardrobe:
    revision: int = 0
    worn: tuple[Garment, ...] = ()

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise GarmentTransitionError("invalid wardrobe revision")
        if not isinstance(self.worn, tuple) or len(self.worn) > MAX_GARMENTS:
            raise GarmentTransitionError("invalid garment collection")
        ids: set[str] = set()
        occupied: set[tuple[str, str]] = set()
        for garment in self.worn:
            if not isinstance(garment, Garment):
                raise GarmentTransitionError("invalid garment entry")
            if garment.garment_id in ids or (garment.slot, garment.layer) in occupied:
                raise GarmentTransitionError("duplicate garment or occupied layer")
            ids.add(garment.garment_id)
            occupied.add((garment.slot, garment.layer))
        if tuple(sorted(self.worn, key=_sort_key)) != self.worn:
            raise GarmentTransitionError("worn garments must have canonical order")


def _sort_key(garment: Garment) -> tuple[str, int, str]:
    return garment.slot, _LAYER_RANK[garment.layer], garment.garment_id


def apply(state: Wardrobe, event: Transition) -> Wardrobe:
    """Apply exactly one event, rejecting stale or impossible transitions."""
    if not isinstance(state, Wardrobe) or not isinstance(event, Transition):
        raise GarmentTransitionError("invalid state or event")
    if event.sequence != state.revision + 1:
        raise GarmentTransitionError("non-consecutive transition sequence")
    current = {g.garment_id: g for g in state.worn}
    garment = event.garment
    if event.action == "equip":
        if garment.garment_id in current:
            raise GarmentTransitionError("garment already equipped")
        if len(current) >= MAX_GARMENTS:
            raise GarmentTransitionError("garment capacity reached")
        if any(g.slot == garment.slot and g.layer == garment.layer for g in state.worn):
            raise GarmentTransitionError("slot/layer occupied")
        current[garment.garment_id] = garment
    else:
        if current.get(garment.garment_id) != garment:
            raise GarmentTransitionError("garment not equipped or metadata mismatch")
        # Outer layers must be removed before inner layers of the same slot.
        if any(g.slot == garment.slot and _LAYER_RANK[g.layer] > _LAYER_RANK[garment.layer]
               for g in state.worn):
            raise GarmentTransitionError("remove outer layers first")
        del current[garment.garment_id]
    return Wardrobe(event.sequence, tuple(sorted(current.values(), key=_sort_key)))


def replay(events: Iterable[Transition]) -> Wardrobe:
    state = Wardrobe()
    for event in events:
        state = apply(state, event)
    return state
