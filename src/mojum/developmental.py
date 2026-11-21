"""Goal-free CPU-only point-room motor play with measured tabular prediction.

NOT a neural brain, reward optimizer, physical skill or real actuation.
No room geometry, object IDs, goals or future measurements are policy inputs.
"""

from __future__ import annotations

import hashlib
import json

SCHEMA = "mojum.developmental.motor-play/1"
STEP = 0.125
MOVES = (
    ("east", STEP, 0.0),
    ("north", 0.0, STEP),
    ("west", -STEP, 0.0),
    ("south", 0.0, -STEP),
)
REST = ("rest", 0.0, 0.0)


class DevelopmentalError(ValueError):
    """Reject malformed action/history before advancing a logical tick."""


def _digest(state):
    value = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MotorPlay:
    """Deterministic tabular effect predictor driving bounded motor babbling.

    The first four decisions sample each direction once. Later decisions use
    measured success rates, with periodic exploratory probes and rest.
    """

    def __init__(self):
        self.samples = {name: {"trials": 0, "blocked": 0} for name, _, _ in (*MOVES, REST)}
        self.observations = 0

    def snapshot(self):
        return {"schema": SCHEMA, "observations": self.observations,
                "samples": {name: dict(row) for name, row in self.samples.items()}}

    @classmethod
    def from_snapshot(cls, raw):
        instance = cls()
        if (not isinstance(raw, dict) or set(raw) != {"schema", "observations", "samples"}
                or raw["schema"] != SCHEMA or type(raw["observations"]) is not int
                or raw["observations"] < 0 or not isinstance(raw["samples"], dict)
                or set(raw["samples"]) != set(instance.samples)):
            raise DevelopmentalError("invalid motor-play snapshot")
        for name, row in raw["samples"].items():
            if (not isinstance(row, dict) or set(row) != {"trials", "blocked"}
                    or type(row["trials"]) is not int or type(row["blocked"]) is not int
                    or not 0 <= row["blocked"] <= row["trials"]):
                raise DevelopmentalError("invalid measured action counts")
        if sum(row["trials"] for row in raw["samples"].values()) != raw["observations"]:
            raise DevelopmentalError("motor observations do not match action history")
        instance.samples = {name: dict(row) for name, row in raw["samples"].items()}
        instance.observations = raw["observations"]
        return instance

    def choose(self, tick):
        """Select only from committed prior outcomes, never room internals."""
        if type(tick) is not int or tick != self.observations:
            raise DevelopmentalError("stale motor decision tick")
        if tick < len(MOVES):
            chosen = MOVES[tick]
        elif tick % 11 == 10:
            chosen = REST
        elif tick % 7 == 6:
            chosen = min(MOVES, key=lambda m: (self.samples[m[0]]["trials"],
                                               MOVES.index(m)))
        else:
            def score(move):
                row = self.samples[move[0]]
                return (row["trials"] - row["blocked"] + 1) / (row["trials"] + 2)
            chosen = max(MOVES, key=score)
        name, dx, dy = chosen
        row = self.samples[name]
        return {
            "schema": SCHEMA, "kind": name, "dx": dx, "dy": dy,
            "expected_unblocked_probability":
                (row["trials"] - row["blocked"] + 1) / (row["trials"] + 2),
            "prior_trials": row["trials"],
            "interface": "bounded_point_room_fixture_only",
        }

    def observe(self, *, tick, kind, blocked):
        """Update only after the world returned the measured outcome."""
        if (type(tick) is not int or tick != self.observations
                or type(kind) is not str or kind not in self.samples
                or type(blocked) is not bool):
            raise DevelopmentalError("invalid or stale measured motor outcome")
        self.samples[kind]["trials"] += 1
        self.samples[kind]["blocked"] += int(blocked)
        self.observations += 1
        return {"observed_blocked": blocked, "observations": self.observations,
                "model_sha256": _digest(self.snapshot()),
                "training_source": "own_measured_point_room_fixture_outcome"}
