"""Locked synthetic out-of-fixture evaluation for Mojum neutral probe.

These predeclared deterministic scenes exercise three already-implemented
policies without feeding room geometry, obstacle coordinates or future labels
to either learner. Evaluation is descriptive; it cannot prove cognition,
general navigation, welfare, or out-of-distribution guarantees.
"""
from __future__ import annotations

import hashlib
import json

from .neutral_probe_trial import compare
from .world_room import Room

SCHEMA = "mojum.research.probe-multiscene-evaluation/1"
MAX_EVAL_SCENES = 16
METRICS = (
    "ticks", "blocked_count", "rest_count", "attempted_movement_count",
    "successful_movement_count", "unique_positions_1um",
)
ARMS = ("adaptive", "frozen", "local_cell")

# Fixed before evaluating these cases. Not a scientifically blinded test:
# public fixtures can be inspected by developers. Don't tune on these
# same scenes and subsequently call them independent held-out evidence.
SCENARIOS = (
    {"id": "open-small-alternating", "room": [0.64, 0.64, []],
     "start": [0.08, 0.08], "ticks": 160, "pulse": "alternating", "seed": 0},
    {"id": "open-large-seeded", "room": [4.0, 4.0, []],
     "start": [1.0, 2.0], "ticks": 192, "pulse": "seeded", "seed": 7},
    {"id": "thin-wall-alternating", "room": [0.32, 0.32,
                                          [[0.14, 0.0, 0.005, 0.32]]],
     "start": [0.04, 0.16], "ticks": 160, "pulse": "alternating", "seed": 0},
    {"id": "vertical-wall-bursts", "room": [0.80, 0.80,
                                        [[0.35, 0.15, 0.02, 0.50]]],
     "start": [0.28, 0.52], "ticks": 192, "pulse": "bursts", "seed": 0},
    {"id": "narrow-corridor-seeded", "room": [0.40, 0.64,
                                          [[0.14, 0.0, 0.06, 0.52],
                                           [0.32, 0.10, 0.04, 0.52]]],
     "start": [0.08, 0.56], "ticks": 160, "pulse": "seeded", "seed": 39},
    {"id": "offset-double-wall", "room": [1.28, 1.28,
                                       [[0.48, 0.0, 0.04, 0.80],
                                        [0.64, 0.48, 0.04, 0.80]]],
     "start": [0.24, 0.40], "ticks": 192, "pulse": "bursts", "seed": 0},
    {"id": "border-no-pulse", "room": [0.64, 0.64, []],
     "start": [0.60, 0.60], "ticks": 160, "pulse": "off", "seed": 0},
    {"id": "central-block-seeded", "room": [0.40, 0.40,
                                        [[0.16, 0.16, 0.08, 0.08]]],
     "start": [0.12, 0.12], "ticks": 192, "pulse": "seeded", "seed": 101},
    {"id": "open-small-no-pulse", "room": [0.64, 0.64, []],
     "start": [0.08, 0.08], "ticks": 160, "pulse": "off", "seed": 0},
    {"id": "thin-wall-bursts", "room": [0.32, 0.32,
                                     [[0.14, 0.0, 0.005, 0.32]]],
     "start": [0.04, 0.16], "ticks": 160, "pulse": "bursts", "seed": 0},
)


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def pulses_for(spec):
    """Explicit deterministic bit stream, independent of any action/outcome."""
    size, kind, seed = spec["ticks"], spec["pulse"], spec["seed"]
    if type(size) is not int or not 1 <= size <= 512:
        raise ValueError("invalid predeclared evaluation length")
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("invalid predeclared pulse seed")
    if kind == "alternating":
        return tuple((tick % 2) == 0 for tick in range(size))
    if kind == "off":
        return (False,) * size
    if kind == "bursts":
        return tuple((tick // 7) % 3 == 0 for tick in range(size))
    if kind == "seeded":
        value = seed
        result = []
        for _ in range(size):
            value = (1103515245 * value + 12345) & 0x7fffffff
            result.append(bool(value & (1 << 16)))
        return tuple(result)
    raise ValueError("unknown predeclared pulse stream")


def _totals(rows, arm):
    return {
        "blocked": sum(row["arms"][arm]["blocked"] for row in rows),
        "rest": sum(row["arms"][arm]["rest"] for row in rows),
        "attempts": sum(row["arms"][arm]["attempts"] for row in rows),
        "successful_moves": sum(row["arms"][arm]["successful_moves"]
                                for row in rows),
        "stalled_scenes": sum(row["arms"][arm]["stalled"] for row in rows),
    }


def evaluate():
    """Return reproducible metrics, never the per-tick synthetic trace."""
    if not 6 <= len(SCENARIOS) <= MAX_EVAL_SCENES:
        raise ValueError("evaluation suite size outside fixed budget")
    labels = [s["id"] for s in SCENARIOS]
    if len(labels) != len(set(labels)):
        raise ValueError("duplicated scenario IDs")

    scenes = []
    for spec in SCENARIOS:
        dims, start = spec["room"], spec["start"]
        room = Room(dims[0], dims[1], tuple(tuple(o) for o in dims[2]))
        pulse = pulses_for(spec)
        run = compare(room=room, start=tuple(start), pulses=pulse)
        arms = {}
        for arm in ARMS:
            data = run[arm]
            if (data["ticks"] != len(pulse)
                    or data["rest_count"] + data["attempted_movement_count"]
                       != len(pulse)
                    or data["blocked_count"] + data["successful_movement_count"]
                       != data["attempted_movement_count"]):
                raise AssertionError("contradictory synthetic evidence")
            arms[arm] = {
                "blocked": data["blocked_count"],
                "rest": data["rest_count"],
                "attempts": data["attempted_movement_count"],
                "successful_moves": data["successful_movement_count"],
                "positions": data["unique_positions_1um"],
                "longest_rest": data["longest_rest_streak"],
                "stalled": data["stalled_warning"],
                "trace_sha256": _sha(data["trace"]),
            }
        scenes.append({
            "id": spec["id"],
            "fixture_sha256": _sha(spec),
            "ticks": len(pulse),
            "pulse_sha256": _sha(pulse),
            "arms": arms,
        })
    totals = {arm: _totals(scenes, arm) for arm in ARMS}
    local_vs_frozen = {
        "fewer_blocked_scenes": sum(
            row["arms"]["local_cell"]["blocked"] <
            row["arms"]["frozen"]["blocked"] for row in scenes
        ),
        "equal_blocked_scenes": sum(
            row["arms"]["local_cell"]["blocked"] ==
            row["arms"]["frozen"]["blocked"] for row in scenes
        ),
        "more_blocked_scenes": sum(
            row["arms"]["local_cell"]["blocked"] >
            row["arms"]["frozen"]["blocked"] for row in scenes
        ),
        "more_successful_moves_scenes": sum(
            row["arms"]["local_cell"]["successful_moves"] >
            row["arms"]["frozen"]["successful_moves"] for row in scenes
        ),
        "fewer_successful_moves_scenes": sum(
            row["arms"]["local_cell"]["successful_moves"] <
            row["arms"]["frozen"]["successful_moves"] for row in scenes
        ),
    }
    contact_scene_ids = [
        scene["id"] for scene in scenes
        if scene["arms"]["frozen"]["blocked"] > 0
    ]
    divergence_scene_ids = [
        scene["id"] for scene in scenes
        if any(scene["arms"][arm]["trace_sha256"] !=
               scene["arms"]["frozen"]["trace_sha256"]
               for arm in ("adaptive", "local_cell"))
    ]
    # Descriptive sample-coverage warning, not a hypothesis-test p-value.
    # Most empty-contact scenes cannot distinguish "learning" from a
    # hardcoded, equally successful innate policy.
    low_exposure = len(contact_scene_ids) * 2 < len(scenes)
    return {
        "schema": SCHEMA,
        "classification": "PUBLIC_SYNTHETIC_PREDECLARED_CASES_NOT_BLINDED",
        "evidence_quality": {
            "status": ("LOW_CONTACT_EXPOSURE" if low_exposure
                       else "DESCRIPTIVE_CONTACT_EXPOSURE_ONLY"),
            "minimum_exposure_fraction_heuristic": 0.5,
            "frozen_contact_scene_count": len(contact_scene_ids),
            "frozen_contact_scene_ids": contact_scene_ids,
            "behavior_divergence_scene_count": len(divergence_scene_ids),
            "behavior_divergence_scene_ids": divergence_scene_ids,
            "no_contact_frozen_scene_count": len(scenes) - len(contact_scene_ids),
            "no_generalization_claim": True,
        },
        "suite_sha256": _sha(SCENARIOS),
        "scene_count": len(scenes),
        "total_ticks_each_arm": sum(row["ticks"] for row in scenes),
        "totals": totals,
        "local_vs_frozen": local_vs_frozen,
        "scenes": scenes,
    }


def main():
    # Deliberately prints ONLY deterministic aggregate counts + fixture/trace
    # digests; no organism memory, hardware or user data is read or exposed.
    report = evaluate()
    print(_canonical(report))
    return report


if __name__ == "__main__":
    main()
