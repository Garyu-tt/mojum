"""Mojum R0.2 offline-first *synthetic* aquarium, using reviewed component fixtures.

GENESIS -> Universal Graph IR pulse/placeholder brain -> point-room -> SQLite.
Not a neural model, MuJoCo physical simulation, encrypted memory or atomic
multi-component transaction. SQLite is the only committed source of truth.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import unicodedata
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .genesis import GenomeLedger, GenesisError, sha256 as genome_sha256, validate_seed
from .body_genetics import (compile_body, compile_body_at_month,
                            validate_human_name)
from .neural import NATIVE_PULSE_SUM, PulsePredictor
from .developmental import MotorPlay, SCHEMA as MOTOR_SCHEMA
from .graph import GraphError, load_graph
from .graph import validate as validate_graph
from .noosphere import EMPTY_HEAD, EVENT_SCHEMA, LocalNoosphere, NoosphereError
from .sensory_bridge import ToyContactBridge, SCHEMA as TOY_CONTACT_SCHEMA, OCCLUSION_SCHEMA
from .sensory_play import ContactAssociations, ToyScene, SENSE_SCHEMA, MODEL_SCHEMA
from .sim import Simulation, canonical, digest
from .temporal_outcomes import (
    ACTIONS, GUIDANCE_SCHEMA, SCHEMA as RANGE_SCHEMA, RangeCue, RangeOutcomeMemory,
)
from .world_room import (
    MAX_DISPLACEMENT_M, Room, advance, displacement_within_budget, initial_state,
)


SCHEMA = "mojum.aquarium.frame/1"
MANIFEST_SCHEMA = "mojum.aquarium.bootstrap/1"
MAX_REPLAY_EVENTS = 10_000
KINDS = (
    "genesis.seed", "body.heartbeat", "sensory.interoception",
    "world.room", "brain.placeholder", "memory.episode",
)


class AquariumError(ValueError):
    """Nonrecoverable mismatch, invalid command or unsupported fixture."""


class _FrameMismatch(AquariumError):
    """A recorded frame differs from the result of deterministic replay."""


DEVELOPMENT_SCHEMA = "mojum.agent.development-event/1"
IDENTITY_EVENT_SCHEMA = "mojum.agent.identity-event/1"


def _safe_event_text(value, field, limit=256):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AquariumError(f"{field} must be nonempty text (<= {limit} chars)")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in value):
        raise AquariumError(f"{field} contains control/invisible characters")
    return value


def _start_coordinate(value):
    """Validate an initial world position, not a per-step displacement."""
    if type(value) not in (int, float):
        raise AquariumError("start must be a numeric scalar")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise AquariumError("start coordinate exceeds numeric range") from exc
    if not math.isfinite(numeric):
        raise AquariumError("start coordinate must be finite")
    return numeric


def _movement(value):
    if type(value) not in (int, float):
        raise AquariumError("movement must be a numeric scalar, not bool or string")
    try:
        if not math.isfinite(value) or abs(value) > MAX_DISPLACEMENT_M:
            raise AquariumError(f"movement is nonfinite or outside {MAX_DISPLACEMENT_M:g}m budget")
        return float(value)
    except OverflowError as exc:
        raise AquariumError("movement exceeds numeric range") from exc


def _room_manifest(room: Room, start):
    return {
        "width": float(room.width), "height": float(room.height),
        "obstacles": [list(rect) for rect in room.obstacles],
        "start": list(start),
    }


class Aquarium:
    """One organism, one local event journal and one logical simulation clock.

    Reopening replays and revalidates all events. A crash after a SQLite append
    but before updating in-memory state is reconstructed from the committed log.
    A crash before SQLite commit does not mutate the in-memory logical state.
    """

    def __init__(
        self, db_path, seed, graph, *, agent_id="dante-fixture-001",
        room=None, start=(1.0, 2.0), dt_ns=10_000_000,
        external_expected_head=None, neural_mode="shadow", motor_mode="scripted", pulse_sum="auto",
        sensory_mode="none", sensory_scene=None, temporal_mode="none",
    ):
        if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
            raise AquariumError("invalid virtual dt_ns")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AquariumError("invalid agent ID")
        if neural_mode not in ("shadow", "disabled"):
            raise AquariumError("neural_mode must be shadow or disabled")
        if motor_mode not in ("scripted", "developmental_fixture"):
            raise AquariumError("motor_mode must be scripted or developmental_fixture")
        if pulse_sum not in ("auto", "ordered", "compensated"):
            raise AquariumError("pulse_sum must be auto, ordered or compensated")
        if pulse_sum != "auto" and neural_mode == "disabled":
            raise AquariumError("explicit pulse summation requires shadow mode")
        if type(sensory_mode) is not str or sensory_mode not in ("none", "toy_probe"):
            raise AquariumError("sensory_mode must be none or toy_probe")
        if sensory_mode == "toy_probe" and motor_mode != "developmental_fixture":
            raise AquariumError("toy_probe requires developmental_fixture motor mode")
        if sensory_mode == "none" and sensory_scene is not None:
            raise AquariumError("sensory_scene requires toy_probe mode")
        if type(temporal_mode) is not str or temporal_mode not in ("none", "range_guided"):
            raise AquariumError("temporal_mode must be none or range_guided")
        if temporal_mode == "range_guided" and sensory_mode != "toy_probe":
            raise AquariumError("range_guided requires toy_probe sensory mode")
        self.temporal_mode = temporal_mode
        self.motor_mode = motor_mode
        self.neural_mode = neural_mode
        self.dt_ns = dt_ns
        self.agent_id = agent_id
        self.seed = validate_seed(seed)
        if self.seed["genome_id"] != agent_id:
            raise AquariumError("genome_id must match local agent_id")
        self.graph = copy.deepcopy(graph)
        validate_graph(self.graph)
        kinds = [node["kind"] for node in self.graph["nodes"]]
        if sorted(kinds) != sorted(KINDS):
            raise AquariumError("aquarium requires exactly six known fixture graph kinds")
        self.kind_ids = {node["kind"]: node["id"] for node in self.graph["nodes"]}
        self.graph_sha256 = digest(self.graph)
        self.room = room if room is not None else Room(4, 4, ((2, 1, .1, 2),))
        if not isinstance(self.room, Room) or type(start) not in (tuple, list) or len(start) != 2:
            raise AquariumError("invalid room or start")
        try:
            self.start = (_start_coordinate(start[0]), _start_coordinate(start[1]))
        except AquariumError as exc:
            raise AquariumError("invalid room start coordinates") from exc
        self.sensory_bridge = None
        if sensory_mode == "toy_probe":
            scene = ToyScene() if sensory_scene is None else sensory_scene
            self.sensory_bridge = ToyContactBridge(scene, motor_room=self.room)
        self._reset_replay_state(NATIVE_PULSE_SUM if pulse_sum == "auto" else pulse_sum)
        self.manifest = {
            "schema": MANIFEST_SCHEMA, "agent_id": agent_id,
            "dt_ns": dt_ns, "genotype_sha256": genome_sha256(self.seed),
            "graph_sha256": self.graph_sha256,
            "neural_mode": self.neural_mode,
            "neural_model_schema": "mojum.neural.delayed-pulse/1" if self.learner is not None else None,
            "room": _room_manifest(self.room, self.start),
            "source_classification": "synthetic_only_brain_unimplemented_no_mujoco",
        }
        # Do not modify the legacy scripted bootstrap for existing journals.
        if self.motor is not None:
            self.manifest["motor_mode"] = self.motor_mode
            self.manifest["motor_policy_schema"] = MOTOR_SCHEMA
        if self.sensory_bridge is not None:
            self.manifest.update({
                "sensory_mode": sensory_mode,
                "sensory_schema": SENSE_SCHEMA,
                "sensory_model_schema": MODEL_SCHEMA,
                "sensory_scene": self.sensory_bridge.scene.manifest(),
                "contact_bridge_schema": TOY_CONTACT_SCHEMA,
                "occlusion_schema": OCCLUSION_SCHEMA,
            })
        if self.range_model is not None:
            self.manifest.update({
                "temporal_mode": temporal_mode,
                "temporal_schema": RANGE_SCHEMA,
                "motor_guidance_schema": GUIDANCE_SCHEMA,
            })
        # Only advance this head when the corresponding state is in memory.
        self._committed_head = EMPTY_HEAD
        self.store = LocalNoosphere(db_path, agent_id)
        try:
            if self.store.count() == 0:
                self._append_record("aquarium.bootstrap", "aquarium-bootstrap", self.manifest, 0, self.graph_sha256)
            self._replay(external_expected_head, detect_sum=pulse_sum == "auto" and self.learner is not None)
        except BaseException:
            self.store.close()
            raise

    def close(self):
        self.store.close()

    def _reset_replay_state(self, pulse_sum):
        self._pulse_sum = pulse_sum
        self.learner = PulsePredictor(summation=pulse_sum) if self.neural_mode == "shadow" else None
        self.motor = MotorPlay() if self.motor_mode == "developmental_fixture" else None
        self.contact_model = ContactAssociations() if self.sensory_bridge is not None else None
        self.range_model = RangeOutcomeMemory() if self.temporal_mode == "range_guided" else None
        self.genome = GenomeLedger(self.seed)
        try:
            self.world = initial_state(self.room, *self.start)
        except ValueError as exc:
            raise AquariumError("invalid room start coordinates") from exc
        self.tick = 0
        self.development_months = 72  # separate, non-senescent synthetic developmental calendar
        self.identity = None  # human-declared, never inferred from genes

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _record(self, topic, event_id, payload, at_ns, source_revision):
        return {
            "schema": EVENT_SCHEMA, "event_id": event_id, "agent_id": self.agent_id,
            "topic": topic, "observed_at_ns": at_ns, "recorded_at_ns": at_ns,
            "valid_from_ns": at_ns, "valid_to_ns": None, "visibility": "PRIVATE",
            "source": "mojum.aquarium.fixture", "source_revision": source_revision,
            "payload": payload,
        }

    def _append_record(self, topic, event_id, payload, at_ns, source_revision):
        if self.store.count() >= MAX_REPLAY_EVENTS:
            raise AquariumError("R0.2 journal event budget reached; streaming replay pending")
        event = self._record(topic, event_id, payload, at_ns, source_revision)
        return self.store.append(event, expected_head=self._committed_head)

    def _calculate(self, dx, dy):
        """Preserve the existing three-value scripted fixture test contract."""
        payload, world, learner, *_ = self._calculate_frame(dx, dy)
        return payload, world, learner

    def _calculate_frame(self, dx, dy):
        pre_move_sensor = None
        avoid = prefer = ()
        if self.range_model is not None:
            pre_move_sensor = self.sensory_bridge.sense(self.world)
            before = RangeCue(self.tick, self.world.revision, pre_move_sensor["range_bucket_10cm"])
            predictions = {action: self.range_model.predict(before, action) for action in ACTIONS}
            avoid = tuple(action for action, result in predictions.items()
                          if result["blocked"] is True)
            prefer = tuple(action for action, result in predictions.items()
                           if action != "rest" and result["blocked"] is False
                           and result["next_bucket"] != before.range_bucket_10cm)
        next_motor = (MotorPlay.from_snapshot(self.motor.snapshot())
                      if self.motor is not None else None)
        if next_motor is None:
            dx, dy = _movement(dx), _movement(dy)
        else:
            if dx is not None or dy is not None:
                raise AquariumError("developmental motor play rejects external dx/dy")
            intent = next_motor.choose(self.tick, avoid=avoid, prefer=prefer)
            dx, dy = intent["dx"], intent["dy"]
        pheno = self.genome.phenotype(self.tick)
        bpm = pheno["parameters"]["heartbeat_bpm"]
        adapted = copy.deepcopy(self.graph)
        # Explicit data binding at the GENESIS port of the typed graph. The
        # R0.1 executor has a static seed fixture; rebuild its config per tick.
        for node in adapted["nodes"]:
            if node["kind"] == "genesis.seed":
                node["config"]["bpm"] = bpm
        runtime = Simulation(adapted, dt_ns=self.dt_ns)
        runtime.tick = self.tick
        output = runtime.step()
        new_world, world_obs = advance(
            self.room, self.world, tick=self.tick + 1, dx=dx, dy=dy,
            expected_revision=self.world.revision,
        )
        brain = output[self.kind_ids["brain.placeholder"]]["observation"]
        sensory = output[self.kind_ids["sensory.interoception"]]["sensory"]
        # Calculate against a copy. The predictor is committed only *after*
        # the SQLite append succeeds, and replay derives identical weights.
        next_learner = (PulsePredictor.from_snapshot(self.learner.snapshot(), summation=self._pulse_sum)
                        if self.learner is not None else None)
        learning = next_learner.observe(sensory) if next_learner is not None else None
        if brain.get("status") != "UNIMPLEMENTED" or brain.get("action") is not None:
            raise AquariumError("unexpected privileged brain behavior in fixture")
        payload = {
            "schema": SCHEMA, "tick": self.tick, "time_ns": self.tick * self.dt_ns,
            "genome_revision": self.genome.revision,
            "phenotype": pheno,
            "graph_execution_sha256": runtime.graph_hash,
            "interoception": sensory,
            "brain": brain, "learning_observer": learning, "world": world_obs,
            "movement_request": {"dx": dx, "dy": dy},
            "physical_model": "NONE_FIXTURE_ONLY",
        }
        if next_motor is not None:
            outcome = next_motor.observe(
                tick=self.tick, kind=intent["kind"], blocked=world_obs["blocked"],
            )
            payload["developmental_controller"] = {
                "schema": MOTOR_SCHEMA,
                "status": "SYNTHETIC_GOAL_FREE_TABULAR_MOTOR_PLAY",
                "intent": intent, "outcome": outcome,
                "action_source": "internal_committed_prior_outcomes_only",
                "real_actuation": False,
            }
        next_contact = None
        if self.sensory_bridge is not None:
            payload["toy_contact"], next_contact = self.sensory_bridge.evaluate(
                self.world, self.contact_model, cue=pre_move_sensor,
            )
        next_range = None
        if self.range_model is not None:
            post_move_sensor = self.sensory_bridge.sense(new_world)
            after = RangeCue(new_world.tick, new_world.revision, post_move_sensor["range_bucket_10cm"])
            action = intent["kind"]
            next_range = RangeOutcomeMemory.from_snapshot(self.range_model.snapshot())
            measured = next_range.observe(before, action, after, world_obs["blocked"], predictions[action])
            payload["range_guidance"] = {
                "schema": GUIDANCE_SCHEMA,
                "before": asdict(before),
                "after": asdict(after),
                "predictions": predictions,
                "avoid": list(avoid),
                "prefer": list(prefer),
                "measured": measured,
                "state_sha256": digest(next_range.snapshot()),
            }
        return payload, new_world, next_learner, next_motor, next_contact, next_range

    def body_preview(self, somatic_chromosome, age_years=None):
        """Project genetic body dimensions without modifying journal or learning.

        Without an age override, use journal-persisted developmental months.
        Whole-year what-if previews remain available and never change state.
        """
        phenotype = self.genome.phenotype(self.tick)
        if age_years is None:
            return compile_body_at_month(phenotype, somatic_chromosome,
                                         self.development_months)
        return compile_body(phenotype, somatic_chromosome, age_years)

    def advance_development(self, months=1, *, reason="explicit simulation advancement"):
        """Persist a synthetic age milestone independently of physics frames."""
        if type(months) is not int or not 1 <= months <= 12:
            raise AquariumError("development advance must be 1..12 whole months")
        reason = _safe_event_text(reason, "development reason")
        target = self.development_months + months
        if target > 192:
            raise AquariumError("development maximum age is 16 years")
        payload = {
            "schema": DEVELOPMENT_SCHEMA,
            "from_months": self.development_months,
            "to_months": target,
            "reason": reason,
        }
        event_id = f"development-{self.development_months:03d}-{target:03d}"
        head = self._append_record("development.advance", event_id, payload,
                            self.tick * self.dt_ns, self.genome.revision)
        self.development_months = target
        self._committed_head = head
        return target

    def assign_human_name(self, identity, *, actor_id):
        """Append one human-*declared* name; actor identity is NOT authenticated.

        The future authenticated front end must verify authorization; caller-
        supplied actor_id alone does not prove the naming actor is human.
        """
        if self.identity is not None:
            raise AquariumError("agent already named; renaming is not implemented")
        actor_id = _safe_event_text(actor_id, "human actor id", 128)
        validated = validate_human_name(identity, self.agent_id)
        payload = {
            "schema": IDENTITY_EVENT_SCHEMA,
            "identity": validated,
            "actor_id": actor_id,
        }
        head = self._append_record("identity.assigned", "identity-assigned", payload,
                            self.tick * self.dt_ns, self.genome.revision)
        self.identity = validated
        self._committed_head = head
        return copy.deepcopy(self.identity)

    def step(self, *, dx=None, dy=None):
        if self.motor is None:
            dx = 0.02 if dx is None else dx
            dy = 0.0 if dy is None else dy
        frame = self._calculate_frame(dx, dy)
        payload, next_world, next_learner, next_motor, next_contact, next_range = frame
        head = self._append_record("aquarium.frame", f"frame-{self.tick:012d}", payload,
                                   self.tick * self.dt_ns, self.genome.revision)
        self.world = next_world
        self.learner = next_learner
        self.motor = next_motor
        self.contact_model = next_contact
        self.range_model = next_range
        self.tick += 1
        self._committed_head = head
        return copy.deepcopy(payload)

    def acquire(self, acquired):
        """Persist an acquired-gene event. Only future/current ticks allowed."""
        if not isinstance(acquired, dict):
            raise AquariumError("acquired change must be an event object")
        if type(acquired.get("valid_from_tick")) is not int:
            raise AquariumError("valid_from_tick must be an integer")
        if acquired.get("valid_from_tick", -1) < self.tick:
            raise AquariumError("cannot retroactively change committed frames")
        if acquired.get("recorded_at_ns") != self.tick * self.dt_ns:
            raise AquariumError("recorded_at_ns must equal current virtual tick time")
        proposed = GenomeLedger.from_snapshot(self.genome.snapshot())
        proposed.append(acquired, proposed.revision)
        event_id = acquired["event_id"]
        head = self._append_record("genesis.acquisition", "acq-" + event_id,
                                   {"acquisition": acquired}, self.tick * self.dt_ns,
                                   proposed.revision)
        self.genome = proposed
        self._committed_head = head
        return self.genome.revision

    def _replay(self, external_expected_head, *, detect_sum):
        verified = self.store.verify_chain(external_expected_head=external_expected_head)
        count = verified["count"]
        if count > MAX_REPLAY_EVENTS:
            raise AquariumError("R0.2 replay exceeds 10,000 journal events; streaming pending")
        view = self.store.dataset(include_nonpublic=True, limit=MAX_REPLAY_EVENTS)
        items = view["projection"]["items"]
        if not items:
            raise AquariumError("bootstrap event missing")
        if len(items) != count or items[-1]["event_hash"] != verified["head"]:
            raise AquariumError("journal changed since verification, reopen the aquarium")
        first = items[0]["event"]
        expected = self._record("aquarium.bootstrap", "aquarium-bootstrap",
                                self.manifest, 0, self.graph_sha256)
        if first != expected or (self.sensory_bridge is not None and
                                 canonical(first) != canonical(expected)):
            raise AquariumError("aquarium seed, room, graph, clock or mode mismatch")
        try:
            self._replay_events(items)
        except _FrameMismatch:
            if not detect_sum:
                raise
            # A single arithmetic rule must reproduce the entire verified history.
            other_sum = "ordered" if self._pulse_sum == "compensated" else "compensated"
            self._reset_replay_state(other_sum)
            self._replay_events(items)
        if self.store.head() != verified["head"]:
            raise AquariumError("journal advanced during replay, reopen the aquarium")
        self._committed_head = verified["head"]

    def _replay_events(self, items):
        for entry in items[1:]:
            event = entry["event"]
            if event["visibility"] != "PRIVATE" or event["source"] != "mojum.aquarium.fixture":
                raise AquariumError("unexpected journal ownership or visibility")
            if event["observed_at_ns"] != self.tick * self.dt_ns:
                raise AquariumError("unsynchronized event time")
            if event["topic"] == "development.advance":
                payload = event["payload"]
                if not isinstance(payload, dict) or set(payload) != {
                    "schema", "from_months", "to_months", "reason"
                } or payload["schema"] != DEVELOPMENT_SCHEMA:
                    raise AquariumError("invalid development journal event")
                previous, target = payload["from_months"], payload["to_months"]
                if (type(previous) is not int or previous != self.development_months or
                        type(target) is not int or not 1 <= target - previous <= 12 or
                        target > 192):
                    raise AquariumError("development clock continuity broken")
                _safe_event_text(payload["reason"], "development reason")
                event_id = f"development-{previous:03d}-{target:03d}"
                if event != self._record("development.advance", event_id,
                                         payload, self.tick * self.dt_ns,
                                         self.genome.revision):
                    raise AquariumError("noncanonical development event")
                self.development_months = target
            elif event["topic"] == "identity.assigned":
                payload = event["payload"]
                if (self.identity is not None or not isinstance(payload, dict) or
                        set(payload) != {"schema", "identity", "actor_id"} or
                        payload["schema"] != IDENTITY_EVENT_SCHEMA):
                    raise AquariumError("invalid or duplicate identity journal event")
                _safe_event_text(payload["actor_id"], "human actor id", 128)
                validated = validate_human_name(payload["identity"], self.agent_id)
                if event != self._record("identity.assigned", "identity-assigned",
                                         payload, self.tick * self.dt_ns,
                                         self.genome.revision):
                    raise AquariumError("noncanonical identity event")
                self.identity = validated
            elif event["topic"] == "genesis.acquisition":
                raw = event["payload"].get("acquisition")
                if not isinstance(raw, dict) or event["event_id"] != "acq-" + str(raw.get("event_id")):
                    raise AquariumError("invalid acquisition envelope")
                if raw.get("valid_from_tick", -1) < self.tick:
                    raise AquariumError("retroactive committed acquisition")
                if raw.get("recorded_at_ns") != event["recorded_at_ns"]:
                    raise AquariumError("acquisition and journal recorded time differ")
                next_ledger = GenomeLedger.from_snapshot(self.genome.snapshot())
                next_ledger.append(raw, next_ledger.revision)
                if next_ledger.revision != event["source_revision"]:
                    raise AquariumError("acquired gene revision mismatch")
                if event != self._record("genesis.acquisition", event["event_id"],
                                         event["payload"], self.tick * self.dt_ns,
                                         next_ledger.revision):
                    raise AquariumError("noncanonical acquired event envelope")
                self.genome = next_ledger
            elif event["topic"] == "aquarium.frame":
                if event["event_id"] != f"frame-{self.tick:012d}":
                    raise AquariumError("frame identifier discontinuity")
                move = event["payload"].get("movement_request")
                if not isinstance(move, dict) or set(move) != {"dx", "dy"}:
                    raise AquariumError("invalid replayed movement")
                # Re-derive internal intent from the previously committed evidence.
                args = (move["dx"], move["dy"]) if self.motor is None else (None, None)
                frame = self._calculate_frame(*args)
                payload, next_world, next_learner, next_motor, next_contact, next_range = frame
                expected = self._record("aquarium.frame", event["event_id"], payload,
                                        self.tick * self.dt_ns, self.genome.revision)
                # Compare JSON types for the opt-in wire. Python structural
                # equality treats bools and numbers as interchangeable.
                matches = (event == expected if next_contact is None
                           else canonical(event) == canonical(expected))
                if not matches:
                    raise _FrameMismatch("non-deterministic or mismatched committed frame")
                self.world = next_world
                self.learner = next_learner
                self.motor = next_motor
                self.contact_model = next_contact
                self.range_model = next_range
                self.tick += 1
            else:
                raise AquariumError("unexpected aquarium journal topic")

    def checkpoint(self):
        state = {
            "schema": "mojum.aquarium.checkpoint/1",
            "agent_id": self.agent_id, "tick": self.tick,
            "sim_time_ns": self.tick * self.dt_ns,
            "genome_revision": self.genome.revision,
            "world_revision": self.world.revision,
            "journal_head": self._committed_head,
            "neural_mode": self.neural_mode,
            "neural_state": self.learner.snapshot() if self.learner is not None else None,
            "manifest_sha256": digest(self.manifest),
            "development_months": self.development_months,
            "development_years_floor": self.development_months // 12,
            "display_name": (self.identity["display_name"]
                             if self.identity is not None else None),
            "name_provenance": ("human_declared_unverified"
                                if self.identity is not None else "not_assigned"),
            "trust": "checksum_only_not_authenticated",
        }
        if self.motor is not None:
            state["motor_mode"] = self.motor_mode
            state["motor_state"] = self.motor.snapshot()
        if self.contact_model is not None:
            state["sensory_mode"] = self.manifest["sensory_mode"]
            state["toy_contact_state"] = self.contact_model.snapshot()
        if self.range_model is not None:
            state["temporal_mode"] = self.temporal_mode
            state["temporal_range_state"] = self.range_model.snapshot()
        if self.store.head() != state["journal_head"]:
            raise AquariumError("stale aquarium checkpoint, reopen the journal")
        return state


def main():
    parser = argparse.ArgumentParser(description="Mojum R0.2 CPU-only fixture aquarium")
    packaged = files("mojum").joinpath("data")
    parser.add_argument("--db", type=Path, required=True, help="explicit local SQLite file; not cloud")
    parser.add_argument("--genome", type=Path, default=packaged.joinpath("genesis_seed.json"),
                        help="genotype JSON providing the organism identity")
    parser.add_argument("--graph", type=Path, default=packaged.joinpath("first_loop.json"))
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--dx", type=float)
    parser.add_argument("--dy", type=float)
    parser.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
    parser.add_argument("--motor-mode", choices=("scripted", "developmental_fixture"),
                        default="scripted")
    parser.add_argument("--sensory-mode", choices=("none", "toy_probe"), default="none",
                        help="opt-in synthetic contact learning with developmental motor play")
    parser.add_argument("--temporal-mode", choices=("none", "range_guided"), default="none",
                        help="guide virtual movement from measured range transitions")
    parser.add_argument("--pulse-sum", choices=("auto", "ordered", "compensated"), default="auto",
                        help="shadow predictor arithmetic for an untagged journal (default: detect on replay)")
    args = parser.parse_args()
    if args.neural_mode == "disabled" and args.pulse_sum != "auto":
        parser.error("--pulse-sum requires shadow mode")
    if args.motor_mode != "scripted" and (args.dx is not None or args.dy is not None):
        parser.error("developmental motor play does not accept --dx/--dy")
    if args.sensory_mode == "toy_probe" and args.motor_mode != "developmental_fixture":
        parser.error("--sensory-mode toy_probe requires --motor-mode developmental_fixture")
    if args.temporal_mode == "range_guided" and args.sensory_mode != "toy_probe":
        parser.error("--temporal-mode range_guided requires --sensory-mode toy_probe")
    if not 0 <= args.ticks <= 1000:
        parser.error("--ticks must be within 0..1000")
    # Validate command-line movement before Aquarium can create a bootstrap
    # journal. Invalid requests must not leave behind a new organism fixture.
    if args.motor_mode == "scripted":
        try:
            dx = _movement(0.02 if args.dx is None else args.dx)
            dy = _movement(0.0 if args.dy is None else args.dy)
        except AquariumError as exc:
            parser.error(str(exc))
        if not displacement_within_budget(dx, dy):
            parser.error(f"movement length must be finite and at most {MAX_DISPLACEMENT_M:g} m")
    else:
        # The internal controller selects each action from committed history.
        dx, dy = None, None
    seed = validate_seed(json.loads(args.genome.read_text(encoding="utf-8")))
    try:
        graph = load_graph(args.graph)
        validate_graph(graph)  # Fail before opening a new SQLite journal.
    except GraphError as exc:
        parser.error(str(exc))
    with Aquarium(
        args.db, seed, graph, agent_id=seed["genome_id"],
        neural_mode=args.neural_mode, motor_mode=args.motor_mode, pulse_sum=args.pulse_sum,
        sensory_mode=args.sensory_mode, temporal_mode=args.temporal_mode,
    ) as aquarium:
        # Reject the whole CLI batch before any frame is committed. The
        # per-step gate remains authoritative for direct API callers.
        if aquarium.store.count() + args.ticks > MAX_REPLAY_EVENTS:
            parser.error("requested ticks exceed remaining journal event budget")
        for _ in range(args.ticks):
            aquarium.step(dx=dx, dy=dy)
        # The checkpoint's final head check also covers the count read here.
        stored_events = aquarium.store.count()
        checkpoint = aquarium.checkpoint()
        checkpoint["stored_events"] = stored_events
        checkpoint["status"] = (
            "SYNTHETIC_SHADOW_LEARNING_NOT_FULL_BRAIN_OR_MUJOCO"
            if aquarium.neural_mode == "shadow"
            else "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO"
        )
        if aquarium.motor is not None:
            checkpoint["status"] += "_WITH_INTERNAL_SYNTHETIC_MOTOR_PLAY_NOT_REAL_ACTUATION"
        print(json.dumps(checkpoint, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
