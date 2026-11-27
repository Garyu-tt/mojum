"""Capacity and atomic publication regression for the existing SDK contract."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from mojum_core.graph import GraphError
from mojum_core.sdk import Registry

REV = "a" * 40


def handler(*args):
    return {}, []


def register(registry, plugin_id, count):
    nodes = {f"{plugin_id}.n{i}": {"inputs": {}, "outputs": {}}
             for i in range(count)}
    manifest = {"schema": "mojum.plugin/1", "id": plugin_id,
                "version": "1.0.0", "api_major": 1, "nodes": nodes}
    registry.register(manifest, dict.fromkeys(nodes, handler), revision=REV)


def snapshot(registry):
    # Observe the public executor handoff, without testing graph semantics here.
    with patch("mojum_core.sdk.Executor", side_effect=lambda graph, **kw: kw):
        return registry.executor({})


class SDKCapacityTests(unittest.TestCase):
    def test_provider_limit_and_atomic_rejection(self):
        registry = Registry()
        for count in (0, 257):
            with self.subTest(count=count):
                with self.assertRaises(GraphError):
                    register(registry, "bounded", count)
                self.assertEqual(registry.inventory(), [])
                self.assertEqual(snapshot(registry)["ports"], {})
                self.assertEqual(snapshot(registry)["handlers"], {})
        register(registry, "bounded", 256)
        self.assertEqual(len(snapshot(registry)["ports"]), 256)

    def test_total_limit_and_prior_snapshot(self):
        registry = Registry()
        for i in range(3):
            register(registry, f"p{i}", 256)
        prior = snapshot(registry)
        prior_inventory = registry.inventory()
        register(registry, "p3", 256)
        full = snapshot(registry)
        self.assertEqual(len(full["ports"]), 1024)
        self.assertEqual(set(full["ports"]), set(full["handlers"]))
        self.assertEqual(len(prior["ports"]), 768)
        self.assertEqual(set(prior["ports"]), set(prior["handlers"]))
        self.assertEqual(len(prior_inventory), 3)
        before = registry.inventory()
        with self.assertRaisesRegex(GraphError, "capacity"):
            register(registry, "overflow", 1)
        self.assertEqual(registry.inventory(), before)
        self.assertEqual(snapshot(registry), full)

    def test_concurrent_last_slot_has_one_winner(self):
        registry = Registry()
        for i, count in enumerate((256, 256, 256, 255)):
            register(registry, f"p{i}", count)
        prior = snapshot(registry)
        gate = Barrier(2)

        def contender(plugin_id):
            gate.wait(timeout=5)
            try:
                register(registry, plugin_id, 1)
            except GraphError as exc:
                return plugin_id, str(exc)
            return plugin_id, "ok"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender, pid) for pid in ("left", "right")]
            results = dict(f.result(timeout=10) for f in futures)
        winners = [pid for pid, status in results.items() if status == "ok"]
        self.assertEqual(len(winners), 1)
        loser = next(pid for pid in results if pid != winners[0])
        self.assertIn("capacity", results[loser])
        current = snapshot(registry)
        self.assertEqual(len(current["ports"]), 1024)
        self.assertEqual(set(current["ports"]), set(current["handlers"]))
        self.assertEqual(set(current["ports"]) - set(prior["ports"]),
                         {f"{winners[0]}.n0"})
        self.assertEqual(len(prior["ports"]), 1023)
        self.assertEqual({item["id"] for item in registry.inventory()},
                         {"p0", "p1", "p2", "p3", winners[0]})
