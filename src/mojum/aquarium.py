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
from .graph import validate as validate_graph
from .noosphere import EVENT_SCHEMA, LocalNoosphere, NoosphereError
from .sim import Simulation, digest
from .world_room import Room, advance, initial_state


SCHEMA = "mojum.aquarium.frame/1"
MANIFEST_SCHEMA = "mojum.aquarium.bootstrap/1"
MAX_REPLAY_EVENTS = 10_000
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
        external_expected_head=None,
    ):
        if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
            raise AquariumError("invalid virtual dt_ns")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise AquariumError("invalid agent ID")
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
            "room": _room_manifest(self.room, self.start),
            "source_classification": "synthetic_only_brain_unimplemented_no_mujoco",
        }
        self.genome = GenomeLedger(self.seed)
        self.world = initial_state(self.room, *self.start)
        self.tick = 0
        self.store = LocalNoosphere(db_path, agent_id)
        try:
            if self.store.count() == 0:
                self._append_record("aquarium.bootstrap", "aquarium-bootstrap", self.manifest, 0, self.graph_sha256)
            self._replay(external_expected_head)
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
        event = self._record(topic, event_id, payload, at_ns, source_revision)
        return self.store.append(event, expected_head=self.store.head())

    def _calculate(self, dx, dy):
        dx, dy = _movement(dx), _movement(dy)
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
        if brain.get("status") != "UNIMPLEMENTED" or brain.get("action") is not None:
            raise AquariumError("unexpected privileged brain behavior in fixture")
        payload = {
            "schema": SCHEMA, "tick": self.tick, "time_ns": self.tick * self.dt_ns,
            "genome_revision": self.genome.revision,
            "phenotype": pheno,
            "graph_execution_sha256": runtime.graph_hash,
            "interoception": output[self.kind_ids["sensory.interoception"]]["sensory"],
            "brain": brain, "world": world_obs,
            "movement_request": {"dx": dx, "dy": dy},
            "physical_model": "NONE_FIXTURE_ONLY",
        }
        return payload, new_world

    def step(self, *, dx=0.02, dy=0.0):
        payload, next_world = self._calculate(dx, dy)
        self._append_record("aquarium.frame", f"frame-{self.tick:012d}", payload,
                            self.tick * self.dt_ns, self.genome.revision)
        self.world = next_world
        self.tick += 1
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
        self._append_record("genesis.acquisition", "acq-" + event_id,
                            {"acquisition": acquired}, self.tick * self.dt_ns,
                            proposed.revision)
        self.genome = proposed
        return self.genome.revision

    def _replay(self, external_expected_head):
        self.store.verify_chain(external_expected_head=external_expected_head)
        count = self.store.count()
        if count > MAX_REPLAY_EVENTS:
            raise AquariumError("R0.2 replay exceeds 10,000 journal events; streaming pending")
        view = self.store.dataset(include_nonpublic=True, limit=MAX_REPLAY_EVENTS)
        items = view["projection"]["items"]
        if len(items) != count:
            raise AquariumError("incomplete replay dataset")
        if not items:
            raise AquariumError("bootstrap event missing")
        first = items[0]["event"]
        if (first["topic"] != "aquarium.bootstrap" or
                first["event_id"] != "aquarium-bootstrap" or
                first["payload"] != self.manifest):
            raise AquariumError("aquarium seed, room, graph or clock mismatch")
        for entry in items[1:]:
            event = entry["event"]
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
                next_ledger = GenomeLedger.from_snapshot(self.genome.snapshot())
                next_ledger.append(raw, next_ledger.revision)
                if next_ledger.revision != event["source_revision"]:
                    raise AquariumError("acquired gene revision mismatch")
                self.genome = next_ledger
            elif event["topic"] == "aquarium.frame":
                if event["event_id"] != f"frame-{self.tick:012d}":
                    raise AquariumError("frame identifier discontinuity")
                move = event["payload"].get("movement_request")
                if not isinstance(move, dict) or set(move) != {"dx", "dy"}:
                    raise AquariumError("invalid replayed movement")
                payload, next_world = self._calculate(move["dx"], move["dy"])
                if event["payload"] != payload or event["source_revision"] != self.genome.revision:
                    raise AquariumError("non-deterministic or mismatched committed frame")
                self.world = next_world
                self.tick += 1
            else:
                raise AquariumError("unexpected aquarium journal topic")

    def checkpoint(self):
        return {
            "schema": "mojum.aquarium.checkpoint/1",
            "agent_id": self.agent_id, "tick": self.tick,
            "sim_time_ns": self.tick * self.dt_ns,
            "genome_revision": self.genome.revision,
            "world_revision": self.world.revision,
            "journal_head": self.store.head(),
            "manifest_sha256": digest(self.manifest),
            "trust": "checksum_only_not_authenticated",
        }


def main():
    parser = argparse.ArgumentParser(description="Mojum R0.2 CPU-only fixture aquarium")
    root = Path(__file__).resolve().parents[2]
    parser.add_argument("--db", type=Path, required=True, help="explicit local SQLite file; not cloud")
    parser.add_argument("--genome", type=Path, default=root / "examples/genesis_seed.json")
    parser.add_argument("--graph", type=Path, default=root / "examples/first_loop.json")
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--dx", type=float, default=0.02)
    parser.add_argument("--dy", type=float, default=0.0)
    args = parser.parse_args()
    if not 0 <= args.ticks <= 1000:
        parser.error("--ticks must be within 0..1000")
    with Aquarium(
        args.db, json.loads(args.genome.read_text(encoding="utf-8")),
        json.loads(args.graph.read_text(encoding="utf-8"))
    ) as aquarium:
        for _ in range(args.ticks):
            aquarium.step(dx=args.dx, dy=args.dy)
        checkpoint = aquarium.checkpoint()
        checkpoint["stored_events"] = aquarium.store.count()
        checkpoint["status"] = "SYNTHETIC_INTEGRATION_NOT_NEURAL_OR_MUJOCO"
        print(json.dumps(checkpoint, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
