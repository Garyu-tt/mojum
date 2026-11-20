"""Tiny CPU-only self-supervised pulse predictor.

Actual online gradient updates from a one-tick-delayed observation label.
Not a complete brain, general intelligence, consciousness, physiology or
motor-control policy. No optional NumPy/Torch/GPU imports.
"""

import copy
import hashlib
import json
import math


SCHEMA = "mojum.neural.delayed-pulse/1"


class NeuralError(ValueError):
    pass


def _finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clamp(value, low, high):
    return min(high, max(low, value))


class PulsePredictor:
    """One-step delayed online LMS learner in a bounded 3-feature space.

    Features from previous tick: bias, heartbeat phase / 1000, prior pulse.
    The current pulse is used only as the previous features' *label*.
    The learner never emits actuation commands.
    """

    def __init__(self, learning_rate=0.10):
        if not _finite(learning_rate) or not (0.0 < learning_rate <= 0.5):
            raise NeuralError("invalid learning rate")
        self._rate = float(learning_rate)
        self._weights = [0.5, 0.0, 0.0]
        self._pending = None
        self._updates = 0
        self._loss_sum = 0.0
        self._last_observed_at_ns = None

    @property
    def updates(self):
        return self._updates

    def snapshot(self):
        return {
            "schema": SCHEMA,
            "learning_rate": self._rate,
            "weights": list(self._weights),
            "pending_features": copy.deepcopy(self._pending),
            "updates": self._updates,
            "loss_sum": self._loss_sum,
            "last_observed_at_ns": self._last_observed_at_ns,
        }

    @classmethod
    def from_snapshot(cls, state):
        if not isinstance(state, dict) or set(state) != {
            "schema", "learning_rate", "weights", "pending_features",
            "updates", "loss_sum", "last_observed_at_ns",
        } or state["schema"] != SCHEMA:
            raise NeuralError("incompatible neural snapshot")
        inst = cls(state["learning_rate"])
        weights = state["weights"]
        pending = state["pending_features"]
        updates = state["updates"]
        last = state["last_observed_at_ns"]
        if (not isinstance(weights, list) or len(weights) != 3
                or not all(_finite(w) and -2 <= w <= 2 for w in weights)):
            raise NeuralError("invalid weights")
        if (pending is not None and
                (not isinstance(pending, list) or len(pending) != 3
                 or not all(_finite(v) and 0 <= v <= 1 for v in pending)
                 or pending[0] != 1.0)):
            raise NeuralError("invalid pending features")
        if (type(updates) is not int or updates < 0
                or not _finite(state["loss_sum"]) or state["loss_sum"] < 0):
            raise NeuralError("invalid online-learning counters")
        if ((last is None and (pending is not None or updates != 0))
                or (last is not None and (type(last) is not int or last < 0 or pending is None))):
            raise NeuralError("invalid previous sensory time")
        inst._weights = list(weights)
        inst._pending = copy.deepcopy(pending)
        inst._updates = updates
        inst._loss_sum = float(state["loss_sum"])
        inst._last_observed_at_ns = last
        return inst

    def _estimate(self, features):
        return _clamp(sum(w * x for w, x in zip(self._weights, features)), 0.0, 1.0)

    def observe(self, sensory):
        if not isinstance(sensory, dict) or sensory.get("kind") != "interoception":
            raise NeuralError("interoception required")
        heartbeat = sensory.get("heartbeat")
        at = sensory.get("observed_at_ns")
        if not isinstance(heartbeat, dict) or type(at) is not int or at < 0:
            raise NeuralError("invalid timestamp/heartbeat")
        if self._last_observed_at_ns is not None and at <= self._last_observed_at_ns:
            raise NeuralError("stale or duplicate sensory frame")
        phase, pulse = heartbeat.get("phase_milli"), heartbeat.get("pulse")
        if type(phase) is not int or not 0 <= phase < 1000 or type(pulse) is not bool:
            raise NeuralError("invalid synthetic pulse features")
        features = [1.0, phase / 1000.0, float(pulse)]
        prediction, loss = None, None
        trained = self._pending is not None
        if trained:
            # Crucial: previous tick's features predict current tick's pulse.
            # No feature from current tick is used to predict its own label.
            prediction = self._estimate(self._pending)
            error = float(pulse) - prediction
            loss = error * error
            self._weights = [
                _clamp(w + self._rate * error * feature, -2.0, 2.0)
                for w, feature in zip(self._weights, self._pending)
            ]
            self._updates += 1
            self._loss_sum += loss
        self._pending = features
        self._last_observed_at_ns = at
        frame = {
            "schema": SCHEMA,
            "status": "ONLINE_SHADOW_PREDICTOR",
            "observed_at_ns": at,
            "trained": trained,
            "prediction_from_prior_tick": prediction,
            "observed_current_pulse": pulse,
            "squared_error": loss,
            "next_tick_prediction": self._estimate(features),
            "updates": self._updates,
            "mean_squared_error": (self._loss_sum / self._updates
                                   if self._updates else None),
            "weights_sha256": _hash(self._weights),
            "motor_intention": None,
            "training_source": "synthetic_heartbeat_delayed_self_supervision",
        }
        return frame
