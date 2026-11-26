"""Typed topology for the opt-in, synthetic spinal CPU fixture.

This is the distinct ``mojum.spinal-graph/1`` contract, not Universal Graph
IR v1 or v2. Canonical IR container and executable-runtime integration remain
pending. The 31 logical names and adjacent links are engineering topology;
they do not establish anatomical fidelity or calibrated neural pathways.
"""
from __future__ import annotations

import math


GRAPH_SCHEMA = "mojum.spinal-graph/1"
SEGMENTS = tuple(
    f"{region}{index}"
    for region, count in (("C", 8), ("T", 12), ("L", 5), ("S", 5), ("Co", 1))
    for index in range(1, count + 1)
)
MAX_REVISION = 2**31 - 1
_SEGMENT_SET = frozenset(SEGMENTS)
_IDENTIFIER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)
_INPUTS = (
    ("sensory", "mojum.spinal-sensory/1"),
    ("intention", "mojum.spinal-intention/1"),
    ("intersegment", "mojum.spinal-sensory/1"),
)
_OUTPUTS = (
    ("ascending", "mojum.spinal-sensory/1"),
    ("intersegment", "mojum.spinal-sensory/1"),
    ("motor", "mojum.spinal-motor/1"),
)
_ROUTES = tuple(
    route
    for left, right in zip(SEGMENTS, SEGMENTS[1:])
    for route in ((left, right), (right, left))
)
_ROUTE_SET = frozenset(_ROUTES)


class SpinalError(ValueError):
    """Malformed or unsupported spinal fixture input."""


def require_int(value, name, minimum=0, maximum=2**63 - 1):
    """Return an exact native integer in the inclusive caller-declared range."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise SpinalError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def require_identifier(value, name):
    """Admit a nonempty ASCII identifier of at most 64 characters."""
    if (type(value) is not str or not 1 <= len(value) <= 64
            or any(char not in _IDENTIFIER_CHARS for char in value)):
        raise SpinalError(f"{name} must be a bounded ASCII identifier")
    return value


def require_number(value, name):
    """Normalize a native finite scalar whose absolute value is at most 1e6."""
    if type(value) not in (int, float) or abs(value) > 1_000_000:
        raise SpinalError(f"{name} must be a finite bounded number")
    if type(value) is float and not math.isfinite(value):
        raise SpinalError(f"{name} must be a finite bounded number")
    return float(value)


def _segment(value):
    require_identifier(value, "segment")
    if value not in _SEGMENT_SET:
        raise SpinalError("unknown spinal segment")
    return value


def neighbors(segment):
    """Return the adjacent logical segments in canonical cranial-to-caudal order."""
    segment = _segment(segment)
    index = SEGMENTS.index(segment)
    return tuple(
        SEGMENTS[other]
        for other in (index - 1, index + 1)
        if 0 <= other < len(SEGMENTS)
    )


def build_spinal_graph(revision=0):
    """Create a detached, versioned graph with all 31 segments and 60 routes."""
    require_int(revision, "revision", maximum=MAX_REVISION)
    return {
        "schema": GRAPH_SCHEMA,
        "revision": revision,
        "segments": [
            {"id": segment, "inputs": dict(_INPUTS), "outputs": dict(_OUTPUTS)}
            for segment in SEGMENTS
        ],
        "edges": [
            {
                "from": {"segment": source, "port": "intersegment"},
                "to": {"segment": target, "port": "intersegment"},
            }
            for source, target in _ROUTES
        ],
    }


def _object(value, fields, name):
    # Check exact native types and bounds before hashing/comparing caller keys.
    # Fixed record shapes also bound traversal without serializing untrusted
    # content or invoking container/scalar subclass hooks.
    if type(value) is not dict or len(value) != len(fields):
        raise SpinalError(f"invalid {name} fields")
    if any(type(key) is not str or len(key) > 64 for key in value):
        raise SpinalError(f"invalid {name} keys")
    if set(value) != set(fields):
        raise SpinalError(f"invalid {name} fields")
    return value


def _ports(value, expected, name):
    _object(value, tuple(key for key, _ in expected), name)
    for key, port_type in expected:
        actual = value[key]
        if (type(actual) is not str or len(actual) > 64
                or actual != port_type):
            raise SpinalError(f"invalid {name} type")


def _endpoint(value):
    _object(value, ("segment", "port"), "edge endpoint")
    segment = _segment(value["segment"])
    port = value["port"]
    if type(port) is not str or len(port) > 64 or port != "intersegment":
        raise SpinalError("edge must connect intersegment ports")
    return segment


def validate_spinal_graph(graph):
    """Validate the fixed typed topology and return its detached canonical form.

    Input list ordering is insignificant; segment/edge identities, directions
    and port types are exact. Missing, duplicate and nonadjacent routes fail.
    The result owns all its containers and cannot mutate the input graph.
    """
    _object(graph, ("schema", "revision", "segments", "edges"), "graph")
    schema = graph["schema"]
    if type(schema) is not str or len(schema) > 64 or schema != GRAPH_SCHEMA:
        raise SpinalError("unsupported spinal graph schema")
    revision = require_int(graph["revision"], "revision", maximum=MAX_REVISION)
    segments, edges = graph["segments"], graph["edges"]
    if type(segments) is not list or len(segments) != len(SEGMENTS):
        raise SpinalError("graph requires exactly 31 segments")
    if type(edges) is not list or len(edges) != len(_ROUTES):
        raise SpinalError("graph requires exactly 60 directed adjacent routes")
    seen_segments = set()
    for record in segments:
        _object(record, ("id", "inputs", "outputs"), "segment")
        segment = _segment(record["id"])
        if segment in seen_segments:
            raise SpinalError("duplicate spinal segment")
        seen_segments.add(segment)
        _ports(record["inputs"], _INPUTS, "input ports")
        _ports(record["outputs"], _OUTPUTS, "output ports")
    if seen_segments != _SEGMENT_SET:
        raise SpinalError("incomplete spinal segment inventory")
    seen_routes = set()
    for edge in edges:
        _object(edge, ("from", "to"), "edge")
        route = (_endpoint(edge["from"]), _endpoint(edge["to"]))
        if route not in _ROUTE_SET:
            raise SpinalError("route must connect adjacent distinct segments")
        if route in seen_routes:
            raise SpinalError("duplicate spinal route")
        seen_routes.add(route)
    if seen_routes != _ROUTE_SET:
        raise SpinalError("incomplete spinal route inventory")
    return build_spinal_graph(revision)
