"""Minimal deterministic virtual-clock runner; no physical or neural claims."""

import copy
import hashlib
import json
from .graph import GraphError, validate


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


class Simulation:
    def __init__(self, graph, dt_ns=10_000_000):
        self.graph = copy.deepcopy(graph)
        self.nodes, self.incoming, self.order = validate(self.graph)
        if type(dt_ns) is not int or not 1_000 <= dt_ns <= 1_000_000_000:
            raise GraphError("dt_ns must be integer in [1000, 1000000000]")
        self.dt_ns = dt_ns
        # Hash the validated, pinned graph, never the caller's mutable input.
        # Otherwise a concurrent mutation (or a custom deepcopy source) can
        # produce a checkpoint identity for a graph this runner never executes.
        self.graph_hash = digest(self.graph)
        self.tick = 0
        self.episodes = []

    def step(self):
        results = {}
        staged_episodes = []
        time_ns = self.tick * self.dt_ns
        for nid in self.order:
            spec = self.nodes[nid]
            inp = {port: results[source][out] for port, (source, out) in self.incoming[nid].items()}
            kind, config = spec["kind"], spec["config"]
            if kind == "genesis.seed":
                bpm = config.get("bpm", 60)
                if type(bpm) is not int or not 30 <= bpm <= 180:
                    raise GraphError("Genome bpm must be an integer in [30,180]")
                result = {"genome": {"fixture": True, "heartbeat_bpm": bpm, "genotype_status": "SEED_ONLY"}}
            elif kind == "body.heartbeat":
                bpm = inp["genome"]["heartbeat_bpm"]
                period_ns = 60_000_000_000 // bpm
                phase_ns = time_ns % period_ns
                result = {"pulse": {"beat_index": time_ns // period_ns, "phase_milli": 1000 * phase_ns // period_ns, "pulse": phase_ns < period_ns // 8, "synthetic": True}}
            elif kind == "sensory.interoception":
                result = {"sensory": {"kind": "interoception", "heartbeat": inp["pulse"], "observed_at_ns": time_ns}}
            elif kind == "world.room":
                level = config.get("light", "dim")
                if level not in ("dark", "dim", "bright"):
                    raise GraphError("Invalid world.room light fixture")
                result = {"world": {"kind": "static-room-fixture", "light": level, "observed_at_ns": time_ns}}
            elif kind == "brain.placeholder":
                result = {"observation": {"status": "UNIMPLEMENTED", "sensory_kind": inp["sensory"]["kind"], "world_light": inp["world"]["light"], "action": None}}
            elif kind == "memory.episode":
                episode = {"tick": self.tick, "observed_at_ns": time_ns, "sensory": inp["sensory"], "brain_observation": inp["observation"], "provenance": "in-memory-fixture-not-Noosphere"}
                staged_episodes.append(copy.deepcopy(episode))
                result = {"episode": episode}
            else:
                raise GraphError("Unsupported executor node kind")
            results[nid] = result
        # Commit all effects only after the entire graph has succeeded. A
        # later independent node may still fail after memory.episode runs.
        returned = copy.deepcopy(results)
        self.episodes.extend(staged_episodes)
        self.tick += 1
        return returned

    def snapshot(self):
        state = {"schema": "mojum.sim-checkpoint/0.1", "graph_sha256": self.graph_hash, "tick": self.tick, "dt_ns": self.dt_ns, "episodes": copy.deepcopy(self.episodes)}
        return {"state": state, "sha256": digest(state)}

    def restore(self, snapshot):
        """Cold-restore only episodes that the pinned graph can reproduce.

        The checksum detects accidental changes, not forgery. Deterministic
        replay also catches internally inconsistent checksum-rewritten histories.
        Validate everything before changing the live clock or episode list.
        """
        if (not isinstance(snapshot, dict) or set(snapshot) != {"state", "sha256"}
                or not isinstance(snapshot["state"], dict)):
            raise GraphError("Invalid snapshot")
        state = snapshot["state"]
        if set(state) != {"schema", "graph_sha256", "tick", "dt_ns", "episodes"}:
            raise GraphError("Invalid snapshot state shape")
        try:
            matches_checksum = (type(snapshot["sha256"]) is str
                                and snapshot["sha256"] == digest(state))
        except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
            raise GraphError("Snapshot contains noncanonical JSON") from exc
        if not matches_checksum:
            raise GraphError("Snapshot checksum mismatch")
        if (state["schema"] != "mojum.sim-checkpoint/0.1"
                or state["graph_sha256"] != self.graph_hash
                or state["dt_ns"] != self.dt_ns):
            raise GraphError("Snapshot version, graph or dt mismatch")
        tick, episodes = state["tick"], state["episodes"]
        if (type(tick) is not int or tick < 0
                or not isinstance(episodes, list) or len(episodes) != tick):
            raise GraphError("Inconsistent snapshot state")
        # A matching SHA-256 is not evidence that the episode payload came
        # from this graph. Re-run the deterministic graph from tick zero.
        replay = Simulation(self.graph, dt_ns=self.dt_ns)
        for i, episode in enumerate(episodes):
            if (not isinstance(episode, dict)
                    or type(episode.get("tick")) is not int
                    or episode["tick"] != i
                    or type(episode.get("observed_at_ns")) is not int
                    or episode["observed_at_ns"] != i * self.dt_ns):
                raise GraphError("Inconsistent episode sequence")
            replay.step()
            # Compare canonical JSON rather than Python dict equality so that
            # a forged False is never interchangeable with integer zero.
            try:
                actual = canonical(episode)
                expected = canonical(replay.episodes[-1])
            except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
                raise GraphError("Snapshot contains noncanonical episode JSON") from exc
            if actual != expected:
                raise GraphError("Snapshot episode differs from deterministic replay")
        self.tick = tick
        self.episodes = copy.deepcopy(episodes)
