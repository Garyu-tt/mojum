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
from pathlib import Path

from .genesis import GenomeLedger, GenesisError, sha256 as genome_sha256, validate_seed
from .neural import PulsePredictor
from .developmental_policy import Explorer, Proprioception, SCHEMA as DEVELOPMENT_SCHEMA
from .sensory_play import ContactAssociations, ToyScene, SENSE_SCHEMA, MODEL_SCHEMA
from .sensory_bridge import (ToyContactBridge, validate_scene,
                             SCHEMA as TOY_CONTACT_SCHEMA,
                             OCCLUSION_SCHEMA)
from .graph import validate as validate_graph
from .noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError
from .sim import Simulation, digest
from .world_room import Room, RoomState, _revision, _valid, advance, initial_state
from .verified_snapshot import (
    STATE_SCHEMA, SnapshotError, load_snapshot, save_snapshot,
    runtime_source_sha256,
)


SCHEMA = "mojum.aquarium.frame/1"
MANIFEST_SCHEMA = "mojum.aquarium.bootstrap/1"
MAX_REPLAY_EVENTS = 100_000  # Still bounded; full replay is CPU-linear.
_OMITTED_MOVEMENT = object()  # Distinguish omitted dx/dy from explicit None.
KINDS = (
    "genesis.seed", "body.heartbeat", "sensory.interoception",
    "world.room", "brain.placeholder", "memory.episode",
)


class AquariumError(ValueError):
    """Nonrecoverable mismatch, invalid command or unsupported fixture."""


def _movement(value):
    if type(value) not in (int, float):
        raise AquariumError("movement must be a numeric scalar, not bool or string")
    try:
        if not math.isfinite(value) or abs(value) > 1000:
            raise AquariumError("movement is nonfinite or outside 1000m budget")
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
        external_expected_head=None, neural_mode="shadow",
        exploration_mode="scripted", sensory_mode="none", sensory_scene=None,
        replay_snapshot_file=None, replay_snapshot_key_file=None,
    ):
        if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
            raise AquariumError("invalid virtual dt_ns")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AquariumError("invalid agent ID")
        if neural_mode not in ("shadow", "disabled"):
            raise AquariumError("neural_mode must be shadow or disabled")
        if exploration_mode not in ("scripted", "goal_free"):
            raise AquariumError("exploration_mode must be scripted or goal_free")
        if type(sensory_mode) is not str or sensory_mode not in ("none", "toy_probe"):
            raise AquariumError("sensory_mode must be none or toy_probe")
        if sensory_mode == "toy_probe" and exploration_mode != "goal_free":
            raise AquariumError("toy_probe requires goal_free exploration")
        if sensory_mode == "none" and sensory_scene is not None:
            raise AquariumError("unused synthetic sensory scene")
        if (replay_snapshot_file is None) != (replay_snapshot_key_file is None):
            raise AquariumError("replay snapshot file and key file must be supplied together")
        self._snapshot_file = replay_snapshot_file
        self._snapshot_key_file = replay_snapshot_key_file
        signed_state = None
        if replay_snapshot_file is not None:
            paths = [Path(db_path).resolve(), Path(replay_snapshot_file).resolve(),
                     Path(replay_snapshot_key_file).resolve()]
            if len(set(paths)) != 3:
                raise AquariumError("snapshot, key and SQLite must be separate files")
            # Verify the optional sidecar before any SQLite bootstrap/write.
            signed_state = load_snapshot(replay_snapshot_file, replay_snapshot_key_file)
            if signed_state is not None and not Path(db_path).is_file():
                raise AquariumError("signed replay snapshot exists but SQLite source is missing")
        self.exploration_mode = exploration_mode
        self.sensory_mode = sensory_mode
        self.neural_mode = neural_mode
        self.learner = PulsePredictor() if neural_mode == "shadow" else None
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
        if not isinstance(self.room, Room) or len(start) != 2:
            raise AquariumError("invalid room or start")
        self.start = (float(start[0]), float(start[1]))
        self.manifest = {
            "schema": MANIFEST_SCHEMA, "agent_id": agent_id,
            "dt_ns": dt_ns, "genotype_sha256": genome_sha256(self.seed),
            "graph_sha256": self.graph_sha256,
            "neural_mode": self.neural_mode,
            "neural_model_schema": "mojum.neural.delayed-pulse/1" if self.learner is not None else None,
            "room": _room_manifest(self.room, self.start),
            "source_classification": "synthetic_only_brain_unimplemented_no_mujoco",
        }
        self.genome = GenomeLedger(self.seed)
        self.world = initial_state(self.room, *self.start)
        self.explorer = Explorer() if exploration_mode == "goal_free" else None
        self.sensory_bridge = None
        if sensory_mode == "toy_probe":
            scene = ToyScene() if sensory_scene is None else sensory_scene
            self.sensory_bridge = ToyContactBridge(
                validate_scene(self.room, scene), motor_room=self.room,
            )
        self.tick = 0
        if self.explorer is not None:
            self.manifest["developmental_schema"] = DEVELOPMENT_SCHEMA
            self.manifest["exploration_mode"] = "goal_free"
        if self.sensory_bridge is not None:
            self.manifest["sensory_mode"] = "toy_probe"
            self.manifest["sensory_schema"] = SENSE_SCHEMA
            self.manifest["sensory_model_schema"] = MODEL_SCHEMA
            self.manifest["sensory_scene"] = self.sensory_bridge.scene.manifest()
            self.manifest["contact_bridge_schema"] = TOY_CONTACT_SCHEMA
            # Pin visibility semantics in the bootstrap. Existing pre-mask
            # private journals must fail closed rather than silently replay
            # historical sensory frames under a different physical model.
            self.manifest["occlusion_schema"] = OCCLUSION_SCHEMA
        self.store = LocalNoosphere(db_path, agent_id)
        # Local RAM is authoritative only through its last fully adopted commit.
        # A read of the shared SQLite head alone is not an organism checkpoint.
        self._committed_head = None
        self._committed_event_count = self.store.count()
        try:
            if self._committed_event_count == 0:
                self._append_record("aquarium.bootstrap", "aquarium-bootstrap", self.manifest, 0, self.graph_sha256)
            self._replay(external_expected_head, signed_state=signed_state)
        except BaseException:
            self.store.close()
            raise

    def close(self):
        self.store.close()

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
        if self._committed_event_count >= MAX_REPLAY_EVENTS:
            raise AquariumError("journal event budget reached; verified snapshot/compaction pending")
        event = self._record(topic, event_id, payload, at_ns, source_revision)
        expected = (self.store.head() if self._committed_head is None
                    else self._committed_head)
        committed = self.store.append(event, expected_head=expected)
        self._committed_event_count += 1
        return committed

    def _calculate(self, dx, dy):
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
        brain = output[self.kind_ids["brain.placeholder"]]["observation"]
        sensory = output[self.kind_ids["sensory.interoception"]]["sensory"]
        next_explorer = None
        intent = signal = None
        if self.explorer is not None:
            if dx is not None or dy is not None:
                raise AquariumError("goal-free mode rejects externally scripted movement")
            # Only pre-action proprioception and own pulse reach this policy.
            # Obstacle geometry, hidden objects and future frames stay private.
            signal = Proprioception(
                self.tick, self.world.revision, self.world.x, self.world.y,
                sensory["heartbeat"]["pulse"],
            )
            next_explorer = Explorer.from_snapshot(self.explorer.snapshot())
            intent = next_explorer.decide(signal)
            dx, dy = intent["dx"], intent["dy"]
        else:
            # Scripted mode must reject explicit None as before R0.13.
            dx = _movement(dx)
            dy = _movement(dy)
        new_world, world_obs = advance(
            self.room, self.world, tick=self.tick + 1, dx=dx, dy=dy,
            expected_revision=self.world.revision,
        )
        # Calculate against a copy. The predictor is committed only *after*
        # the SQLite append succeeds, and replay derives identical weights.
        next_learner = (PulsePredictor.from_snapshot(self.learner.snapshot())
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
        if next_explorer is not None:
            measured = next_explorer.observe(signal, intent, world_obs["blocked"])
            payload["developmental"] = {
                "schema": DEVELOPMENT_SCHEMA,
                "observation": signal.public(),
                "intent": intent,
                "learning": measured,
                "policy_sha256": digest(next_explorer.snapshot()),
                "motor_capability": "SYNTHETIC_POINT_ROOM_ONLY",
            }
        if self.sensory_bridge is not None:
            sensory_frame, _ = self.sensory_bridge.evaluate(self.world)
            payload["toy_contact"] = sensory_frame
        return payload, new_world, next_learner

    def _adopt_developmental(self, payload):
        if self.explorer is None:
            return
        # Compute against a copy, validate the persisted causal event and
        # adopt only after the SQLite CAS append or successful replay.
        cue = Proprioception(
            self.tick, self.world.revision, self.world.x, self.world.y,
            payload["interoception"]["heartbeat"]["pulse"],
        )
        clone = Explorer.from_snapshot(self.explorer.snapshot())
        claimed = payload["developmental"]
        if (claimed["observation"] != cue.public()
                or claimed["intent"] != clone.decide(cue)
                or claimed["motor_capability"] != "SYNTHETIC_POINT_ROOM_ONLY"):
            raise AquariumError("developmental observation/intent mismatch")
        measured = clone.observe(cue, claimed["intent"], payload["world"]["blocked"])
        if (measured != claimed["learning"]
                or digest(clone.snapshot()) != claimed["policy_sha256"]):
            raise AquariumError("developmental experience replay mismatch")
        self.explorer = clone

    def _adopt_sensory(self, payload):
        if self.sensory_bridge is not None:
            self.sensory_bridge.adopt(self.world, payload["toy_contact"])

    def step(self, *, dx=_OMITTED_MOVEMENT, dy=_OMITTED_MOVEMENT):
        if self.explorer is not None:
            # Explicit None is an external command, not an omission.
            if dx is not _OMITTED_MOVEMENT or dy is not _OMITTED_MOVEMENT:
                raise AquariumError("goal-free mode rejects externally scripted movement")
            arguments = (None, None)
        else:
            arguments = (0.02 if dx is _OMITTED_MOVEMENT else dx,
                         0.0 if dy is _OMITTED_MOVEMENT else dy)
        payload, next_world, next_learner = self._calculate(*arguments)
        committed = self._append_record("aquarium.frame", f"frame-{self.tick:012d}", payload,
                                        self.tick * self.dt_ns, self.genome.revision)
        self._adopt_sensory(payload)
        self._adopt_developmental(payload)
        self.world = next_world
        self.learner = next_learner
        self.tick += 1
        # A successful append is not a local checkpoint until RAM adoption.
        self._committed_head = committed
        return copy.deepcopy(payload)

    def acquire(self, acquired):
        """Persist an acquired-gene event. Only future/current ticks allowed."""
        if not isinstance(acquired, dict):
            raise AquariumError("acquired change must be an event object")
        if acquired.get("valid_from_tick", -1) < self.tick:
            raise AquariumError("cannot retroactively change committed frames")
        if acquired.get("recorded_at_ns") != self.tick * self.dt_ns:
            raise AquariumError("recorded_at_ns must equal current virtual tick time")
        proposed = GenomeLedger.from_snapshot(self.genome.snapshot())
        proposed.append(acquired, proposed.revision)
        event_id = acquired["event_id"]
        committed = self._append_record("genesis.acquisition", "acq-" + event_id,
                                        {"acquisition": acquired}, self.tick * self.dt_ns,
                                        proposed.revision)
        self.genome = proposed
        self._committed_head = committed
        return self.genome.revision

    def _local_checkpoint(self):
        """Derive exact checkpoint fields without reading current SQLite head.

        This permits validating a signed *earlier* snapshot before replaying
        the newer tail, without pretending its state is the current head.
        """
        result = {
            "schema": "mojum.aquarium.checkpoint/1",
            "agent_id": self.agent_id, "tick": self.tick,
            "sim_time_ns": self.tick * self.dt_ns,
            "genome_revision": self.genome.revision,
            "world_revision": self.world.revision,
            "journal_head": self._committed_head,
            "neural_mode": self.neural_mode,
            "neural_state": self.learner.snapshot() if self.learner is not None else None,
            "manifest_sha256": digest(self.manifest),
            "trust": "checksum_only_not_authenticated",
        }
        if self.explorer is not None:
            result["developmental_state"] = self.explorer.snapshot()
        if self.sensory_bridge is not None:
            result["toy_contact_state"] = self.sensory_bridge.model.snapshot()
        return result

    def _adopt_signed_replay_state(self, state, *, committed_count):
        """Strictly validate HMAC-protected cache and pinned SQLite prefix."""
        required = {
            "schema", "agent_id", "manifest_sha256", "runtime_source_sha256",
            "journal_events", "journal_head", "tick", "world", "genome", "learner",
            "explorer", "toy_contact_state", "checkpoint",
        }
        if type(state) is not dict or set(state) != required or state["schema"] != STATE_SCHEMA:
            raise SnapshotError("invalid replay state field contract")
        events, tick = state["journal_events"], state["tick"]
        if (type(events) is not int or not 1 <= events <= committed_count
                or type(tick) is not int or not 0 <= tick < MAX_REPLAY_EVENTS):
            raise SnapshotError("invalid signed event count or tick")
        if (state["agent_id"] != self.agent_id
                or state["manifest_sha256"] != digest(self.manifest)):
            raise SnapshotError("snapshot belongs to a different agent/room/mode/genome")
        if state["runtime_source_sha256"] != runtime_source_sha256():
            raise SnapshotError("runtime source changed: full replay required; "
                                "move signed cache aside explicitly")
        if self.store.event_at(events)["event_hash"] != state["journal_head"]:
            raise SnapshotError("signed snapshot head is absent from SQLite prefix")
        world = state["world"]
        if (type(world) is not dict or set(world) !=
                {"tick", "x", "y", "revision"}
                or type(world["tick"]) is not int or world["tick"] != tick
                or any(type(world[v]) not in (int, float) for v in ("x", "y"))
                or not _valid(self.room, world["x"], world["y"])
                or world["revision"] != _revision(
                    self.room, tick, world["x"], world["y"])):
            raise SnapshotError("signed point-room state is invalid")
        ledger = GenomeLedger.from_snapshot(state["genome"])
        if ledger.snapshot()["seed"] != self.seed:
            raise SnapshotError("snapshot GENESIS seed differs from current organism")
        if self.learner is None:
            if state["learner"] is not None:
                raise SnapshotError("unexpected saved shadow neural learner")
            learner = None
        else:
            learner = PulsePredictor.from_snapshot(state["learner"])
            expected_last_ns = (tick - 1) * self.dt_ns if tick else None
            if learner.snapshot()["last_observed_at_ns"] != expected_last_ns:
                raise SnapshotError("shadow learner time differs from saved tick")
        if self.explorer is None:
            if state["explorer"] is not None:
                raise SnapshotError("unexpected developmental state")
            explorer = None
        else:
            explorer = Explorer.from_snapshot(state["explorer"])
            if explorer.updates != tick:
                raise SnapshotError("developmental state tick count mismatch")
        if self.sensory_bridge is None:
            if state["toy_contact_state"] is not None:
                raise SnapshotError("unexpected sensory model state")
            contact = None
        else:
            contact = ContactAssociations.from_snapshot(state["toy_contact_state"])
            if contact.updates > tick:
                raise SnapshotError("sensory model has more measurements than ticks")

        self.tick = tick
        self.world = RoomState(tick, float(world["x"]), float(world["y"]),
                               world["revision"])
        self.genome, self.learner, self.explorer = ledger, learner, explorer
        if self.sensory_bridge is not None:
            self.sensory_bridge.model = contact
        self._committed_head = state["journal_head"]
        self._committed_event_count = events
        # Validate that all components form the SAME pinned checkpoint.
        if state["checkpoint"] != self._local_checkpoint():
            raise SnapshotError("signed component snapshots disagree on checkpoint")

    def save_replay_snapshot(self):
        """Publish one *optional* HMAC PRIVATE sidecar of committed RAM state.

        Snapshot is a speed cache, not the journal. At save time a full SQLite
        hash-chain verification pins the current head/count before publication.
        """
        if self._snapshot_file is None:
            raise SnapshotError("replay cache not enabled for this Aquarium")
        checkpoint = self.checkpoint()
        verified = self.store.verify_chain(external_expected_head=self._committed_head)
        if verified["count"] != self._committed_event_count:
            raise SnapshotError("journal count changed during snapshot creation")
        if self.store.head() != self._committed_head:
            raise SnapshotError("journal advanced while snapshot was prepared")
        state = {
            "schema": STATE_SCHEMA,
            "agent_id": self.agent_id,
            "manifest_sha256": digest(self.manifest),
            "runtime_source_sha256": runtime_source_sha256(),
            "journal_events": self._committed_event_count,
            "journal_head": self._committed_head,
            "tick": self.tick,
            "world": {
                "tick": self.world.tick, "x": self.world.x, "y": self.world.y,
                "revision": self.world.revision,
            },
            "genome": self.genome.snapshot(),
            "learner": self.learner.snapshot() if self.learner is not None else None,
            "explorer": self.explorer.snapshot() if self.explorer is not None else None,
            "toy_contact_state": (
                self.sensory_bridge.model.snapshot()
                if self.sensory_bridge is not None else None
            ),
            "checkpoint": checkpoint,
        }
        return save_snapshot(self._snapshot_file, self._snapshot_key_file, state)

    def _replay(self, external_expected_head, *, signed_state=None):
        # Still audit the FULL SQLite journal hash chain and independent head.
        # Only deterministic SIMULATION of the authenticated prefix is
        # skipped when an explicit HMAC-protected sidecar is available.
        count = self.store.count()
        if count > MAX_REPLAY_EVENTS:
            raise AquariumError("journal exceeds bounded 100000-event replay budget")
        verified = self.store.verify_chain(external_expected_head=external_expected_head)
        if verified["count"] != count:
            raise AquariumError("journal changed during integrity verification")
        if signed_state is not None:
            bootstrap = self.store.event_at(1)["event"]
            if bootstrap != self._record(
                    "aquarium.bootstrap", "aquarium-bootstrap", self.manifest,
                    0, self.graph_sha256):
                raise AquariumError("aquarium bootstrap differs from signed cache")
            self._adopt_signed_replay_state(signed_state, committed_count=count)
            seen = signed_state["journal_events"]
            replayed_head = signed_state["journal_head"]
            start_seq = seen
        else:
            seen = 0
            replayed_head = None
            start_seq = 0
        for entry in self.store.iter_events(
                include_nonpublic=True, after_seq=start_seq):
            seen += 1
            if entry["seq"] != seen:
                raise AquariumError("journal sequence changed during streaming replay")
            replayed_head = entry["event_hash"]
            event = entry["event"]
            if seen == 1:
                if event != self._record("aquarium.bootstrap", "aquarium-bootstrap", self.manifest,
                                         0, self.graph_sha256):
                    raise AquariumError("aquarium seed, room, graph or clock mismatch")
                continue
            if event["visibility"] != "PRIVATE" or event["source"] != "mojum.aquarium.fixture":
                raise AquariumError("unexpected journal ownership or visibility")
            if event["observed_at_ns"] != self.tick * self.dt_ns:
                raise AquariumError("unsynchronized event time")
            if event["topic"] == "genesis.acquisition":
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
                # Goal-free replay always recomputes the intention, never
                # accepts journaled dx/dy as a privileged external script.
                args = ((None, None) if self.explorer is not None
                        else (move["dx"], move["dy"]))
                payload, next_world, next_learner = self._calculate(*args)
                if event != self._record("aquarium.frame", event["event_id"], payload,
                                         self.tick * self.dt_ns, self.genome.revision):
                    raise AquariumError("non-deterministic or mismatched committed frame")
                self._adopt_sensory(payload)
                self._adopt_developmental(payload)
                self.world = next_world
                self.learner = next_learner
                self.tick += 1
            else:
                raise AquariumError("unexpected aquarium journal topic")
        # Verify no row disappeared or appeared between the chain scan and
        # the streamed deterministic execution. Never adopt a partial RAM
        # checkpoint after concurrent history changes.
        if not seen:
            raise AquariumError("bootstrap event missing")
        if seen != count or replayed_head != verified["head"]:
            raise AquariumError("incomplete or changed streaming replay")
        if self.store.head() != replayed_head or self.store.count() != count:
            raise AquariumError("journal advanced during aquarium replay")
        self._committed_head = replayed_head
        self._committed_event_count = count

    def checkpoint(self):
        if self._committed_head is None or self.store.head() != self._committed_head:
            raise AquariumError("stale or unadopted aquarium checkpoint; reopen")
        result = self._local_checkpoint()
        if self.store.head() != self._committed_head:
            raise AquariumError("journal advanced during checkpoint; reopen")
        return result


def main():
    parser = argparse.ArgumentParser(description="Mojum R0.2 CPU-only fixture aquarium")
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--db", type=Path, required=True, help="explicit local SQLite file; not cloud")
    parser.add_argument("--genome", type=Path, default=root / "examples/genesis_seed.json")
    parser.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    parser.add_argument("--ticks", type=int, default=120)
    # None means the option was absent; a supplied nominal value is still an
    # external motor request and must not be silently accepted in goal_free.
    parser.add_argument("--dx", type=float, default=None)
    parser.add_argument("--dy", type=float, default=None)
    parser.add_argument("--neural-mode", choices=("shadow", "disabled"), default="shadow")
    parser.add_argument("--exploration-mode", choices=("scripted", "goal_free"),
                        default="scripted")
    parser.add_argument("--sensory-mode", choices=("none", "toy_probe"),
                        default="none")
    parser.add_argument("--watch", action="store_true",
                        help="emit PRIVATE synthetic frames as local JSONL")
    args = parser.parse_args()
    if not 0 <= args.ticks <= 1000:
        parser.error("--ticks must be within 0..1000")
    # Reject a forbidden external movement before opening/bootstrapping SQLite.
    # A failed CLI invocation must not create a new organism or journal.
    if args.exploration_mode == "goal_free" and (
            args.dx is not None or args.dy is not None):
        parser.error("--dx/--dy cannot override goal-free exploratory intentions")
    if args.exploration_mode == "scripted":
        # Invalid scripted motion is a CLI error, not an organism birth.
        # Reject NaN, infinite and out-of-budget displacement before SQLite
        # opens and writes the otherwise valid bootstrap event.
        try:
            _movement(0.02 if args.dx is None else args.dx)
            _movement(0.0 if args.dy is None else args.dy)
        except AquariumError as exc:
            parser.error(str(exc))
    with Aquarium(
        args.db, json.loads(args.genome.read_text(encoding="utf-8")),
        json.loads(args.graph.read_text(encoding="utf-8")),
        neural_mode=args.neural_mode, exploration_mode=args.exploration_mode,
        sensory_mode=args.sensory_mode,
    ) as aquarium:
        for _ in range(args.ticks):
            if args.exploration_mode == "goal_free":
                frame = aquarium.step()
            else:
                frame = aquarium.step(
                    dx=0.02 if args.dx is None else args.dx,
                    dy=0.0 if args.dy is None else args.dy,
                )
            if args.watch:
                print(json.dumps(frame, sort_keys=True, ensure_ascii=False,
                                 allow_nan=False), flush=True)
        checkpoint = aquarium.checkpoint()
        checkpoint["stored_events"] = aquarium.store.count()
        checkpoint["status"] = (
            "SYNTHETIC_SHADOW_LEARNING_NOT_FULL_BRAIN_OR_MUJOCO"
            if aquarium.neural_mode == "shadow"
            else "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO"
        )
        print(json.dumps(checkpoint, indent=None if args.watch else 2,
                         ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
