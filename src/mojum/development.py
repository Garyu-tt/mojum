"""Opt-in deterministic GENESIS development clock for synthetic fixtures.

This module does not modify Aquarium journals, run a neural system, age a
biological body, or change the existing GENESIS seed/phenotype wire format.
Three independently supplied nonnegative counters make elapsed time, visual
maturation and learning activity impossible to conflate accidentally.
"""

from __future__ import annotations

from collections.abc import Mapping

POLICY_SCHEMA = "mojum.genesis.development-policy/1"
STATE_SCHEMA = "mojum.genesis.development-state/1"
PROJECTION_SCHEMA = "mojum.genesis.development-projection/1"
MAX_COUNTER = 2**63 - 1
MAX_AGE_MILLI = 200_000
MAX_TICKS_PER_YEAR = 10**12


class DevelopmentError(ValueError):
    """Invalid development contract or non-replayable state transition."""


def _object(value, fields, label):
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DevelopmentError(f"{label} must have exactly {sorted(fields)}")


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DevelopmentError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def default_policy():
    """Independent, JSON-compatible policy, not shared mutable state."""
    return {
        "schema": POLICY_SCHEMA,
        "start_age_milli": 0,
        "maturity_age_milli": 16_000,
        "ticks_per_virtual_year": 10_000,
        "senescence_enabled": False,
    }


def validate_policy(policy):
    """Reject unknown fields and any attempt to enable age-based decay."""
    _object(
        policy,
        {"schema", "start_age_milli", "maturity_age_milli", "ticks_per_virtual_year", "senescence_enabled"},
        "development policy",
    )
    if policy["schema"] != POLICY_SCHEMA:
        raise DevelopmentError("Unsupported development policy schema")
    start = _integer(policy["start_age_milli"], "start_age_milli", 0, MAX_AGE_MILLI)
    end = _integer(policy["maturity_age_milli"], "maturity_age_milli", 1, MAX_AGE_MILLI)
    if end < start:
        raise DevelopmentError("Maturity cannot precede the starting developmental age")
    _integer(policy["ticks_per_virtual_year"], "ticks_per_virtual_year", 1, MAX_TICKS_PER_YEAR)
    if policy["senescence_enabled"] is not False:
        raise DevelopmentError("Age-related senescence is unsupported by this no-senescence policy")
    return dict(policy)


def initial_state():
    return {"schema": STATE_SCHEMA, "elapsed_ticks": 0, "development_ticks": 0, "learning_events": 0}


def validate_state(state):
    _object(state, {"schema", "elapsed_ticks", "development_ticks", "learning_events"}, "development state")
    if state["schema"] != STATE_SCHEMA:
        raise DevelopmentError("Unsupported development state schema")
    for field in ("elapsed_ticks", "development_ticks", "learning_events"):
        _integer(state[field], field, 0, MAX_COUNTER)
    return dict(state)


def advance(state, *, elapsed_ticks=1, development_ticks=1, learning_events=0):
    """Pure transition; independent increments, no mutation on success or failure.

    For external journals, commit this result only after the journal's own
    compare-and-append succeeds. This function is not a durable writer.
    """
    checked = validate_state(state)
    increments = {
        "elapsed_ticks": elapsed_ticks,
        "development_ticks": development_ticks,
        "learning_events": learning_events,
    }
    for field, increment in increments.items():
        _integer(increment, field + " increment", 0, MAX_COUNTER)
        if increment > MAX_COUNTER - checked[field]:
            raise DevelopmentError(f"{field} counter overflow")
    return {"schema": STATE_SCHEMA, **{field: checked[field] + inc for field, inc in increments.items()}}


def project(state, policy=None):
    """Deterministic fixed-point projection, independent of wall-clock time.

    Maturation saturates at the policy's ceiling; elapsed time and reported
    learning events continue. 'Learning events' is a counter, not evidence
    that an organism, neural system or conscious entity learned anything.
    """
    checked = validate_state(state)
    p = validate_policy(default_policy() if policy is None else policy)
    ticks_per_year = p["ticks_per_virtual_year"]
    age = min(
        p["maturity_age_milli"],
        p["start_age_milli"] + checked["development_ticks"] * 1000 // ticks_per_year,
    )
    return {
        "schema": PROJECTION_SCHEMA,
        "elapsed_ticks": checked["elapsed_ticks"],
        "development_ticks": checked["development_ticks"],
        "learning_events": checked["learning_events"],
        "elapsed_virtual_years_milli": checked["elapsed_ticks"] * 1000 // ticks_per_year,
        "development_age_milli": age,
        "maturity_reached": age == p["maturity_age_milli"],
        "senescence_enabled": False,
        "age_related_death_enabled": False,
        "classification": "synthetic_contract_not_biological_age_or_evidence_of_learning",
    }
