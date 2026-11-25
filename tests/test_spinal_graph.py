"""Topology and malformed-input acceptance for the opt-in spinal fixture."""
import copy
import json
import unittest

from mojum.graph import GraphError, validate as validate_universal_graph
from mojum.spinal_graph import (
    GRAPH_SCHEMA, MAX_REVISION, SEGMENTS, SpinalError, build_spinal_graph,
    neighbors, require_identifier, require_int, require_number,
    validate_spinal_graph,
)


class SpinalGraphTests(unittest.TestCase):
    def test_31_segment_inventory_and_region_boundaries(self):
        self.assertEqual(len(SEGMENTS), 31)
        self.assertEqual(SEGMENTS[:8], tuple(f"C{i}" for i in range(1, 9)))
        self.assertEqual(SEGMENTS[8:20], tuple(f"T{i}" for i in range(1, 13)))
        self.assertEqual(SEGMENTS[20:25], tuple(f"L{i}" for i in range(1, 6)))
        self.assertEqual(SEGMENTS[25:30], tuple(f"S{i}" for i in range(1, 6)))
        self.assertEqual(SEGMENTS[-1], "Co1")
        self.assertEqual(neighbors("C1"), ("C2",))
        self.assertEqual(neighbors("C8"), ("C7", "T1"))
        self.assertEqual(neighbors("T12"), ("T11", "L1"))
        self.assertEqual(neighbors("L5"), ("L4", "S1"))
        self.assertEqual(neighbors("Co1"), ("S5",))

    def test_every_route_is_adjacent_bidirectional_and_typed(self):
        graph = build_spinal_graph(revision=9)
        self.assertEqual(graph["schema"], GRAPH_SCHEMA)
        self.assertEqual(graph["revision"], 9)
        self.assertEqual(len(graph["edges"]), 60)
        nodes = {record["id"]: record for record in graph["segments"]}
        route_pairs = set()
        for edge in graph["edges"]:
            source, target = edge["from"], edge["to"]
            self.assertIn(target["segment"], neighbors(source["segment"]))
            self.assertEqual(
                nodes[source["segment"]]["outputs"][source["port"]],
                nodes[target["segment"]]["inputs"][target["port"]],
            )
            route_pairs.add((source["segment"], target["segment"]))
        self.assertEqual(route_pairs, {(right, left) for left, right in route_pairs})
        for node in nodes.values():
            self.assertEqual(node["inputs"]["intention"], "mojum.spinal-intention/1")
            self.assertEqual(node["outputs"]["motor"], "mojum.spinal-motor/1")
            self.assertEqual(node["outputs"]["ascending"], "mojum.spinal-sensory/1")

    def test_canonical_order_roundtrip_and_detached_results(self):
        canonical = build_spinal_graph(5)
        scrambled = copy.deepcopy(canonical)
        scrambled["segments"].reverse()
        scrambled["edges"].reverse()
        before = copy.deepcopy(scrambled)
        checked = validate_spinal_graph(scrambled)
        self.assertEqual(checked, canonical)
        self.assertEqual(scrambled, before)
        self.assertEqual(validate_spinal_graph(json.loads(json.dumps(checked))), canonical)
        checked["segments"][0]["inputs"]["sensory"] = "changed"
        checked["edges"][0]["to"]["segment"] = "Co1"
        self.assertEqual(scrambled, before)
        self.assertEqual(build_spinal_graph(5), canonical)

    def test_schema_does_not_impersonate_executable_universal_graph_ir(self):
        graph = build_spinal_graph()
        with self.assertRaisesRegex(GraphError, "graph schema"):
            validate_universal_graph(graph)
        for schema in ("mechanicus.universal-graph/1", "mechanicus.universal-graph/2"):
            graph["schema"] = schema
            with self.assertRaises(SpinalError):
                validate_spinal_graph(graph)

    def test_missing_extra_duplicate_and_unknown_segments(self):
        for change in ("missing", "extra", "duplicate", "unknown"):
            graph = build_spinal_graph()
            if change == "missing":
                graph["segments"].pop()
            elif change == "extra":
                graph["segments"].append(copy.deepcopy(graph["segments"][0]))
            elif change == "duplicate":
                graph["segments"][-1] = copy.deepcopy(graph["segments"][0])
            else:
                graph["segments"][-1]["id"] = "C9"
            with self.subTest(change=change), self.assertRaises(SpinalError):
                validate_spinal_graph(graph)

    def test_missing_extra_duplicate_self_and_nonadjacent_routes(self):
        for change in ("missing", "extra", "duplicate", "self", "remote", "unknown"):
            graph = build_spinal_graph()
            if change == "missing":
                graph["edges"].pop()
            elif change == "extra":
                graph["edges"].append(copy.deepcopy(graph["edges"][0]))
            elif change == "duplicate":
                graph["edges"][-1] = copy.deepcopy(graph["edges"][0])
            else:
                graph["edges"][0]["to"]["segment"] = {
                    "self": "C1", "remote": "L1", "unknown": "brain",
                }[change]
            with self.subTest(change=change), self.assertRaises(SpinalError):
                validate_spinal_graph(graph)

    def test_port_types_and_directions_are_not_interchangeable(self):
        mutations = (
            lambda g: g["segments"][0]["inputs"].__setitem__("sensory", "any"),
            lambda g: g["segments"][0]["outputs"].__setitem__("motor", "mojum.spinal-sensory/1"),
            lambda g: g["segments"][0]["inputs"].pop("intention"),
            lambda g: g["segments"][0]["outputs"].__setitem__("new", "any"),
            lambda g: g["edges"][0]["from"].__setitem__("port", "ascending"),
            lambda g: g["edges"][0]["to"].__setitem__("port", "sensory"),
        )
        for index, mutate in enumerate(mutations):
            graph = build_spinal_graph()
            mutate(graph)
            with self.subTest(index=index), self.assertRaises(SpinalError):
                validate_spinal_graph(graph)

    def test_exact_record_fields_and_bounded_collections(self):
        mutations = (
            lambda g: g.__setitem__("extra", 0),
            lambda g: g.pop("schema"),
            lambda g: g.__setitem__("segments", tuple(g["segments"])),
            lambda g: g.__setitem__("edges", g["edges"] * 1000),
            lambda g: g["segments"][0].__setitem__("config", {}),
            lambda g: g["edges"][0].__setitem__("delay", 1),
            lambda g: g["edges"][0]["from"].__setitem__("extra", 0),
            lambda g: g["segments"][0].__setitem__("id", "C" * 65),
        )
        for index, mutate in enumerate(mutations):
            graph = build_spinal_graph()
            mutate(graph)
            with self.subTest(index=index), self.assertRaises(SpinalError):
                validate_spinal_graph(graph)

    def test_revision_boundaries_and_native_integer_contract(self):
        for revision in (0, MAX_REVISION):
            self.assertEqual(validate_spinal_graph(build_spinal_graph(revision))["revision"], revision)
        for revision in (-1, MAX_REVISION + 1, True, 1.0, "1", None, 10**1000):
            with self.subTest(kind=type(revision).__name__), self.assertRaises(SpinalError):
                build_spinal_graph(revision)
            graph = build_spinal_graph()
            graph["revision"] = revision
            with self.assertRaises(SpinalError):
                validate_spinal_graph(graph)

    def test_scalar_helpers_reject_nonfinite_and_out_of_budget_values(self):
        self.assertEqual(require_int(5, "tick", minimum=5, maximum=5), 5)
        for value in (True, -1, 2**63, 0.0, None):
            with self.subTest(value=value), self.assertRaises(SpinalError):
                require_int(value, "tick")
        for value in (-1_000_000, 1_000_000, 0, 0.5):
            self.assertEqual(require_number(value, "sample"), float(value))
            self.assertIs(type(require_number(value, "sample")), float)
        for value in (True, "0", None, float("nan"), float("inf"),
                      -float("inf"), 1_000_001, 10**1000):
            with self.subTest(kind=type(value).__name__), self.assertRaises(SpinalError):
                require_number(value, "sample")
        self.assertEqual(require_identifier("a-z_A.0:1", "name"), "a-z_A.0:1")
        for value in ("", "a" * 65, "has space", "C\N{CYRILLIC SMALL LETTER A}", "\ud800", 1):
            with self.subTest(kind=type(value).__name__), self.assertRaises(SpinalError):
                require_identifier(value, "name")
        for value in ("C0", "C9", "T13", "S6", "Co2", True, ["C1"]):
            with self.subTest(value=value), self.assertRaises(SpinalError):
                neighbors(value)

    def test_hostile_subclasses_are_rejected_without_running_hooks(self):
        class HostileDict(dict):
            def __len__(self):
                raise AssertionError("dict subclass hook")

            def __iter__(self):
                raise AssertionError("dict subclass hook")

        class HostileList(list):
            def __len__(self):
                raise AssertionError("list subclass hook")

            def __iter__(self):
                raise AssertionError("list subclass hook")

        class HostileString(str):
            def __len__(self):
                raise AssertionError("string subclass hook")

            def __eq__(self, other):
                raise AssertionError("string subclass comparison")

        class HostileInt(int):
            def __le__(self, other):
                raise AssertionError("integer subclass hook")

            def __abs__(self):
                raise AssertionError("integer subclass hook")

        for graph in (HostileDict(), []):
            with self.assertRaises(SpinalError):
                validate_spinal_graph(graph)
        replacements = (
            ("schema", HostileString(GRAPH_SCHEMA)),
            ("revision", HostileInt(0)),
            ("segments", HostileList()),
            ("edges", HostileList()),
        )
        for key, value in replacements:
            graph = build_spinal_graph()
            graph[key] = value
            with self.subTest(key=key), self.assertRaises(SpinalError):
                validate_spinal_graph(graph)
        graph = build_spinal_graph()
        graph["segments"][0] = HostileDict()
        with self.assertRaises(SpinalError):
            validate_spinal_graph(graph)
        graph = build_spinal_graph()
        graph["segments"][0]["inputs"] = HostileDict()
        with self.assertRaises(SpinalError):
            validate_spinal_graph(graph)
        with self.assertRaises(SpinalError):
            require_number(HostileInt(0), "sample")
        with self.assertRaises(SpinalError):
            require_identifier(HostileString("name"), "name")

    def test_hostile_keys_and_cycles_fail_before_comparison_or_traversal(self):
        class HostileKey(str):
            armed = False

            def __hash__(self):
                if self.armed:
                    raise AssertionError("key hashing hook")
                return str.__hash__(self)

            def __eq__(self, other):
                if self.armed:
                    raise AssertionError("key comparison hook")
                return str.__eq__(self, other)

        key = HostileKey("schema")
        graph = {key: GRAPH_SCHEMA, "revision": 0, "segments": [], "edges": []}
        key.armed = True
        with self.assertRaises(SpinalError):
            validate_spinal_graph(graph)
        graph = build_spinal_graph()
        graph["segments"][0]["inputs"] = graph
        with self.assertRaises(SpinalError):
            validate_spinal_graph(graph)
        graph = build_spinal_graph()
        graph["edges"][0]["from"] = graph["edges"][0]
        with self.assertRaises(SpinalError):
            validate_spinal_graph(graph)


if __name__ == "__main__":
    unittest.main()
