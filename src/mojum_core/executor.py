"""Domain-neutral extraction of one deterministic, staged graph step."""
import json
import math
from threading import Lock
from .graph import GraphError, validate, _freeze_ports, _json_string_wire_size

MAX_STEP_BYTES = 256 * 1024
MAX_STEP_NODES = 50_000

def _measure(value):
    """Count compact JSON bytes and nodes before allocating a wire buffer.

    Shared references count once per occurrence on the wire. Inputs must not
    be mutated concurrently by their owner.
    """
    pending = [(value, 0)]
    count = size = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_STEP_NODES or depth > 128:
            raise GraphError("Step value exceeds structural budget")
        kind = type(item)
        if kind in (dict, list):
            n = len(item)
            if count + len(pending) + n * (2 if kind is dict else 1) > MAX_STEP_NODES:
                raise GraphError("Step value exceeds structural budget")
            size += 2 + max(0, n - 1)
            if kind is dict:
                size += n
                count += n
                for k, v in item.items():
                    if type(k) is not str:
                        raise GraphError("Step values require string keys")
                    size += _json_string_wire_size(k)
                    if size > MAX_STEP_BYTES:
                        raise GraphError("Step value exceeds wire budget")
                    pending.append((v, depth + 1))
            else:
                pending.extend((v, depth + 1) for v in item)
        elif kind is str:
            size += _json_string_wire_size(item)
        elif kind is int:
            if item.bit_length() > MAX_STEP_BYTES * 3:
                raise GraphError("Step integer exceeds wire budget")
            try:
                size += len(str(item))
            except ValueError as exc:
                raise GraphError("Step integer exceeds wire budget") from exc
        elif kind is float:
            if not math.isfinite(item):
                raise GraphError("Step values require finite numbers")
            size += len(repr(item))
        elif kind is bool:
            size += 4 if item else 5
        elif item is None:
            size += 4
        else:
            raise GraphError("Step values require native JSON")
        if size > MAX_STEP_BYTES:
            raise GraphError("Step value exceeds wire budget")
    return size, count

def detached(value):
    """Preflight native JSON budgets before serializing a bounded snapshot."""
    try:
        _measure(value)
        wire = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
        if len(wire.encode("utf-8")) > MAX_STEP_BYTES:
            raise GraphError("Step value exceeds wire budget")
        return json.loads(wire)
    except GraphError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError, RuntimeError) as exc:
        raise GraphError("Invalid step JSON") from exc

class Executor:
    """Trusted, pure handlers only. No external side effects. Concurrent/reentrant steps fail closed.

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
        self._step_lock = Lock()
        self._active_step = None

    @property
    def tick(self):
        return self._tick

    def step(self):
        # Serialize only admission/publication of the ownership token, never
        # handler execution. The outer finally covers interruption during
        # admission as well as evaluation, without releasing another step.
        token = object()
        try:
            with self._step_lock:
                if self._active_step is not None:
                    raise GraphError("Executor step already in progress")
                self._active_step = token
            response = self._evaluate(self._tick)
            self._tick += 1
            return response
        finally:
            with self._step_lock:
                if self._active_step is token:
                    self._active_step = None

    def _evaluate(self, tick):
        """Evaluate pure handlers at an explicit tick without committing state.

        Private compatibility boundary; callers own serialization and publication.
        """
        results, effects = {}, []
        # Check the complete accumulated response before retaining each result.
        # Per-handler output is already owned by the handler; we bound core copies.
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
            _measure(out)
            _measure(staged)
            candidate = {"outputs": {**results, nid: out}, "effects": effects + staged}
            _measure(candidate)
            results[nid] = detached(out)
            effects.extend(detached(staged))
        return detached({"outputs": results, "effects": effects})
