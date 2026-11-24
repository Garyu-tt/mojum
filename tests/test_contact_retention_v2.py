import copy
import unittest

from mojum.contact_retention_v2 import (
    SCHEMA, ContactRetentionV2, RetentionError,
)


class ContactRetentionV2Tests(unittest.TestCase):
    def test_schema_is_separate_and_snapshot_round_trips(self):
        ledger = ContactRetentionV2()
        ledger.observe(cue="rgb:1:2:3", value_milli=250, tick=0)
        ledger.observe(cue="rgb:1:2:3", value_milli=750, tick=1)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["schema"], SCHEMA)
        restored = ContactRetentionV2.from_snapshot(snapshot)
        self.assertEqual(restored.snapshot(), snapshot)

    def test_capacity_eviction_is_lru_with_lexical_tie_break(self):
        ledger = ContactRetentionV2(max_cues=2, retention_ticks=50)
        ledger.observe(cue="b", value_milli=1, tick=1)
        ledger.observe(cue="a", value_milli=2, tick=2)
        ledger.observe(cue="c", value_milli=3, tick=3)
        self.assertEqual(set(ledger.entries), {"a", "c"})
        self.assertEqual(ledger.capacity_evictions, 1)

    def test_ttl_expiry_is_logical_and_inclusive(self):
        ledger = ContactRetentionV2(retention_ticks=2)
        ledger.observe(cue="a", value_milli=1, tick=0)
        ledger.observe(cue="b", value_milli=2, tick=2)
        self.assertIn("a", ledger.entries)
        ledger.observe(cue="c", value_milli=3, tick=3)
        self.assertNotIn("a", ledger.entries)
        self.assertEqual(ledger.expired_cues, 1)

    def test_mean_and_count_update(self):
        ledger = ContactRetentionV2()
        ledger.observe(cue="a", value_milli=100, tick=1)
        ledger.observe(cue="a", value_milli=300, tick=2)
        self.assertEqual(ledger.entries["a"]["count"], 2)
        self.assertEqual(ledger.entries["a"]["mean_milli"], 200)

    def test_saturated_count_stays_bounded_and_updates(self):
        ledger = ContactRetentionV2(max_observations_per_cue=2)
        for tick, value in enumerate((100, 200, 300, 400), 1):
            ledger.observe(cue="a", value_milli=value, tick=tick)
        self.assertEqual(ledger.entries["a"]["count"], 2)
        self.assertGreater(ledger.entries["a"]["mean_milli"], 200)

    def test_digest_and_order_are_deterministic(self):
        a, b = ContactRetentionV2(), ContactRetentionV2()
        for cue in ("z", "a", "m"):
            a.observe(cue=cue, value_milli=123, tick=len(a.entries))
        for cue in ("z", "a", "m"):
            b.observe(cue=cue, value_milli=123, tick=len(b.entries))
        self.assertEqual(a.digest(), b.digest())
        self.assertEqual([x["cue"] for x in a.report()["entries"]], ["a", "m", "z"])

    def test_snapshot_is_detached(self):
        ledger = ContactRetentionV2()
        ledger.observe(cue="a", value_milli=5, tick=1)
        snap = ledger.snapshot()
        snap["entries"][0]["count"] = 999
        self.assertEqual(ledger.entries["a"]["count"], 1)

    def test_snapshot_detects_tampering(self):
        ledger = ContactRetentionV2()
        ledger.observe(cue="a", value_milli=5, tick=1)
        snap = ledger.snapshot()
        snap["entries"][0]["mean_milli"] = 999
        with self.assertRaises(RetentionError):
            ContactRetentionV2.from_snapshot(snap)

    def test_rejects_v1_or_unknown_schema(self):
        ledger = ContactRetentionV2()
        snap = ledger.snapshot()
        snap["schema"] = "mojum.sensory-curiosity.association/1"
        with self.assertRaises(RetentionError):
            ContactRetentionV2.from_snapshot(snap)

    def test_rejects_bool_and_non_native_integer(self):
        for value in (True, 1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaises(RetentionError):
                    ContactRetentionV2().observe(cue="a", value_milli=value, tick=1)

    def test_rejects_nonmonotonic_ticks_and_bad_provenance(self):
        ledger = ContactRetentionV2()
        ledger.observe(cue="a", value_milli=5, tick=2)
        with self.assertRaises(RetentionError):
            ledger.observe(cue="b", value_milli=5, tick=2)
        with self.assertRaises(RetentionError):
            ledger.observe(cue="b", value_milli=5, tick=3, provenance="PRIVATE")

    def test_rejects_bad_bounds_and_cues(self):
        for kwargs in (
            {"cue": "", "value_milli": 1, "tick": 1},
            {"cue": "a", "value_milli": -1, "tick": 1},
            {"cue": "a", "value_milli": 1001, "tick": 1},
            {"cue": "a", "value_milli": 1, "tick": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(RetentionError):
                ContactRetentionV2().observe(**kwargs)

    def test_rejects_snapshot_duplicate_or_invalid_entry(self):
        ledger = ContactRetentionV2()
        ledger.observe(cue="a", value_milli=5, tick=1)
        snap = ledger.snapshot()
        snap["entries"].append(copy.deepcopy(snap["entries"][0]))
        unsigned = {k: v for k, v in snap.items() if k != "digest"}
        import hashlib, json
        snap["digest"] = hashlib.sha256(json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")).hexdigest()
        with self.assertRaises(RetentionError):
            ContactRetentionV2.from_snapshot(snap)

    def test_large_tick_gap_expires_old_cues(self):
        ledger = ContactRetentionV2(retention_ticks=5)
        ledger.observe(cue="a", value_milli=100, tick=0)
        ledger.observe(cue="b", value_milli=200, tick=6)
        self.assertEqual(set(ledger.entries), {"b"})
        self.assertEqual(ledger.expired_cues, 1)


if __name__ == "__main__":
    unittest.main()
