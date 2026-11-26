"""Fail-closed admission for externally supplied synthetic neural checkpoints.

Use restore_snapshot() at trust boundaries. The core PulsePredictor v1 API is
kept unchanged for historical replay compatibility.
"""
from __future__ import annotations

from mojum.neural import NATIVE_PULSE_SUM, NeuralError, PulsePredictor, SCHEMA

_MAX_NODES = 64
_MAX_DEPTH = 4
_MAX_INTEGER_BITS = 128
_FIELDS = frozenset({
    "schema", "learning_rate", "weights", "pending_features",
    "updates", "loss_sum", "last_observed_at_ns",
})


def _admit_native_tree(root: object) -> None:
    """Reject subclasses, cycles, exotic containers and unbounded scalars."""
    if type(root) is not dict:
        raise NeuralError("snapshot must be a native dict")
    stack = [(root, 0)]
    seen: set[int] = set()
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise NeuralError("snapshot exceeds structural budget")
        kind = type(value)
        if kind is dict:
            identity = id(value)
            if identity in seen:
                raise NeuralError("snapshot contains a cycle or shared container")
            seen.add(identity)
            for key, child in dict.items(value):
                if type(key) is not str:
                    raise NeuralError("snapshot keys must be native strings")
                stack.append((child, depth + 1))
        elif kind is list:
            identity = id(value)
            if identity in seen:
                raise NeuralError("snapshot contains a cycle or shared container")
            seen.add(identity)
            for child in list.__iter__(value):
                stack.append((child, depth + 1))
        elif kind is str:
            if len(value) > 64:
                raise NeuralError("snapshot string exceeds limit")
        elif kind is int:
            if value.bit_length() > _MAX_INTEGER_BITS:
                raise NeuralError("snapshot integer exceeds limit")
        elif kind is float:
            if value != value or value in (float("inf"), float("-inf")):
                raise NeuralError("snapshot contains a non-finite number")
        elif value is None:
            continue
        else:
            raise NeuralError("snapshot contains a non-native value")


def _clone_native(value: object) -> object:
    """Copy only already-admitted exact builtin containers and scalars."""
    kind = type(value)
    if kind is dict:
        return {key: _clone_native(child) for key, child in dict.items(value)}
    if kind is list:
        return [_clone_native(child) for child in list.__iter__(value)]
    return value


def restore_snapshot(state: object, *, summation: str = NATIVE_PULSE_SUM) -> PulsePredictor:
    """Validate and restore a detached predictor from a PUBLIC_SYNTHETIC snapshot.

    This is an admission wrapper, not a wire-format migration or a signature.
    The caller remains responsible for provenance, authorization and persistence.
    """
    if type(summation) is not str or summation not in ("ordered", "compensated"):
        raise NeuralError("unsupported pulse summation")
    _admit_native_tree(state)
    if set(dict.keys(state)) != _FIELDS:
        raise NeuralError("incompatible neural snapshot")
    if dict.__getitem__(state, "schema") != SCHEMA:
        raise NeuralError("incompatible neural snapshot")
    detached = _clone_native(state)
    return PulsePredictor.from_snapshot(detached, summation=summation)
