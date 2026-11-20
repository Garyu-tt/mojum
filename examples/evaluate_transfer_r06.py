"""Held-out-room evaluation of Mojum's *synthetic* point-room policies.

No neural generalization claim is implied by this benchmark. A policy that
only moves toward a fixed goal cannot solve paths requiring a temporary
detour away from it. All outcomes are deterministic, local and CPU-only.
"""

import argparse
import copy
import json
from pathlib import Path
from statistics import mean, median
import tempfile

from mojum.aquarium import Aquarium
from mojum.learned_policy import TabularAxisPolicy, gate_fixture_proposal
from mojum.reactive_controller import decide
from mojum.sim import Simulation
from mojum.world_room import Room, advance, initial_state


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
START = (1.0, 2.0)
GOAL = (3.5, 3.5)
EVAL_SEEDS = (1979, 1981, 1983, 1985)
MODES = ("reactive", "scratch_online", "transfer_frozen", "transfer_online")
TRAIN_ROOM = Room(4, 4, ((2, 1, .1, 2),))
ROOMS = {
    "open_floor": (Room(4, 4), "held_out_reachable"),
    "shifted_barrier": (Room(4, 4, ((1.75, 1.35, .15, 1.35),)), "held_out_reachable"),
    "upper_barrier": (Room(4, 4, ((2.45, 1.2, .15, 2.1),)), "held_out_reachable"),
    "detour_required": (Room(4, 4, ((2, 1, .1, 2.75),)), "held_out_reachable_requires_away_from_goal"),
    "sealed_barrier": (Room(4, 4, ((2, 0, .1, 4),)), "negative_control_no_path"),
}
SENSORY_ID = next(n["id"] for n in GRAPH["nodes"]
                  if n["kind"] == "sensory.interoception")


def _distance(state):
    return round(abs(GOAL[0] - state.x) + abs(GOAL[1] - state.y), 9)


def _pretrain(ticks, db_path):
    """Acquire committed training experience only in the original room."""
    with Aquarium(db_path, SEED, GRAPH, room=TRAIN_ROOM,
                  control_mode="learned_fixture", goal=GOAL) as aquarium:
        for _ in range(ticks):
            aquarium.step()
        state = aquarium.policy.snapshot()
        return state, aquarium.policy.updates


def _episode(room, mode, seed, trained_snapshot, ticks):
    """New episode/world per evaluation; no holdout events enter pretraining."""
    if mode not in MODES or type(seed) is not int or not 0 <= seed < 2 ** 32:
        raise ValueError("invalid evaluation mode or RNG seed")
    state = initial_state(room, *START)
    runtime = Simulation(GRAPH)
    feedback = None
    model = None
    initial_updates = None
    if mode != "reactive":
        snapshot = (copy.deepcopy(trained_snapshot) if mode.startswith("transfer_")
                    else TabularAxisPolicy().snapshot())
        snapshot["rng_state"] = seed  # matched rollout PRNG across model variants
        model = TabularAxisPolicy.from_snapshot(snapshot)
        initial_updates = model.updates
        frozen_values = copy.deepcopy(model.snapshot()["table"])
    blocked = 0
    goal_tick = None
    for tick in range(1, ticks + 1):
        sensor = runtime.step()[SENSORY_ID]["sensory"]
        if mode == "reactive":
            proposal = decide(room, state, sensor, GOAL, feedback)
            dx, dy = proposal["dx"], proposal["dy"]
        else:
            proposal = model.propose(room, state, sensor, GOAL)
            dx, dy = gate_fixture_proposal(room, state, sensor, GOAL, proposal)
        next_state, world = advance(
            room, state, tick=tick, dx=dx, dy=dy,
            expected_revision=state.revision,
        )
        if model is not None and mode != "transfer_frozen":
            model.learn(room, state, next_state, world, GOAL, proposal)
        blocked += int(world["blocked"])
        feedback = {"dx": dx, "dy": dy, "blocked": world["blocked"]}
        state = next_state
        if goal_tick is None and _distance(state) == 0.0:
            goal_tick = tick
    if mode == "transfer_frozen" and model.snapshot()["table"] != frozen_values:
        raise AssertionError("frozen evaluation changed learned Q values")
    return {
        "seed": seed,
        "first_goal_tick": goal_tick,
        "blocked_steps": blocked,
        "final_goal_distance_m": _distance(state),
        "evaluation_updates": model.updates - initial_updates if model else 0,
    }


def _aggregate(runs, room_label):
    successful = [r["first_goal_tick"] for r in runs if r["first_goal_tick"] is not None]
    return {
        "room": room_label,
        "episodes": len(runs),
        "goals_reached": len(successful),
        "median_first_goal_tick_on_success": median(successful) if successful else None,
        "mean_blocked_steps": round(mean(r["blocked_steps"] for r in runs), 3),
        "mean_final_goal_distance_m": round(mean(r["final_goal_distance_m"] for r in runs), 9),
        "mean_new_policy_updates": round(mean(r["evaluation_updates"] for r in runs), 3),
        "per_seed": runs,
    }


def evaluate(*, ticks=240, train_ticks=160, seeds=3):
    if type(ticks) is not int or not 1 <= ticks <= 600:
        raise ValueError("ticks outside 1..600")
    if type(train_ticks) is not int or not 1 <= train_ticks <= 600:
        raise ValueError("train_ticks outside 1..600")
    if type(seeds) is not int or not 1 <= seeds <= len(EVAL_SEEDS):
        raise ValueError("seeds outside 1..4")
    # All room geometries, goals and seed order are pinned in source code.
    # The held-out suite must remain disjoint from the training geometry.
    if any(room.obstacles == TRAIN_ROOM.obstacles for room, _ in ROOMS.values()):
        raise AssertionError("holdout room leaked the training-room geometry")
    with tempfile.TemporaryDirectory(prefix="mojum-transfer-r06-") as tmp:
        snapshot, trained_updates = _pretrain(train_ticks, Path(tmp) / "training.sqlite3")
    cases = []
    for room_name, (room, classification) in ROOMS.items():
        policies = {}
        for mode in MODES:
            runs = [_episode(room, mode, seed, snapshot, ticks)
                    for seed in EVAL_SEEDS[:seeds]]
            policies[mode] = _aggregate(runs, room_name)
        cases.append({
            "room": room_name,
            "class": classification,
            "obstacles": [list(r) for r in room.obstacles],
            "policies": policies,
        })
    return {
        "schema": "mojum.evaluation.heldout-room/1",
        "status": "CONTROLLED_SYNTHETIC_FIXTURE_NOT_GENERAL_INTELLIGENCE",
        "train_room_obstacles": [list(r) for r in TRAIN_ROOM.obstacles],
        "train_ticks": train_ticks,
        "train_updates": trained_updates,
        "evaluation_seeds": list(EVAL_SEEDS[:seeds]),
        "evaluation_ticks": ticks,
        "movement_constraints": "X_or_Y_toward_pinned_goal_only",
        "transfer_frozen": "same_Q_values_no_reward_updates_holdout",
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description="Mojum CPU-only held-out room transfer QA")
    parser.add_argument("--ticks", type=int, default=240)
    parser.add_argument("--train-ticks", type=int, default=160)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    result = evaluate(ticks=args.ticks, train_ticks=args.train_ticks, seeds=args.seeds)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
