"""Conservative *synthetic-only* empirical motor advice from measured outcomes.

This is a deterministic, read-only adapter over the R0.14 observed-outcomes
table. It cannot command hardware or inspect the obstacle map, toy IDs,
color, hidden material, future states, or motor-safety credentials.
"""
from __future__ import annotations

from .developmental_policy import ACTIONS
from .temporal_outcomes import RangeOutcomeMemory, _context

SCHEMA = "mojum.temporal.action-advisor/1"
MIN_BLOCKED_SAMPLES = 2
MOTOR_ACTIONS = tuple(action for action in ACTIONS if action != "rest")


class ActionAdviceError(ValueError):
    """Unsupported policy or invalid learned-outcome evidence."""


def advisory(memory, observation, mode):
    """Offer *soft* avoidance hints only for repeatedly observed 100% blocks.

    A single block is inconclusive; a single observed success prevents an
    inhibition. Unknown/mixed cases remain available for exploration. No
    actuator safety guarantee and no extrapolation beyond the numeric cue.
    """
    if type(memory) is not RangeOutcomeMemory or mode not in ("observe", "learned"):
        raise ActionAdviceError("invalid empirical advisor inputs")
    candidates = []
    hints = []
    for action in MOTOR_ACTIONS:
        key = _context(observation, action)
        outcomes = memory.histograms.get(key, {})
        seen = sum(outcomes.values())
        blocked = sum(n for outcome, n in outcomes.items()
                      if outcome.endswith("|1"))
        eligible = seen >= MIN_BLOCKED_SAMPLES and blocked == seen
        candidates.append({
            "action": action, "observations": seen,
            "blocked_samples": blocked, "success_samples": seen - blocked,
            "blocked_supported": eligible,
        })
        if eligible:
            hints.append(action)
    return {
        "schema": SCHEMA,
        "mode": mode, "min_blocked_samples": MIN_BLOCKED_SAMPLES,
        "candidates": candidates,
        "blocked_hints": hints,
        "applied_hints": hints if mode == "learned" else [],
        "authority": "SOFT_SYNTHETIC_HINT_NOT_A_HARDWARE_SAFETY_GATE",
        "source": "measured_past_virtual_actions_only_no_geometry_oracle",
    }
