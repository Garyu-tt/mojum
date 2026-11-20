"""Held-out synthetic observation QA for provisional object identity (R0.10).

The oracle labels and movable toy layout are used ONLY to score a run.
They are NEVER passed to ProvisionalObjectMemory. Uses no pretrained
weights, GPU, network, neural inference or production environment.
"""

from __future__ import annotations

import argparse
import json

from mojum.object_memory import ProvisionalObjectMemory
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import advance, initial_state


SCHEMA = "mojum.qa.object-continuity.heldout/1"
RED = (157, 64, 28)
TOY_A = Toy("oracle.a", 1.2, 2.0, .14, RED, .15)
TOY_B = Toy("oracle.b", 2.8, 2.0, .14, RED, .85)


def _sample(toys, xy, oracle, *, noise=None, dropout=False):
    """Only QA can see oracle labels. Sensor sees numeric properties."""
    return {"toys": toys, "xy": xy, "oracle": oracle,
            "noise": noise, "dropout": dropout}


def scenarios():
    """Fixed, disjoint synthetic cases, not a real-world held-out dataset."""
    same = (TOY_A, TOY_B)
    stationary = ([_sample(same, (1.0, 2.0), "a") for _ in range(4)]
                  + [_sample(same, (2.6, 2.0), "b") for _ in range(4)]
                  + [_sample(same, (1.0, 2.0), "a") for _ in range(4)])
    dropout = (
        [_sample((TOY_A,), (1.0, 2.0), "a") for _ in range(3)]
        + [_sample((TOY_A,), (1.0, 2.0), None, dropout=True) for _ in range(3)]
        + [_sample((TOY_A,), (1.0, 2.0), "a") for _ in range(3)]
    )
    # Deliberately unhandled: exact RGB hashing is not color-noise invariant.
    colors = [RED, (RED[0] + 1, RED[1], RED[2]),
              (RED[0], RED[1] + 1, RED[2])]
    jitter = [_sample((TOY_A,), (1.0, 2.0), "a", noise=colors[i % len(colors)])
              for i in range(12)]
    # The same toy actually moves while the observer follows it; this
    # exposes the stationary-landmark assumption without requiring a
    # trained planner or real motor dynamics.
    moving = []
    for i in range(17):
        x = 1.2 + .065 * i
        t = Toy("oracle.a", x, 2.0, .14, RED, .15)
        moving.append(_sample((t,), (x - .2, 2.0), "a"))
    # Crossing swaps the two hidden toy positions. Their RGB is identical.
    # The learner has no motion cues or identity tags, so it may conflate
    # their contact histories and report a plausible *wrong* hypothesis.
    crossed = [
        _sample(same, (1.0, 2.0), "a"),
        _sample(same, (2.6, 2.0), "b"),
        _sample((
            Toy("oracle.a", 2.8, 2.0, .14, RED, .15),
            Toy("oracle.b", 1.2, 2.0, .14, RED, .85),
        ), (1.0, 2.0), "b"),
        _sample((
            Toy("oracle.a", 2.8, 2.0, .14, RED, .15),
            Toy("oracle.b", 1.2, 2.0, .14, RED, .85),
        ), (2.6, 2.0), "a"),
    ]
    return {
        "static_same_color_separated": stationary,
        "temporary_sensor_dropout": dropout,
        "small_rgb_jitter": jitter,
        "moving_held_out_toy": moving,
        "crossing_identical_colors": crossed,
    }


def evaluate_case(samples, name):
    memory = ProvisionalObjectMemory()
    first_scene = ToyScene(toys=samples[0]["toys"])
    world = initial_state(first_scene.room, *samples[0]["xy"])
    # Mapping belongs to this external evaluator, *not* the tracker.
    truth_by_hypothesis = {}
    identified_truths = set()
    counter = {
        "sightings": 0, "matched_reencounters": 0,
        "identity_switches": 0, "splits": 0,
        "ambiguous_abstentions": 0, "no_signal": 0,
        "possible_reencounters": 0,
        "measured_contacts": 0, "contaminated_contacts": 0,
    }
    stream = []
    for i, sample in enumerate(samples):
        scene = ToyScene(toys=sample["toys"])
        if i:
            world, _ = advance(
                scene.room, world, tick=i,
                dx=sample["xy"][0] - world.x,
                dy=sample["xy"][1] - world.y,
                expected_revision=world.revision,
            )
        original = scene.sense(world)
        bearing = scene.directional_cue(world, original)
        sensor = dict(original)
        if sample["noise"] is not None:
            if not sensor["visible"]:
                raise AssertionError("noise must be applied to a visible cue")
            sensor["reflectance_rgb"] = list(sample["noise"])
        if sample["dropout"]:
            # Model a masked sensor sample, not a magically removed toy.
            sensor["visible"] = False
            sensor["reflectance_rgb"] = None
            sensor["range_bucket_10cm"] = None
            bearing["octant"] = None
        contact = (scene.probe(world, original)
                   if not sample["dropout"] and original["visible"] else None)
        # IMPORTANT: actual object IDs remain exclusively in the QA score
        # below. Never add sample["oracle"] or toys to model inputs.
        observation = memory.observe(sensor, bearing,
                                     [world.x, world.y], contact)
        if observation["identity_verified"]:
            raise AssertionError("an unverified fixture claimed object identity")
        status = observation["status"]
        candidate = observation["hypothesis"]
        oracle = sample["oracle"]
        if status == "ambiguous_identity":
            counter["ambiguous_abstentions"] += 1
        if status == "possible_reencounter":
            counter["possible_reencounters"] += 1
        if not sensor["visible"]:
            counter["no_signal"] += 1
        else:
            counter["sightings"] += 1
        if contact and contact["contact"]:
            counter["measured_contacts"] += 1
        if candidate is not None:
            if candidate in truth_by_hypothesis:
                if truth_by_hypothesis[candidate] == oracle:
                    counter["matched_reencounters"] += 1
                else:
                    counter["identity_switches"] += 1
                    if contact and contact["contact"]:
                        counter["contaminated_contacts"] += 1
            else:
                if oracle in identified_truths:
                    counter["splits"] += 1
                truth_by_hypothesis[candidate] = oracle
                identified_truths.add(oracle)
        # This stream is a QA artifact, NOT an agent-view event.
        stream.append({
            "tick": i, "oracle_only": oracle,
            "numeric_cue": sensor["reflectance_rgb"],
            "candidate": candidate, "status": status,
            "evidence_count": observation["sightings"],
            "identity_verified": observation["identity_verified"],
        })
    counter["hypotheses"] = len(memory.tracks)
    return {
        "name": name,
        "evaluated": len(samples),
        "counts": counter,
        "oracle_mapping_for_qa_only": truth_by_hypothesis,
        "timeline_for_qa_only": stream,
        "final_memory_snapshot": memory.snapshot(),
    }


def run_suite():
    result = {
        "schema": SCHEMA,
        "purpose": "heldout_synthetic_failure_characterization_not_object_recognition_claim",
        "oracle_scope": "external_qa_only_never_a_policy_input",
        "cases": {name: evaluate_case(samples, name)
                  for name, samples in scenarios().items()},
    }
    return result


def compact_report(result):
    """No raw toy positions or private trace in the CLI's default report."""
    return {
        "schema": result["schema"],
        "purpose": result["purpose"],
        "oracle_scope": result["oracle_scope"],
        "cases": {
            name: {"evaluated": case["evaluated"], **case["counts"]}
            for name, case in result["cases"].items()
        },
    }


def main():
    p = argparse.ArgumentParser(
        description="Deterministic synthetic held-out QA for R0.10 local hypotheses",
    )
    p.add_argument("--with-qa-traces", action="store_true",
                   help="include oracle-only timelines and all numeric hypothesis snapshots")
    args = p.parse_args()
    result = run_suite()
    print(json.dumps(result if args.with_qa_traces else compact_report(result),
                     ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
