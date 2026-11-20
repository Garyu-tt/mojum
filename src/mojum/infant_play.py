"""Goal-free infant motor babbling and explainable local observation trace.

Synthetic point-room ONLY. No neural cognition, consciousness, vision, joints,
MuJoCo, motor hardware, network service, or public private-memory export.
Each frame is committed into a PRIVATE per-agent SQLite Noosphere journal.
"""

import argparse
import copy
import json
import math
from pathlib import Path

from .graph import validate as validate_graph
from .noosphere import EVENT_SCHEMA, LocalNoosphere
from .sim import Simulation, digest
from .world_room import Room, advance, initial_state


SCHEMA = "mojum.infant-play.frame/1"
MODEL_SCHEMA = "mojum.infant-play.experience/1"
ACTIONS = ("rest", "x+", "x-", "y+", "y-")
SEED = 2027
MAX_EVENTS = 10_000
MAX_CELLS = 512


class InfantError(ValueError):
    """Invalid developmental fixture, journal, or attempted motor command."""


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (ValueError, OverflowError, TypeError):
        return False


def _cell(state):
    if not _finite(state.x) or not _finite(state.y):
        raise InfantError("invalid position")
    x, y = int(state.x * 2), int(state.y * 2)
    if min(x, y) < 0 or max(x, y) >= 10_000:
        raise InfantError("world beyond spatial memory budget")
    return f"{x}:{y}"


class MotorExperience:
    """Learn average consequences of actions in coarse 0.5m local cells.

    No goal, optimal route, reward function, pretrained weights, or
    anticipation of obstacle geometry. A predictable seeded exploration
    schedule is used strictly for deterministic journal replay.
    """

    def __init__(self, *, seed=SEED):
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise InfantError("invalid exploration seed")
        self.seed = seed
        self.rng = seed
        self.updates = 0
        self.cells = {}

    def snapshot(self):
        return {"schema": MODEL_SCHEMA, "seed": self.seed, "rng": self.rng,
                "updates": self.updates, "cells": copy.deepcopy(self.cells)}

    @classmethod
    def from_snapshot(cls, state):
        if (not isinstance(state, dict) or set(state) !=
                {"schema", "seed", "rng", "updates", "cells"}
                or state["schema"] != MODEL_SCHEMA):
            raise InfantError("incompatible motor memory")
        inst = cls(seed=state["seed"])
        if (type(state["rng"]) is not int or not 0 <= state["rng"] < 2**32
                or type(state["updates"]) is not int
                or not 0 <= state["updates"] <= MAX_EVENTS
                or not isinstance(state["cells"], dict)
                or len(state["cells"]) > MAX_CELLS):
            raise InfantError("invalid motor memory fields")
        counted = 0
        for key, record in state["cells"].items():
            if not isinstance(key, str) or len(key) > 12:
                raise InfantError("invalid cell ID")
            parts = key.split(":")
            if (len(parts) != 2 or any(not p.isdecimal() or str(int(p)) != p
                                        or int(p) >= 10000 for p in parts)):
                raise InfantError("invalid cell coordinates")
            if not isinstance(record, dict) or set(record) != set(ACTIONS):
                raise InfantError("incomplete action cell")
            for entry in record.values():
                if (not isinstance(entry, dict)
                        or set(entry) != {"visits", "mean_effect"}
                        or type(entry["visits"]) is not int
                        or not 0 <= entry["visits"] <= state["updates"]
                        or not _finite(entry["mean_effect"])
                        or not 0 <= entry["mean_effect"] <= 1
                        or (entry["visits"] == 0 and entry["mean_effect"] != 0)):
                    raise InfantError("invalid action statistics")
                counted += entry["visits"]
        if counted != state["updates"]:
            raise InfantError("inconsistent action observation count")
        inst.rng, inst.updates = state["rng"], state["updates"]
        inst.cells = copy.deepcopy(state["cells"])
        return inst

    def _stats(self, cell):
        if cell not in self.cells:
            if len(self.cells) >= MAX_CELLS:
                raise InfantError("spatial memory budget reached")
            self.cells[cell] = {
                action: {"visits": 0, "mean_effect": 0.0} for action in ACTIONS
            }
        return self.cells[cell]

    def propose(self, world, pulse):
        if type(pulse) is not bool:
            raise InfantError("heartbeat pulse must be boolean")
        cell = _cell(world)
        stats = self._stats(cell)
        least = min(a["visits"] for a in stats.values())
        candidates = [a for a in ACTIONS if stats[a]["visits"] == least]
        self.rng = (1664525 * self.rng + 1013904223) & 0xffffffff
        action = candidates[self.rng % len(candidates)]
        amount = 0.02 if pulse else 0.04
        dx = amount if action == "x+" else -amount if action == "x-" else 0.0
        dy = amount if action == "y+" else -amount if action == "y-" else 0.0
        return {
            "action": action, "cell": cell, "dx": dx, "dy": dy,
            "prior_visits": stats[action]["visits"],
            "predicted_effect": stats[action]["mean_effect"],
            "model_revision": self.updates,
            "motor_command": None,
            "capability": "SYNTHETIC_POINT_ROOM_ONLY",
        }

    def observe(self, before, after, obs, proposal):
        if (not isinstance(proposal, dict)
                or proposal.get("action") not in ACTIONS
                or proposal.get("cell") != _cell(before)
                or proposal.get("model_revision") != self.updates
                or proposal.get("motor_command") is not None
                or proposal.get("capability") != "SYNTHETIC_POINT_ROOM_ONLY"
                or not isinstance(obs, dict) or obs.get("tick") != before.tick + 1
                or after.tick != before.tick + 1
                or obs.get("revision") != after.revision
                or obs.get("position") != [after.x, after.y]
                or type(obs.get("blocked")) is not bool):
            raise InfantError("malformed synthetic motor feedback")
        dx, dy = proposal["dx"], proposal["dy"]
        if (not _finite(dx) or not _finite(dy)
                or abs(dx) + abs(dy) > 0.040000001
                or (dx, dy) not in {
                    (0.0, 0.0), (0.02, 0.0), (-0.02, 0.0),
                    (0.04, 0.0), (-0.04, 0.0), (0.0, 0.02),
                    (0.0, -0.02), (0.0, 0.04), (0.0, -0.04),
                }):
            raise InfantError("unexpected displacement proposal")
        expected = {"rest": (0.0, 0.0), "x+": (abs(dx), 0.0),
                    "x-": (-abs(dx), 0.0), "y+": (0.0, abs(dy)),
                    "y-": (0.0, -abs(dy))}
        if (dx, dy) != expected[proposal["action"]]:
            raise InfantError("action label does not match displacement")
        if obs["blocked"]:
            if (after.x, after.y) != (before.x, before.y):
                raise InfantError("blocked move changed position")
        elif (after.x, after.y) != (before.x + dx, before.y + dy):
            raise InfantError("mismatched world motion")
        wanted = abs(dx) + abs(dy)
        moved = abs(after.x - before.x) + abs(after.y - before.y)
        effect = min(1.0, moved / wanted) if wanted else 0.0
        entry = self._stats(proposal["cell"])[proposal["action"]]
        if (entry["visits"] != proposal["prior_visits"]
                or entry["mean_effect"] != proposal["predicted_effect"]):
            raise InfantError("stale motor prediction")
        error = effect - entry["mean_effect"]
        entry["visits"] += 1
        entry["mean_effect"] += error / entry["visits"]
        self.updates += 1
        return {
            "actual_effect": effect,
            "prediction_error": error,
            "learned_effect": entry["mean_effect"],
            "times_tried": entry["visits"],
            "updates": self.updates,
            "interpretation": "action_effect_observation_not_emotion",
        }


class InfantPlay:
    """One local organism, private committed experience and deterministic replay."""

    def __init__(self, db_path, graph, *, room=None, start=(1.0, 2.0),
                 agent_id="dante-fixture-001", seed=SEED, dt_ns=10_000_000):
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise InfantError("invalid organism ID")
        if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
            raise InfantError("invalid virtual clock")
        validate_graph(graph)
        kinds = {node["kind"]: node["id"] for node in graph["nodes"]}
        if "sensory.interoception" not in kinds:
            raise InfantError("interoception input absent")
        self.graph = copy.deepcopy(graph)
        self.graph_hash = digest(self.graph)
        self.sensory_id = kinds["sensory.interoception"]
        self.room = room if room is not None else Room(4, 4, ((2, 1, .1, 2),))
        if not isinstance(self.room, Room) or len(start) != 2:
            raise InfantError("invalid point-room")
        self.start = tuple(float(v) for v in start)
        self.world = initial_state(self.room, *self.start)
        self.model = MotorExperience(seed=seed)
        self.agent_id, self.dt_ns = agent_id, dt_ns
        self.tick = 0
        self.manifest = {
            "schema": SCHEMA, "agent_id": agent_id, "graph_sha256": self.graph_hash,
            "model_schema": MODEL_SCHEMA, "seed": seed, "dt_ns": dt_ns,
            "room": {"width": self.room.width, "height": self.room.height,
                     "obstacles": [list(x) for x in self.room.obstacles],
                     "start": list(self.start)},
            "goal": None, "provenance": "synthetic-fixture-no-real-world-observation",
        }
        self.store = LocalNoosphere(db_path, agent_id)
        try:
            if self.store.count() == 0:
                self._append("infant.bootstrap", "infant-bootstrap", self.manifest, 0)
            self._replay()
        except BaseException:
            self.store.close()
            raise

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _event(self, topic, eid, payload, now):
        return {
            "schema": EVENT_SCHEMA, "event_id": eid, "agent_id": self.agent_id,
            "topic": topic, "observed_at_ns": now, "recorded_at_ns": now,
            "valid_from_ns": now, "valid_to_ns": None, "visibility": "PRIVATE",
            "source": "mojum.infant.synthetic", "source_revision": SCHEMA,
            "payload": payload,
        }

    def _append(self, topic, eid, payload, now):
        if self.store.count() >= MAX_EVENTS:
            raise InfantError("infant journal budget reached")
        return self.store.append(
            self._event(topic, eid, payload, now), expected_head=self.store.head(),
        )

    def _calculate(self):
        runtime = Simulation(self.graph, self.dt_ns)
        runtime.tick = self.tick
        sensory = runtime.step()[self.sensory_id]["sensory"]
        if sensory.get("observed_at_ns") != self.tick * self.dt_ns:
            raise InfantError("non-monotone sensory tick")
        pulse = sensory["heartbeat"]["pulse"]
        model = MotorExperience.from_snapshot(self.model.snapshot())
        proposal = model.propose(self.world, pulse)
        next_world, world_obs = advance(
            self.room, self.world, tick=self.tick + 1,
            dx=proposal["dx"], dy=proposal["dy"],
            expected_revision=self.world.revision,
        )
        learning = model.observe(self.world, next_world, world_obs, proposal)
        viewer = {
            "schema": "mojum.observation.trace/1",
            "tick": self.tick, "attention": "synthetic_heartbeat_and_position",
            "perception": {
                "heartbeat_pulse": pulse, "position": [self.world.x, self.world.y],
                "world_revision": self.world.revision,
            },
            # Scene geometry is spectator metadata, NOT a sense the policy used.
            "spectator_scene": {
                "width": self.room.width, "height": self.room.height,
                "obstacles": [list(x) for x in self.room.obstacles],
                "available_to_agent": False,
            },
            "experiment": {
                "action": proposal["action"], "displacement": [proposal["dx"], proposal["dy"]],
                "novel_attempt": proposal["prior_visits"] == 0,
                "expected_effect": proposal["predicted_effect"],
            },
            "outcome": {
                "position": world_obs["position"], "blocked": world_obs["blocked"],
                "actual_effect": learning["actual_effect"],
            },
            "memory": {
                "cell": proposal["cell"], "action": proposal["action"],
                "times_tried": learning["times_tried"],
                "predicted_effect_after": learning["learned_effect"],
                "prediction_error": learning["prediction_error"],
                "total_updates": learning["updates"],
            },
            "assessment": {
                "metric": "local_movement_effectiveness_not_personality_score",
                "score_0_to_5": (
                    round(5 * learning["actual_effect"], 3)
                    if proposal["action"] != "rest" else None
                ),
                "basis": "fraction_of_requested_synthetic_displacement",
                "grade_of": "single_action_result_not_the_organism",
            },
            "inference": {
                "kind": "local_empirical_action_effect",
                "text": (
                    "this_action_did_not_move_me" if world_obs["blocked"]
                    else "rest_produces_no_translation" if proposal["action"] == "rest"
                    else "this_action_changed_my_position"
                ),
                "evidence_count": learning["times_tried"],
                "not": "thought_or_feeling_or_general_reasoning",
            },
            "source": "DETERMINISTIC_SYNTHETIC_TRACE_NOT_CONSCIOUS_EXPERIENCE",
        }
        payload = {
            "schema": SCHEMA, "tick": self.tick,
            "time_ns": self.tick * self.dt_ns,
            "interoception": sensory, "world": world_obs,
            "experiment": proposal, "experience": learning, "viewer": viewer,
            "physical_model": "SYNTHETIC_POINT_ROOM_ONLY",
            "brain_status": "NOT_A_NEURAL_BRAIN",
        }
        return payload, next_world, model

    def step(self):
        payload, next_world, next_model = self._calculate()
        self._append(
            "infant.frame", f"infant-frame-{self.tick:012d}", payload,
            self.tick * self.dt_ns,
        )
        self.world, self.model = next_world, next_model
        self.tick += 1
        return copy.deepcopy(payload)

    def _replay(self):
        self.store.verify_chain()
        count = self.store.count()
        if count > MAX_EVENTS:
            raise InfantError("journal exceeds bounded replay capacity")
        events = self.store.dataset(include_nonpublic=True, limit=MAX_EVENTS)["projection"]["items"]
        if len(events) != count or not events:
            raise InfantError("incomplete infant memory")
        if events[0]["event"] != self._event(
            "infant.bootstrap", "infant-bootstrap", self.manifest, 0,
        ):
            raise InfantError("infant bootstrap does not match this organism")
        for item in events[1:]:
            event = item["event"]
            if event["topic"] != "infant.frame":
                raise InfantError("unexpected infant memory event")
            payload, next_world, next_model = self._calculate()
            expected = self._event(
                "infant.frame", f"infant-frame-{self.tick:012d}",
                payload, self.tick * self.dt_ns,
            )
            if event != expected:
                raise InfantError("infant experience replay mismatch")
            self.world, self.model = next_world, next_model
            self.tick += 1

    def checkpoint(self):
        return {
            "schema": "mojum.infant.checkpoint/1", "tick": self.tick,
            "agent_id": self.agent_id, "world_revision": self.world.revision,
            "motor_memory": self.model.snapshot(), "journal_head": self.store.head(),
            "manifest_sha256": digest(self.manifest),
            "trust": "checksum_only_not_authenticated",
        }


def main():
    parser = argparse.ArgumentParser(
        description="Mojum goal-free infant-play fixture with optional local observer trace",
    )
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    parser.add_argument("--ticks", type=int, default=40)
    parser.add_argument("--watch", action="store_true",
                        help="opt-in PRIVATE synthetic trace on this local terminal")
    args = parser.parse_args()
    if not 0 <= args.ticks <= 1000:
        parser.error("ticks must be within 0..1000")
    with InfantPlay(
        args.db, json.loads(args.graph.read_text(encoding="utf-8")),
    ) as infant:
        for _ in range(args.ticks):
            frame = infant.step()
            if args.watch:
                print(json.dumps(frame["viewer"], ensure_ascii=False, allow_nan=False))
        print(json.dumps(
            {"checkpoint": infant.checkpoint(), "events": infant.store.count()},
            ensure_ascii=False, allow_nan=False,
        ))


if __name__ == "__main__":
    main()
