"""R0.15 isolated goal-free motor-play profile with a bounded rest streak.

Why this module exists
----------------------
The shipped ``mojum.developmental.MotorPlay`` (schema
``mojum.developmental.motor-play/1``) can be driven by a caller-supplied
``avoid`` set that permanently excludes every direction that has ever been
blocked. Once all four directions are excluded the caller falls back to the
full move set, but an avoid-set that never shrinks still produces long rest /
repeat runs, and the wire schema cannot express the difference. Issue #106
therefore asks for a versioned policy rather than a silent semantic change.

This module is that versioned profile, and it is deliberately **isolated**:

* it has its own schema string (``mojum.developmental.motor-play/2``);
* it is not imported by ``mojum.aquarium`` and does not alter any existing
  journal, manifest or replay path;
* it never accepts an ``avoid``/``prefer`` set, so no external outcome can
  permanently ban a direction.

The only learning signal is the policy's own measured ``blocked`` outcome,
exactly like v1. It never reads room geometry, object ids, goals, rewards,
future measurements or hardware state.

Invariants
----------
1. ``rest_streak`` can never exceed ``MAX_CONSECUTIVE_REST`` records.
2. ``observe`` rejects a rest outcome once the cap is reached, so a policy
   violation fails closed instead of silently stalling.
3. Every direction is probed at least ``EXPLORATION_QUOTA`` times before
   measured success scores are allowed to dominate selection.
4. An outcome must match the decision that immediately preceded it: ``observe``
   only accepts the ``pending`` kind returned by the last ``choose`` call, so a
   caller cannot fabricate history for an action it never selected.
5. Ties between equally scored directions are broken by a SHA-256 digest of the
   policy's own measured counters. A fixed cardinal-order tie-break would make
   the policy cycle east/north/west/south and cancel its own displacement.
6. The snapshot is a strict, versioned, bounded JSON document; ``from_snapshot``
   rejects anything that does not satisfy the invariants it can check.

NOT a neural brain, a planner, a reward optimizer, a real actuator or evidence
of intelligent navigation.
"""

from __future__ import annotations

import hashlib
import json

SCHEMA = "mojum.developmental.motor-play/2"
PROFILE = "bounded_attempt_v2"
STEP = 0.125
MAX_OBSERVATIONS = 9_999
#: Hard cap on consecutive recorded rest outcomes.
MAX_CONSECUTIVE_REST = 3
#: Trials each direction needs before measurement-driven scoring is allowed.
EXPLORATION_QUOTA = 2
MOVES = (
    ("east", STEP, 0.0),
    ("north", 0.0, STEP),
    ("west", -STEP, 0.0),
    ("south", 0.0, -STEP),
)
REST = ("rest", 0.0, 0.0)
ACTION_NAMES = tuple(name for name, _, _ in (*MOVES, REST))


class MotorPlayV2Error(ValueError):
    """Reject malformed action history before advancing a logical tick."""


def _digest(state):
    value = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MotorPlayV2:
    """Deterministic tabular motor play whose rest runs are provably bounded.

    Decision ladder for tick ``t`` (each branch is exclusive):

    1. ``rest_streak == MAX_CONSECUTIVE_REST`` -> forced move. Never rest.
    2. ``t < len(MOVES)`` -> one sample of each cardinal direction.
    3. any direction below ``EXPLORATION_QUOTA`` -> least-tried direction.
    4. ``t % 13 == 12`` -> one scheduled rest.
    5. otherwise -> direction with the best Laplace-smoothed success rate,
       ties broken by fewer trials and then fixed cardinal order.
    """

    def __init__(self):
        self.samples = {name: {"trials": 0, "blocked": 0} for name in ACTION_NAMES}
        self.observations = 0
        self.rest_streak = 0
        self.pending = None
        self.last_reason = None

    # -- state -----------------------------------------------------------
    def snapshot(self):
        return {
            "schema": SCHEMA,
            "profile": PROFILE,
            "observations": self.observations,
            "rest_streak": self.rest_streak,
            "max_consecutive_rest": MAX_CONSECUTIVE_REST,
            "pending": self.pending,
            "samples": {name: dict(row) for name, row in self.samples.items()},
        }

    @classmethod
    def from_snapshot(cls, raw):
        instance = cls()
        if (not isinstance(raw, dict)
                or set(raw) != {"schema", "profile", "observations", "rest_streak",
                                "max_consecutive_rest", "pending", "samples"}
                or raw["schema"] != SCHEMA
                or raw["profile"] != PROFILE
                or raw["max_consecutive_rest"] != MAX_CONSECUTIVE_REST
                or type(raw["observations"]) is not int
                or not 0 <= raw["observations"] <= MAX_OBSERVATIONS
                or type(raw["rest_streak"]) is not int
                or not 0 <= raw["rest_streak"] <= MAX_CONSECUTIVE_REST
                or not (raw["pending"] is None
                        or (isinstance(raw["pending"], str)
                            and raw["pending"] in instance.samples))
                or not isinstance(raw["samples"], dict)
                or set(raw["samples"]) != set(instance.samples)):
            raise MotorPlayV2Error("invalid motor-play v2 snapshot")
        for name, row in raw["samples"].items():
            if (not isinstance(row, dict) or set(row) != {"trials", "blocked"}
                    or type(row["trials"]) is not int or type(row["blocked"]) is not int
                    or not 0 <= row["blocked"] <= row["trials"]
                    or row["trials"] > MAX_OBSERVATIONS):
                raise MotorPlayV2Error("invalid measured action counts")
        if sum(row["trials"] for row in raw["samples"].values()) != raw["observations"]:
            raise MotorPlayV2Error("motor observations do not match action history")
        if raw["rest_streak"] > raw["samples"]["rest"]["trials"]:
            raise MotorPlayV2Error("rest streak exceeds recorded rests")
        if raw["samples"]["rest"]["blocked"] != 0:
            raise MotorPlayV2Error("rest cannot have blocked evidence")
        if raw["pending"] == "rest" and raw["rest_streak"] >= MAX_CONSECUTIVE_REST:
            raise MotorPlayV2Error("pending rest outcome would exceed the cap")
        instance.samples = {name: dict(row) for name, row in raw["samples"].items()}
        instance.observations = raw["observations"]
        instance.rest_streak = raw["rest_streak"]
        instance.pending = raw["pending"]
        # A pending action is a promise made by choose(), not an arbitrary
        # caller-supplied label. Recompute against the restored counters so a
        # forged snapshot cannot manufacture an impossible action history.
        if instance.pending is not None:
            if (instance.observations >= MAX_OBSERVATIONS
                    or instance.choose(instance.observations)["kind"] != raw["pending"]):
                raise MotorPlayV2Error("pending action does not match deterministic choice")
        return instance

    # -- decisions -------------------------------------------------------
    def choose(self, tick):
        """Return the next bounded action intent; never reads world geometry."""
        if (type(tick) is not int or not 0 <= tick < MAX_OBSERVATIONS
                or tick != self.observations):
            raise MotorPlayV2Error("stale motor decision tick")
        if self.rest_streak >= MAX_CONSECUTIVE_REST:
            chosen, reason = self._least_tried_move(), "rest_cap_forced_move"
        elif tick < len(MOVES):
            chosen, reason = MOVES[tick], "initial_cardinal_probe"
        elif any(self.samples[move[0]]["trials"] < EXPLORATION_QUOTA for move in MOVES):
            chosen, reason = self._least_tried_move(), "exploration_quota"
        elif tick % 13 == 12:
            chosen, reason = REST, "scheduled_rest"
        else:
            chosen, reason = self._best_scored_move(), "measured_success"
        name, dx, dy = chosen
        row = self.samples[name]
        self.pending = name
        self.last_reason = reason
        return {
            "schema": SCHEMA,
            "profile": PROFILE,
            "kind": name,
            "dx": dx,
            "dy": dy,
            "reason": reason,
            "expected_unblocked_probability":
                (row["trials"] - row["blocked"] + 1) / (row["trials"] + 2),
            "prior_trials": row["trials"],
            "rest_streak": self.rest_streak,
            "rest_cap": MAX_CONSECUTIVE_REST,
            "interface": "bounded_point_room_fixture_only",
        }

    def _least_tried_move(self):
        return min(MOVES, key=lambda move: (self.samples[move[0]]["trials"],
                                            self.samples[move[0]]["blocked"],
                                            MOVES.index(move)))

    def _best_scored_move(self):
        def rank(move):
            row = self.samples[move[0]]
            smoothed = (row["trials"] - row["blocked"] + 1) / (row["trials"] + 2)
            # Deterministic hash tie-break over own measured counters. A fixed
            # cardinal order would cycle east/north/west/south and cancel the
            # policy's own displacement, collapsing coverage to a single cell.
            tie = hashlib.sha256(
                f"{self.observations}:{move[0]}:{row['trials']}:{row['blocked']}".encode()
            ).digest()
            return (-smoothed, tie)
        return min(MOVES, key=rank)

    # -- outcomes --------------------------------------------------------
    def observe(self, *, tick, kind, blocked):
        """Record only the measured outcome of the pending chosen action."""
        if (type(tick) is not int or tick != self.observations
                or type(kind) is not str or kind not in self.samples
                or type(blocked) is not bool
                or kind != self.pending
                or self.observations >= MAX_OBSERVATIONS):
            raise MotorPlayV2Error("invalid, unpaired or stale measured motor outcome")
        if kind == "rest" and blocked:
            raise MotorPlayV2Error("rest cannot be blocked in the point-room fixture")
        if kind == "rest" and self.rest_streak >= MAX_CONSECUTIVE_REST:
            raise MotorPlayV2Error("rest streak cap violated by recorded outcome")
        self.samples[kind]["trials"] += 1
        self.samples[kind]["blocked"] += int(blocked)
        self.observations += 1
        self.pending = None
        self.rest_streak = self.rest_streak + 1 if kind == "rest" else 0
        return {
            "observed_blocked": blocked,
            "observations": self.observations,
            "rest_streak": self.rest_streak,
            "model_sha256": _digest(self.snapshot()),
            "training_source": "own_measured_point_room_fixture_outcome",
        }
