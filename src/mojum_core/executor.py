"""Domain-neutral extraction of one deterministic, staged graph step."""
import json
from .graph import GraphError, validate, _freeze_ports

def detached(value):
    """Detach exact JSON-native data; do not accept Python-only coercions."""
    # Round-trip only after rejecting custom containers and non-string keys.
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 50000 or depth > 128:
            raise GraphError("Step value exceeds structural budget")
        kind = type(item)
        if kind is dict:
            for k, v in item.items():
                if type(k) is not str:
                    raise GraphError("Step values require string keys")
                stack.append((v, depth + 1))
        elif kind is list:
            stack.extend((v, depth + 1) for v in item)
        elif kind not in (str, int, float, bool, type(None)):
            raise GraphError("Step values require native JSON")
    try:
        wire = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
        if len(wire.encode("utf-8")) > 262144:
            raise GraphError("Step value exceeds wire budget")
        return json.loads(wire)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise GraphError("Invalid step JSON") from exc

class Executor:
    """Trusted, pure handlers only. No external side effects or concurrent calls.

    A handler receives detached inputs/config and integer tick/time_ns, and
    returns (output mapping, list of staged JSON effects). The core never
    dispatches an effect externally. Caller owns durable commit/replay policy.
    """
    def __init__(self, graph, *, ports, handlers, dt_ns=10_000_000):
        if type(dt_ns) is not int or not 1000 <= dt_ns <= 1000000000:
            raise GraphError("Invalid tick duration")
        self._ports = _freeze_ports(ports)
        validate(graph, ports=self._ports)
        self._graph = detached(graph)
        self._nodes, self._incoming, self._order = validate(self._graph, ports=self._ports)
        if type(handlers) is not dict or any(type(k) is not str for k in handlers):
            raise GraphError("Invalid handler registry")
        required = {n["kind"] for n in self._nodes.values()}
        if any(k not in handlers or not callable(handlers[k]) for k in required):
            raise GraphError("Missing node handler")
        self._handlers = {k: handlers[k] for k in required}
        self._dt_ns = dt_ns
        self._tick = 0

    @property
    def tick(self):
        return self._tick

    def step(self):
        results, effects = {}, []
        tick = self._tick
        for nid in self._order:
            spec = self._nodes[nid]
            kind = spec["kind"]
            inputs = {port: results[source][out]
                      for port, (source, out) in self._incoming[nid].items()}
            out, staged = self._handlers[kind](
                detached(inputs), detached(spec["config"]), tick, tick * self._dt_ns)
            if type(out) is not dict or any(type(k) is not str for k in out):
                raise GraphError("Invalid handler outputs")
            if out.keys() != self._ports[kind][1].keys():
                raise GraphError("Handler output ports mismatch")
            if type(staged) is not list:
                raise GraphError("Effects must be a list")
            results[nid] = detached(out)
            effects.extend(detached(staged))
        response = detached({"outputs": results, "effects": effects})
        self._tick += 1
        return response
