"""Legacy fixture compatibility adapter, deliberately outside mojum_core.

Only the six original stateless fixture node kinds are supported. Not an
Aquarium/Noosphere migration or generic plugin persistence protocol.
"""
import copy
import hashlib
import json
from threading import Lock
from mojum_core.executor import Executor, detached
from mojum_core.graph import GraphError, validate

PORTS = {
    "genesis.seed": ({}, {"genome": "genome/1"}),
    "body.heartbeat": ({"genome": "genome/1"}, {"pulse": "heartbeat/1"}),
    "sensory.interoception": ({"pulse": "heartbeat/1"}, {"sensory": "sensory/1"}),
    "world.room": ({}, {"world": "world/1"}),
    "brain.placeholder": ({"sensory": "sensory/1", "world": "world/1"}, {"observation": "brain-observation/1"}),
    "memory.episode": ({"sensory": "sensory/1", "observation": "brain-observation/1"}, {"episode": "episode/1"}),
}

def seed(i,c,t,ns):
    bpm=c.get("bpm",60)
    if type(bpm) is not int or not 30 <= bpm <= 180:
        raise GraphError("Genome bpm must be an integer in [30,180]")
    return {"genome":{"fixture":True,"heartbeat_bpm":bpm,"genotype_status":"SEED_ONLY"}},[]
def heartbeat(i,c,t,ns):
    period=60_000_000_000//i["genome"]["heartbeat_bpm"]
    phase=ns%period
    return {"pulse":{"beat_index":ns//period,"phase_milli":1000*phase//period,"pulse":phase<period//8,"synthetic":True}},[]
def sense(i,c,t,ns):
    return {"sensory":{"kind":"interoception","heartbeat":i["pulse"],"observed_at_ns":ns}},[]
def room(i,c,t,ns):
    level=c.get("light","dim")
    if level not in ("dark","dim","bright"): raise GraphError("Invalid world.room light fixture")
    return {"world":{"kind":"static-room-fixture","light":level,"observed_at_ns":ns}},[]
def brain(i,c,t,ns):
    return {"observation":{"status":"UNIMPLEMENTED","sensory_kind":i["sensory"]["kind"],"world_light":i["world"]["light"],"action":None}},[]
def memory(i,c,t,ns):
    e={"tick":t,"observed_at_ns":ns,"sensory":i["sensory"],"brain_observation":i["observation"],"provenance":"in-memory-fixture-not-Noosphere"}
    return {"episode":e},[e]
HANDLERS={"genesis.seed":seed,"body.heartbeat":heartbeat,"sensory.interoception":sense,
          "world.room":room,"brain.placeholder":brain,"memory.episode":memory}


MAX_REPLAY_TICKS = 10000

def canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)

def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()

class LegacySimulation:
    def __init__(self, graph, dt_ns=10_000_000):
        validate(graph, ports=PORTS)
        self._graph = detached(graph)
        self._dt_ns = dt_ns
        self._executor = self._new_executor()
        self._episodes = []
        self._state_lock = Lock()
        self._graph_hash = digest(self._graph)
        self._sinks = sum(n["kind"] == "memory.episode" for n in self._graph["nodes"])

    def _new_executor(self):
        return Executor(self._graph,ports=PORTS,handlers=HANDLERS,dt_ns=self._dt_ns)

    @property
    def tick(self):
        with self._state_lock:
            return self._executor.tick

    @property
    def episodes(self):
        with self._state_lock:
            return copy.deepcopy(self._episodes)

    def step(self):
        with self._state_lock:
            # Every successful step must remain inside the existing restore
            # replay budget, including graphs without any episode sinks.
            if self._executor.tick >= MAX_REPLAY_TICKS:
                raise GraphError("Checkpoint replay budget exhausted")
            result = self._executor.step()
            self._episodes.extend(result["effects"])
            return result["outputs"]

    def snapshot(self):
        with self._state_lock:
            state = {"schema":"mojum.sim-checkpoint/0.1","graph_sha256":self._graph_hash,
                     "tick":self._executor.tick,"dt_ns":self._dt_ns,
                     "episodes":copy.deepcopy(self._episodes)}
        return {"state":state,"sha256":digest(state)}

    def restore(self,snapshot):
        """Serialize restore with steps and snapshot capture.

        Caller must not mutate the supplied snapshot during restoration.
        """
        with self._state_lock:
            self._restore(snapshot)

    def _restore(self,snapshot):
        # Validate native shape before hashing and never mutate live state until
        # independently reproduced history is complete. Caller owns input bounds
        # for the complete Python snapshot; replay budget is explicit.
        if type(snapshot) is not dict or any(type(k) is not str for k in snapshot) or set(snapshot) != {"state","sha256"}:
            raise GraphError("Invalid snapshot")
        state = snapshot["state"]
        if type(state) is not dict or any(type(k) is not str for k in state) or set(state) != {"schema","graph_sha256","tick","dt_ns","episodes"}:
            raise GraphError("Invalid snapshot state shape")
        if (type(state["schema"]) is not str or state["schema"] != "mojum.sim-checkpoint/0.1"
            or type(state["graph_sha256"]) is not str or state["graph_sha256"] != self._graph_hash
            or type(state["dt_ns"]) is not int or state["dt_ns"] != self._dt_ns):
            raise GraphError("Snapshot version, graph or dt mismatch")
        tick, episodes = state["tick"],state["episodes"]
        if type(tick) is not int or not 0 <= tick <= MAX_REPLAY_TICKS or type(episodes) is not list or len(episodes) != tick*self._sinks:
            raise GraphError("Inconsistent snapshot state or replay budget exceeded")
        safe_episodes = []
        for index, episode in enumerate(episodes):
            safe = detached(episode)
            if (type(safe) is not dict or type(safe.get("tick")) is not int or safe["tick"] != index//self._sinks
                or type(safe.get("observed_at_ns")) is not int or safe["observed_at_ns"] != (index//self._sinks)*self._dt_ns):
                raise GraphError("Inconsistent episode sequence")
            safe_episodes.append(safe)
        checked = dict(state,episodes=safe_episodes)
        if type(snapshot["sha256"]) is not str or digest(checked) != snapshot["sha256"]:
            raise GraphError("Snapshot checksum mismatch")
        replay = self._new_executor()
        for i in range(tick):
            effects = replay.step()["effects"]
            expected = safe_episodes[i*self._sinks:(i+1)*self._sinks]
            if canonical(effects) != canonical(expected):
                raise GraphError("Snapshot episode differs from deterministic replay")
        self._executor = replay
        self._episodes = safe_episodes
