"""Deliberately contact-rich *development* stress benchmark for Mojum R0.14.

These cases were designed AFTER looking at the weak-contact 10-scene
evaluation. They are not held-out and may not be used as independent evidence
of generalization or cognition. Only predeclared synthetic room fixtures
and offline pulse streams; no privileged feedback reaches the controllers.
"""
from __future__ import annotations

import hashlib
import json

from .neutral_probe_eval import pulses_for
from .neutral_probe_trial import compare
from .world_room import Room

SCHEMA = "mojum.research.contact-rich-stress/1"
CLASSIFICATION = "POST_BASELINE_DEVELOPMENT_STRESS_NOT_HELD_OUT"
ARMS = ("adaptive", "frozen", "local_cell")
MIN_CONTACT_SCENES = 10
MAX_CASES = 16

# Purposefully start near/cornering boundaries to force independently
# measured contacts on the frozen policy. Not an oracle: the geometry is
# *never* supplied to either policy, only to the canonical world worker.
CASES = (
    {"id": "tight-square-alt", "room": [0.08, 0.08, []],
     "start": [0.08, 0.08], "ticks": 160, "pulse": "alternating", "seed": 0},
    {"id": "tight-square-seeded", "room": [0.12, 0.12, []],
     "start": [0.12, 0.12], "ticks": 192, "pulse": "seeded", "seed": 7},
    {"id": "small-square-bursts", "room": [0.16, 0.16, []],
     "start": [0.16, 0.16], "ticks": 160, "pulse": "bursts", "seed": 0},
    {"id": "wide-top-seeded", "room": [0.24, 0.08, []],
     "start": [0.24, 0.08], "ticks": 192, "pulse": "seeded", "seed": 39},
    {"id": "tall-top-seeded", "room": [0.08, 0.24, []],
     "start": [0.08, 0.24], "ticks": 192, "pulse": "seeded", "seed": 101},
    {"id": "wide-top-off", "room": [0.20, 0.12, []],
     "start": [0.20, 0.12], "ticks": 160, "pulse": "off", "seed": 0},
    {"id": "tall-top-bursts", "room": [0.12, 0.20, []],
     "start": [0.12, 0.20], "ticks": 160, "pulse": "bursts", "seed": 0},
    {"id": "bottom-left-seeded", "room": [0.12, 0.12, []],
     "start": [0.0, 0.0], "ticks": 192, "pulse": "seeded", "seed": 17},
    {"id": "top-left-alt", "room": [0.16, 0.16, []],
     "start": [0.0, 0.16], "ticks": 160, "pulse": "alternating", "seed": 0},
    {"id": "bottom-right-seeded", "room": [0.16, 0.08, []],
     "start": [0.16, 0.0], "ticks": 192, "pulse": "seeded", "seed": 3},
    {"id": "corner-with-thin-wall",
     "room": [0.24, 0.24, [[0.10, 0.03, 0.005, 0.18]]],
     "start": [0.24, 0.24], "ticks": 192, "pulse": "seeded", "seed": 19},
    {"id": "corner-with-central-block",
     "room": [0.16, 0.16, [[0.06, 0.06, 0.04, 0.04]]],
     "start": [0.16, 0.16], "ticks": 192, "pulse": "seeded", "seed": 43},
)


def _hash(obj):
    return hashlib.sha256(json.dumps(
        obj, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _rates(arm):
    attempts, ticks = arm["attempted_movement_count"], arm["ticks"]
    return {
        "blocked": arm["blocked_count"],
        "rest": arm["rest_count"],
        "attempts": attempts,
        "success": arm["successful_movement_count"],
        "unique_positions": arm["unique_positions_1um"],
        "stalled": arm["stalled_warning"],
        "longest_rest": arm["longest_rest_streak"],
        # Explicit denominators prevent "fewest collisions wins" bias.
        "blocked_per_attempt": round(arm["blocked_count"] / attempts, 6)
            if attempts else None,
        "successful_movement_per_tick":
            round(arm["successful_movement_count"] / ticks, 6),
        "trace_sha256": _hash(arm["trace"]),
    }


def evaluate():
    """Descriptive three-arm report; enforce adequate control contact exposure.

    Exposure is based ONLY on the frozen arm's observed collisions. A lack of
    contacts cannot masquerade as good generalization. Tests use the same
    pulse bits and initial state per case but each controller sees its own
    resulting trajectory, as a causal benchmark necessarily requires.
    """
    if not 10 <= len(CASES) <= MAX_CASES:
        raise ValueError("invalid bounded stress suite")
    names = [case["id"] for case in CASES]
    if len(names) != len(set(names)):
        raise ValueError("duplicated scenario ID")
    scenes = []
    for spec in CASES:
        width, height, obstacles = spec["room"]
        room = Room(width, height, tuple(tuple(o) for o in obstacles))
        pulses = pulses_for(spec)
        run = compare(room=room, start=tuple(spec["start"]), pulses=pulses)
        arms = {}
        for name in ARMS:
            raw = run[name]
            metrics = _rates(raw)
            if (metrics["attempts"] + metrics["rest"] != raw["ticks"]
                    or metrics["success"] + metrics["blocked"]
                       != metrics["attempts"]):
                raise AssertionError("causal movement accounting inconsistent")
            arms[name] = metrics
        scenes.append({
            "id": spec["id"],
            "ticks": spec["ticks"],
            "fixture_sha256": _hash(spec),
            "pulse_sha256": _hash(pulses),
            "arms": arms,
        })

    contact_ids = tuple(
        scene["id"] for scene in scenes
        if scene["arms"]["frozen"]["blocked"] > 0
    )
    enough = len(contact_ids) >= MIN_CONTACT_SCENES
    by_arm = {
        arm: {
            "blocked": sum(s["arms"][arm]["blocked"] for s in scenes),
            "rest": sum(s["arms"][arm]["rest"] for s in scenes),
            "attempts": sum(s["arms"][arm]["attempts"] for s in scenes),
            "success": sum(s["arms"][arm]["success"] for s in scenes),
            "stalled_scenes": sum(s["arms"][arm]["stalled"] for s in scenes),
        } for arm in ARMS
    }
    return {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "suite_sha256": _hash(CASES),
        "scene_count": len(scenes),
        "total_ticks_each_arm": sum(s["ticks"] for s in scenes),
        "evidence_quality": {
            "status": ("PURPOSEFUL_CONTACT_EXPOSURE"
                       if enough else "INSUFFICIENT_CONTACT_EXPOSURE"),
            "min_required_contact_scenes": MIN_CONTACT_SCENES,
            "contact_scene_count": len(contact_ids),
            "contact_scene_ids": list(contact_ids),
            "passes_contact_gate": enough,
            "not_blind_or_held_out": True,
            "no_generalization_claim": True,
        },
        "totals": by_arm,
        "local_vs_frozen": {
            "fewer_blocks": sum(
                s["arms"]["local_cell"]["blocked"] <
                s["arms"]["frozen"]["blocked"] for s in scenes
            ),
            "equal_blocks": sum(
                s["arms"]["local_cell"]["blocked"] ==
                s["arms"]["frozen"]["blocked"] for s in scenes
            ),
            "more_blocks": sum(
                s["arms"]["local_cell"]["blocked"] >
                s["arms"]["frozen"]["blocked"] for s in scenes
            ),
            "fewer_successes": sum(
                s["arms"]["local_cell"]["success"] <
                s["arms"]["frozen"]["success"] for s in scenes
            ),
            "more_successes": sum(
                s["arms"]["local_cell"]["success"] >
                s["arms"]["frozen"]["success"] for s in scenes
            ),
        },
        "scenes": scenes,
    }


def main():
    report = evaluate()
    # CLI shows only aggregate outcomes + method caveat; detailed per-case
    # synthetic metrics are available from evaluate() for independent QA.
    summary = {
        key: report[key] for key in (
            "schema", "classification", "suite_sha256", "scene_count",
            "total_ticks_each_arm", "evidence_quality", "totals",
            "local_vs_frozen",
        )
    }
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
    # Keep the measured report visible, but do not let insufficient frozen-arm
    # contact exposure count as a successful development-stress CI run.
    if not report["evidence_quality"]["passes_contact_gate"]:
        raise SystemExit("contact-exposure QA gate failed")
    return summary


if __name__ == "__main__":
    main()
