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
from .developmental_brain import DevelopmentalBrain, SCHEMA as DEVELOPMENTAL_BRAIN_SCHEMA
from .archived_policy_v2a import LocalCellExplorer as ArchivedV2AExplorer, SCHEMA as V2A_SCHEMA
from .cell_policy_v2 import CellExplorer, SCHEMA as V2B_SCHEMA
from .cell_policy_v3 import CellExplorerV3, SCHEMA as V3_SCHEMA
from .cell_policy_v4 import CellExplorerV4, SCHEMA as V4_SCHEMA
from .epoch_sense_v2 import (
    EPOCH_TICKS,
    EpochProprioception,
    MAX_ABSOLUTE_TICKS,
    SCHEMA as EPOCH_SENSE_SCHEMA,
)
from .local_cell_sense import Proprioception
from .graph import GraphError, load_graph
from .graph import validate as validate_graph
from .noosphere import (
    EMPTY_HEAD, EVENT_SCHEMA, LocalNoosphere, NoosphereError, _read_first_event,
)
from .sensory_bridge import ToyContactBridge, SCHEMA as TOY_CONTACT_SCHEMA, OCCLUSION_SCHEMA
from .sensory_play import ContactAssociations, ToyScene, SENSE_SCHEMA, MODEL_SCHEMA
from .sensory_associations_v2 import ContactAssociationsV2, SCHEMA as MODEL_V2_SCHEMA
from .sensory_bridge_v2 import (
    REPROBE_PERIOD_TICKS as REPROBE_PERIOD_V2,
    SCHEMA as TOY_CONTACT_V2_SCHEMA,
    ToyContactBridgeV2,
)
from .sim import Simulation, canonical, digest
from .temporal_outcomes import (
    ACTIONS, GUIDANCE_SCHEMA, SCHEMA as RANGE_SCHEMA, RangeCue, RangeOutcomeMemory,
)
from .world_room import (
    MAX_DISPLACEMENT_M, Room, advance, displacement_within_budget, initial_state,
)


SCHEMA = "mojum.aquarium.frame/1"
MANIFEST_SCHEMA = "mojum.aquarium.bootstrap/1"
MAX_REPLAY_EVENTS = 100_000
MAX_MOTOR_REPLAY_EVENTS = 10_000
REPLAY_BATCH_SIZE = 128
V2A_ARCHIVE_MODE = "local_cell_v2_a_archive"
V2B_MODE = "local_cell_v2b"
V2B_WIRE_MODE = "local_cell_v2"
V3_MODE = "local_cell_v3"
V4_MODE = "local_cell_v4"
EPOCH_EVENT_SCHEMA = "mojum.aquarium.epoch-transition/1"
MOTOR_MODES = (
    "scripted", "developmental_fixture", V2B_MODE, V3_MODE, V4_MODE,
    V2B_WIRE_MODE, V2A_ARCHIVE_MODE,
)
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
        sensory_mode="none", sensory_scene=None, temporal_mode="none", read_only=False,
        brain_mode="placeholder",
    ):
        if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
            raise AquariumError("invalid virtual dt_ns")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AquariumError("invalid agent ID")
        if neural_mode not in ("shadow", "disabled"):
            raise AquariumError("neural_mode must be shadow or disabled")
        if type(motor_mode) is not str or motor_mode not in MOTOR_MODES:
            raise AquariumError("unknown Aquarium motor mode")
        if brain_mode not in ("placeholder", "developmental_controller"):
            raise AquariumError("brain_mode must be placeholder or developmental_controller")
        if brain_mode == "developmental_controller" and motor_mode != "developmental_fixture":
            raise AquariumError("developmental_controller brain requires developmental_fixture motor mode")
        legacy_v2b = motor_mode == V2B_WIRE_MODE
        if legacy_v2b:
            motor_mode = V2B_MODE
        if type(read_only) is not bool:
            raise AquariumError("read_only must be boolean")
        self._archive_v2a = motor_mode == V2A_ARCHIVE_MODE
        self._epoch_v4 = motor_mode == V4_MODE
        self._read_only = self._archive_v2a or read_only
        self._local_cell = motor_mode in (V2A_ARCHIVE_MODE, V2B_MODE, V3_MODE, V4_MODE)
        if pulse_sum not in ("auto", "ordered", "compensated"):
            raise AquariumError("pulse_sum must be auto, ordered or compensated")
        if pulse_sum != "auto" and neural_mode == "disabled":
            raise AquariumError("explicit pulse summation requires shadow mode")
        if type(sensory_mode) is not str or sensory_mode not in ("none", "toy_probe", "toy_probe_v2"):
            raise AquariumError("sensory_mode must be none, toy_probe, or toy_probe_v2")
        if sensory_mode in ("toy_probe", "toy_probe_v2") and motor_mode != "developmental_fixture":
            raise AquariumError(f"{sensory_mode} requires developmental_fixture motor mode")
        if sensory_mode == "none" and sensory_scene is not None:
            raise AquariumError("sensory_scene requires toy_probe or toy_probe_v2 mode")
        if type(temporal_mode) is not str or temporal_mode not in ("none", "range_guided"):
            raise AquariumError("temporal_mode must be none or range_guided")
        if temporal_mode == "range_guided" and sensory_mode != "toy_probe":
            raise AquariumError("range_guided requires toy_probe sensory mode")
        self.temporal_mode = temporal_mode
        self.motor_mode = motor_mode
        self.sensory_mode = sensory_mode
        self.neural_mode = neural_mode
        self.brain_mode = brain_mode
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
        if self._local_cell and (
                type(self.room.width) is not int or self.room.width != 4
                or type(self.room.height) is not int or self.room.height != 4
                or canonical(self.room.obstacles) != "[[2,1,0.1,2]]"
                or self.start != (1.0, 2.0)):
            raise AquariumError("local-cell compatibility requires the exact built-in room and start")
        self.sensory_bridge = None
        if sensory_mode == "toy_probe":
            scene = ToyScene() if sensory_scene is None else sensory_scene
            self.sensory_bridge = ToyContactBridge(scene, motor_room=self.room)
        elif sensory_mode == "toy_probe_v2":
            scene = ToyScene() if sensory_scene is None else sensory_scene
            self.sensory_bridge = ToyContactBridgeV2(scene, motor_room=self.room)
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
        if self.brain_mode == "developmental_controller":
            self.manifest.update({
                "brain_mode": self.brain_mode,
                "brain_model_schema": DEVELOPMENTAL_BRAIN_SCHEMA,
                "source_classification": "synthetic_only_developmental_controller_no_neural_brain_no_mujoco",
            })
        # Do not modify the legacy scripted bootstrap for existing journals.
        if self._local_cell:
            self.manifest["exploration_mode"] = "goal_free"
            if self._archive_v2a:
                self.manifest["developmental_schema"] = V2A_SCHEMA
                self.manifest["motor_policy"] = "local_cell_v2"
                self.manifest["motor_policy_schema"] = V2A_SCHEMA
            else:
                if self._epoch_v4:
                    self.manifest["developmental_schema"] = V4_SCHEMA
                    self.manifest["developmental_policy"] = V4_MODE
                    self.manifest["epoch_transition_schema"] = EPOCH_EVENT_SCHEMA
                    self.manifest["epoch_ticks"] = EPOCH_TICKS
                    self.manifest["proprioception_schema"] = EPOCH_SENSE_SCHEMA
                else:
                    self.manifest["developmental_schema"] = V3_SCHEMA if motor_mode == V3_MODE else V2B_SCHEMA
                    self.manifest["developmental_policy"] = V3_MODE if motor_mode == V3_MODE else V2B_WIRE_MODE
        elif self.motor is not None:
            self.manifest["motor_mode"] = self.motor_mode
            self.manifest["motor_policy_schema"] = MOTOR_SCHEMA
        if self.sensory_bridge is not None:
            versioned = self.sensory_mode == "toy_probe_v2"
            self.manifest.update({
                "sensory_mode": sensory_mode,
                "sensory_schema": SENSE_SCHEMA,
                "sensory_model_schema": MODEL_V2_SCHEMA if versioned else MODEL_SCHEMA,
                "sensory_scene": self.sensory_bridge.scene.manifest(),
                "contact_bridge_schema": TOY_CONTACT_V2_SCHEMA if versioned else TOY_CONTACT_SCHEMA,
                "occlusion_schema": OCCLUSION_SCHEMA,
            })
            if versioned:
                self.manifest["sensory_reprobe_period_ticks"] = REPROBE_PERIOD_V2
        if self.range_model is not None:
            self.manifest.update({
                "temporal_mode": temporal_mode,
                "temporal_schema": RANGE_SCHEMA,
                "motor_guidance_schema": GUIDANCE_SCHEMA,
            })
        # An independently supplied anchor is only meaningful for history that
        # already exists. Validate it before any writable SQLite open so a
        # mistyped anchor cannot create a fresh PRIVATE journal as a side
        # effect of a failed reopen.
        if external_expected_head is not None and (
                type(external_expected_head) is not str
                or len(external_expected_head) != 64
                or any(c not in "0123456789abcdef" for c in external_expected_head)):
            raise AquariumError("expected head must be a lowercase SHA-256 digest")
        # Only advance this head when the corresponding state is in memory.
        self._committed_head = EMPTY_HEAD
        self._committed_count = 0
        first = _read_first_event(db_path, agent_id)
        if first is not None:
            self._check_bootstrap(first)
        elif external_expected_head is not None:
            raise AquariumError(
                "expected head requires an existing journal; refusing to create a new one")
        elif self._read_only or legacy_v2b:
            raise AquariumError("this motor selector requires an existing journal")
        self.store = LocalNoosphere(db_path, agent_id, read_only=self._read_only)
        try:
            if self.store.count() == 0:
                if self._read_only or legacy_v2b:
                    raise AquariumError("this motor selector requires an existing journal")
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
        self.motor = None
        if self.motor_mode == "developmental_fixture":
            self.motor = MotorPlay()
        elif self._archive_v2a:
            self.motor = ArchivedV2AExplorer()
        elif self._epoch_v4:
            self.motor = CellExplorerV4()
        elif self.motor_mode == V3_MODE:
            self.motor = CellExplorerV3()
        elif self._local_cell:
            self.motor = CellExplorer()
        self.brain = DevelopmentalBrain() if self.brain_mode == "developmental_controller" else None
        if self.sensory_bridge is None:
            self.contact_model = None
        elif self.sensory_mode == "toy_probe_v2":
            self.contact_model = ContactAssociationsV2()
        else:
            self.contact_model = ContactAssociations()
        self.range_model = RangeOutcomeMemory() if self.temporal_mode == "range_guided" else None
        self.genome = GenomeLedger(self.seed)
        try:
            self.world = initial_state(self.room, *self.start)
        except ValueError as exc:
            raise AquariumError("invalid room start coordinates") from exc
        self.tick = 0
        self.epoch_index = 0
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
        if self._read_only:
            raise AquariumError("Aquarium journal is read-only")
        allowed = {"aquarium.bootstrap", "aquarium.frame"}
        if self._epoch_v4:
            allowed.add("aquarium.epoch")
        if self._local_cell and topic not in allowed:
            raise AquariumError("local-cell compatibility journals contain only frames")
        # The committed count is cached after bootstrap/replay/CAS append so a
        # long history does not pay a global SQLite count scan per frame. A
        # concurrent writer is still rejected by the append's CAS head check.
        budget = self.journal_event_budget
        if self._committed_count >= budget:
            raise AquariumError(
                f"journal event budget reached ({budget:,}); "
                "export verified history and start a new journal")
        event = self._record(topic, event_id, payload, at_ns, source_revision)
        head = self.store.append(event, expected_head=self._committed_head)
        self._committed_count += 1
        return head

    @property
    def stored_events(self):
        """Number of events this process has verified or committed."""
        return self._committed_count

    @property
    def journal_event_budget(self):
        """Event ceiling for this journal and its pinned motor wire."""
        if self._epoch_v4:
            return MAX_REPLAY_EVENTS
        if self.motor is not None:
            return min(MAX_REPLAY_EVENTS, MAX_MOTOR_REPLAY_EVENTS)
        return MAX_REPLAY_EVENTS

    def required_events_for_steps(self, ticks):
        """Return the bounded journal admissions for a prospective run.

        Epoch v4 writes one explicit marker at each absolute-tick boundary;
        all older modes retain their one-frame-per-tick accounting.
        """
        if type(ticks) is not int or ticks < 0:
            raise AquariumError("steps must be a nonnegative integer")
        if not self._epoch_v4:
            return ticks
        end = self.tick + ticks
        if end > MAX_ABSOLUTE_TICKS:
            raise AquariumError("local_cell_v4 absolute-tick budget exhausted")
        markers = (max(0, (end - 1) // EPOCH_TICKS - self.epoch_index)
                   if ticks else 0)
        return ticks + markers

    def _epoch_event(self, index):
        """Build the canonical durable marker for one epoch boundary."""
        return self._record(
            "aquarium.epoch", f"epoch-{index:08d}",
            {
                "schema": EPOCH_EVENT_SCHEMA,
                "absolute_tick": self.tick,
                "epoch_index": index,
                "previous_epoch_index": index - 1,
                "epoch_tick": 0,
                "source": "explicit_bounded_epoch_rollover_without_reset",
            },
            self.tick * self.dt_ns,
            self.genome.revision,
        )

    def _commit_pending_epoch(self):
        if not self._epoch_v4:
            return
        expected_index = self.tick // EPOCH_TICKS
        if expected_index == self.epoch_index:
            return
        if (self.tick <= 0 or self.tick % EPOCH_TICKS != 0
                or expected_index != self.epoch_index + 1):
            raise AquariumError("noncanonical epoch boundary state")
        marker = self._epoch_event(expected_index)
        head = self._append_record(
            marker["topic"], marker["event_id"], marker["payload"],
            marker["observed_at_ns"], marker["source_revision"],
        )
        # Adopt only after the SQLite CAS succeeds.  If the process stops
        # after the append, replay sees this marker and reconstructs the same
        # half-step before the next frame.
        self.epoch_index = expected_index
        self._committed_head = head

    def _motor_sense(self, pulse):
        if self._epoch_v4:
            if self.epoch_index != self.tick // EPOCH_TICKS:
                raise AquariumError("missing committed epoch boundary")
            return EpochProprioception(
                self.tick, self.epoch_index, self.tick % EPOCH_TICKS,
                self.world.revision, self.world.x, self.world.y, pulse,
            )
        return Proprioception(
            self.tick, self.world.revision, self.world.x, self.world.y, pulse,
        )

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
        next_motor = (type(self.motor).from_snapshot(self.motor.snapshot())
                      if self.motor is not None else None)
        controller_mode = self.brain_mode == "developmental_controller"
        if next_motor is None:
            dx, dy = _movement(dx), _movement(dy)
        elif controller_mode:
            if dx is not None or dy is not None:
                raise AquariumError("developmental motor play rejects external dx/dy")
            if self.sensory_bridge is not None and pre_move_sensor is None:
                pre_move_sensor = self.sensory_bridge.sense(self.world)
        else:
            if dx is not None or dy is not None:
                raise AquariumError("developmental motor play rejects external dx/dy")
            if not self._local_cell:
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
        sensory = output[self.kind_ids["sensory.interoception"]]["sensory"]
        if self._local_cell:
            sense = self._motor_sense(sensory["heartbeat"]["pulse"])
            intent = next_motor.decide(sense)
            dx, dy = intent["dx"], intent["dy"]
        graph_brain = output[self.kind_ids["brain.placeholder"]]["observation"]
        if graph_brain.get("status") != "UNIMPLEMENTED" or graph_brain.get("action") is not None:
            raise AquariumError("unexpected privileged brain behavior in fixture")
        brain = graph_brain
        if controller_mode:
            brain = self.brain.decide(
                next_motor,
                tick=self.tick,
                interoception=sensory,
                proprioception={
                    "tick": self.world.tick,
                    "position": [self.world.x, self.world.y],
                    "world_revision": self.world.revision,
                },
                near_field=pre_move_sensor,
                dt_ns=self.dt_ns,
                avoid=avoid,
                prefer=prefer,
            )
            intent = brain["action"]
            dx, dy = intent["dx"], intent["dy"]
        new_world, world_obs = advance(
            self.room, self.world, tick=self.tick + 1, dx=dx, dy=dy,
            expected_revision=self.world.revision,
        )
        # Calculate against a copy. The predictor is committed only *after*
        # the SQLite append succeeds, and replay derives identical weights.
        next_learner = (PulsePredictor.from_snapshot(self.learner.snapshot(), summation=self._pulse_sum)
                        if self.learner is not None else None)
        learning = next_learner.observe(sensory) if next_learner is not None else None
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
        if self._local_cell:
            payload["developmental"] = {
                "schema": self.manifest["developmental_schema"],
                "observation": sense.public(),
                "intent": intent,
                "learning": next_motor.observe(sense, intent, world_obs["blocked"]),
                "policy_sha256": digest(next_motor.snapshot()),
                "motor_capability": "SYNTHETIC_POINT_ROOM_ONLY",
            }
        elif next_motor is not None:
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
        if self._read_only:
            raise AquariumError("Aquarium journal is read-only")
        if self.motor is None:
            dx = 0.02 if dx is None else dx
            dy = 0.0 if dy is None else dy
        if self._committed_count + self.required_events_for_steps(1) > self.journal_event_budget:
            raise AquariumError("requested step exceeds remaining journal event budget")
        self._commit_pending_epoch()
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

    def _check_bootstrap(self, first):
        expected = self._record("aquarium.bootstrap", "aquarium-bootstrap",
                                self.manifest, 0, self.graph_sha256)
        if first != expected or ((self.sensory_bridge is not None or self._local_cell
                                  or self.brain_mode == "developmental_controller") and
                                 canonical(first) != canonical(expected)):
            raise AquariumError("aquarium seed, room, graph, clock or mode mismatch")

    def _replay(self, external_expected_head, *, detect_sum):
        verified = self.store.verify_chain(
            external_expected_head=external_expected_head,
            strict_wire=(self._local_cell or self.brain_mode == "developmental_controller"),
        )
        count = verified["count"]
        budget = self.journal_event_budget
        if count > budget:
            raise AquariumError(
                f"journal exceeds the {budget:,}-event replay ceiling; "
                "export verified history and start a new journal")
        try:
            self._replay_stream(count, verified["head"])
        except _FrameMismatch:
            if not detect_sum:
                raise
            # A single arithmetic rule must reproduce the entire verified history.
            other_sum = "ordered" if self._pulse_sum == "compensated" else "compensated"
            self._reset_replay_state(other_sum)
            self._replay_stream(count, verified["head"])
        if self.store.head() != verified["head"]:
            raise AquariumError("journal advanced during replay, reopen the aquarium")
        self._committed_count = count
        self._committed_head = verified["head"]

    def _replay_stream(self, count, head):
        """Rebuild state from bounded batches; never materialize the history."""
        stream = self.store.iter_events(include_nonpublic=True,
                                        batch_size=REPLAY_BATCH_SIZE)
        try:
            first = next(stream, None)
            if first is None:
                raise AquariumError("bootstrap event missing")
            self._check_bootstrap(first["event"])
            seen = 1
            last_hash = first["event_hash"]
            for entry in stream:
                self._replay_event(entry)
                seen += 1
                last_hash = entry["event_hash"]
        finally:
            stream.close()
        if seen != count or last_hash != head:
            raise AquariumError("journal advanced during replay, reopen the aquarium")

    def _replay_event(self, entry):
        event = entry["event"]
        if event["visibility"] != "PRIVATE" or event["source"] != "mojum.aquarium.fixture":
            raise AquariumError("unexpected journal ownership or visibility")
        if event["observed_at_ns"] != self.tick * self.dt_ns:
            raise AquariumError("unsynchronized event time")
        allowed_local_topics = {"aquarium.frame"}
        if self._epoch_v4:
            allowed_local_topics.add("aquarium.epoch")
        if self._local_cell and event["topic"] not in allowed_local_topics:
            raise AquariumError("local-cell compatibility journals contain only frames")
        if event["topic"] == "aquarium.epoch":
            payload = event["payload"]
            expected_index = self.tick // EPOCH_TICKS
            if (not self._epoch_v4
                    or self.tick <= 0 or self.tick % EPOCH_TICKS != 0
                    or self.epoch_index + 1 != expected_index
                    or event != self._epoch_event(expected_index)):
                raise AquariumError("invalid epoch boundary or forged transition")
            self.epoch_index = expected_index
        elif event["topic"] == "development.advance":
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
            if self._epoch_v4 and self.epoch_index != self.tick // EPOCH_TICKS:
                raise AquariumError("replay frame missing epoch transition")
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
            matches = (canonical(event) == canonical(expected)
                       if (self._local_cell or next_contact is not None
                           or self.brain_mode == "developmental_controller")
                       else event == expected)
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
            "schema": ("mojum.aquarium.checkpoint/2"
                       if self._epoch_v4 else "mojum.aquarium.checkpoint/1"),
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
        if self._local_cell:
            state["developmental_state"] = self.motor.snapshot()
            if self._epoch_v4:
                state["epoch_index"] = self.epoch_index
                state["epoch_tick"] = self.tick % EPOCH_TICKS
        elif self.motor is not None:
            state["motor_mode"] = self.motor_mode
            state["motor_state"] = self.motor.snapshot()
        if self.contact_model is not None:
            state["sensory_mode"] = self.manifest["sensory_mode"]
            state["toy_contact_state"] = self.contact_model.snapshot()
        if self.range_model is not None:
            state["temporal_mode"] = self.temporal_mode
            state["temporal_range_state"] = self.range_model.snapshot()
        if self.brain_mode == "developmental_controller":
            state["brain_mode"] = self.brain_mode
            state["brain_schema"] = DEVELOPMENTAL_BRAIN_SCHEMA
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
    parser.add_argument("--brain-mode", choices=("placeholder", "developmental_controller"),
                        default="placeholder", help="opt-in synthetic controller, not a neural brain")
    parser.add_argument("--motor-mode", choices=MOTOR_MODES,
                        default="scripted")
    parser.add_argument("--sensory-mode", choices=("none", "toy_probe", "toy_probe_v2"), default="none",
                        help="opt-in synthetic contact learning with developmental motor play")
    parser.add_argument("--temporal-mode", choices=("none", "range_guided"), default="none",
                        help="guide virtual movement from measured range transitions")
    parser.add_argument("--pulse-sum", choices=("auto", "ordered", "compensated"), default="auto",
                        help="shadow predictor arithmetic for an untagged journal (default: detect on replay)")
    parser.add_argument("--expected-head", metavar="SHA256",
                        help="independent journal head to require before appending; "
                             "needs an existing journal")
    args = parser.parse_args()
    if args.expected_head is not None and (
            type(args.expected_head) is not str
            or len(args.expected_head) != 64
            or any(c not in "0123456789abcdef" for c in args.expected_head)):
        parser.error("--expected-head must be exactly 64 lowercase hexadecimal characters")
    if args.neural_mode == "disabled" and args.pulse_sum != "auto":
        parser.error("--pulse-sum requires shadow mode")
    if args.motor_mode == V2A_ARCHIVE_MODE and args.ticks != 0:
        parser.error("v2A archive requires --ticks 0")
    if args.brain_mode == "developmental_controller" and args.motor_mode != "developmental_fixture":
        parser.error("--brain-mode developmental_controller requires --motor-mode developmental_fixture")
    if args.motor_mode != "scripted" and (args.dx is not None or args.dy is not None):
        parser.error("developmental motor play does not accept --dx/--dy")
    if args.sensory_mode in ("toy_probe", "toy_probe_v2") and args.motor_mode != "developmental_fixture":
        parser.error(f"--sensory-mode {args.sensory_mode} requires --motor-mode developmental_fixture")
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
    try:
        aquarium = Aquarium(
            args.db, seed, graph, agent_id=seed["genome_id"],
            neural_mode=args.neural_mode, motor_mode=args.motor_mode, pulse_sum=args.pulse_sum,
            sensory_mode=args.sensory_mode, temporal_mode=args.temporal_mode,
            brain_mode=args.brain_mode,
            external_expected_head=args.expected_head,
        )
    except (AquariumError, NoosphereError) as exc:
        # A wrong independent anchor or a foreign journal is a command error,
        # not a traceback. Construction leaves no committed mutation behind.
        parser.error(str(exc))
    with aquarium:
        # Reject the whole CLI batch before any frame is committed. The
        # per-step gate remains authoritative for direct API callers.
        try:
            required = aquarium.required_events_for_steps(args.ticks)
        except AquariumError as exc:
            parser.error(str(exc))
        if aquarium.stored_events + required > aquarium.journal_event_budget:
            parser.error("requested ticks exceed remaining journal event budget")
        for _ in range(args.ticks):
            aquarium.step(dx=dx, dy=dy)
        # The checkpoint's final head check also covers the count read here.
        stored_events = aquarium.stored_events
        checkpoint = aquarium.checkpoint()
        checkpoint["stored_events"] = stored_events
        checkpoint["status"] = (
            "SYNTHETIC_DEVELOPMENTAL_CONTROLLER_NOT_NEURAL_OR_MUJOCO"
            if aquarium.brain_mode == "developmental_controller"
            else ("SYNTHETIC_SHADOW_LEARNING_NOT_FULL_BRAIN_OR_MUJOCO"
                  if aquarium.neural_mode == "shadow"
                  else "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO")
        )
        if aquarium.motor is not None:
            checkpoint["status"] += "_WITH_INTERNAL_SYNTHETIC_MOTOR_PLAY_NOT_REAL_ACTUATION"
        print(json.dumps(checkpoint, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
