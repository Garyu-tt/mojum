import unittest

from mojum.cloth_damage import (
    ClothDamageError, MAX_SAMPLES, SCHEMA, audit_damage,
)


def request(samples=None, **changes):
    value = {
        "schema": SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "consent": True,
        "profile_revision": "fabric-r1",
        "threshold": 1.25,
        "samples": [] if samples is None else samples,
    }
    value.update(changes)
    return value


class ClothDamageTests(unittest.TestCase):
    def test_empty_report_is_deterministic(self):
        a = audit_damage(request())
        b = audit_damage(request())
        self.assertEqual(a.digest, b.digest)
        self.assertEqual((a.sample_count, a.exceeded_count), (0, 0))

    def test_threshold_boundary_is_not_exceeded(self):
        report = audit_damage(request([
            {"tick": 1, "edge_id": "e1", "stretch_ratio": 1.25},
            {"tick": 1, "edge_id": "e2", "stretch_ratio": 1.25001},
        ]))
        self.assertEqual(report.exceeded_count, 1)
        self.assertFalse(report.findings[0].exceeded)
        self.assertTrue(report.findings[1].exceeded)

    def test_report_detaches_from_input(self):
        source = request([{"tick": 2, "edge_id": "e1", "stretch_ratio": 1.4}])
        report = audit_damage(source)
        source["samples"][0]["stretch_ratio"] = 1.0
        self.assertEqual(report.findings[0].stretch_ratio, 1.4)

    def test_rejects_duplicate_edge_per_tick(self):
        with self.assertRaises(ClothDamageError):
            audit_damage(request([
                {"tick": 1, "edge_id": "e", "stretch_ratio": 1.1},
                {"tick": 1, "edge_id": "e", "stretch_ratio": 1.2},
            ]))

    def test_allows_same_edge_on_later_tick(self):
        report = audit_damage(request([
            {"tick": 1, "edge_id": "e", "stretch_ratio": 1.1},
            {"tick": 2, "edge_id": "e", "stretch_ratio": 1.3},
        ]))
        self.assertEqual(report.sample_count, 2)

    def test_rejects_regressing_ticks(self):
        with self.assertRaises(ClothDamageError):
            audit_damage(request([
                {"tick": 2, "edge_id": "a", "stretch_ratio": 1.1},
                {"tick": 1, "edge_id": "b", "stretch_ratio": 1.1},
            ]))

    def test_rejects_bool_and_nonfinite_numbers(self):
        for value in (True, float("nan"), float("inf"), 0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ClothDamageError):
                    audit_damage(request([{
                        "tick": 1, "edge_id": "e", "stretch_ratio": value,
                    }]))

    def test_rejects_bool_tick_and_unknown_fields(self):
        with self.assertRaises(ClothDamageError):
            audit_damage(request([{"tick": True, "edge_id": "e", "stretch_ratio": 1.1}]))
        with self.assertRaises(ClothDamageError):
            audit_damage(request(extra="no"))

    def test_rejects_private_or_missing_consent(self):
        with self.assertRaises(ClothDamageError):
            audit_damage(request(provenance="PRIVATE"))
        with self.assertRaises(ClothDamageError):
            audit_damage(request(consent=False))

    def test_rejects_oversized_batch(self):
        samples = [
            {"tick": i, "edge_id": f"e{i}", "stretch_ratio": 1.0}
            for i in range(MAX_SAMPLES + 1)
        ]
        with self.assertRaises(ClothDamageError):
            audit_damage(request(samples))

    def test_rejects_malformed_containers_and_revisions(self):
        for bad in (None, {}, [], "x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ClothDamageError):
                    audit_damage(bad)
        with self.assertRaises(ClothDamageError):
            audit_damage(request(profile_revision="../escape"))

    def test_digest_changes_with_measurement(self):
        a = audit_damage(request([{"tick": 1, "edge_id": "e", "stretch_ratio": 1.1}]))
        b = audit_damage(request([{"tick": 1, "edge_id": "e", "stretch_ratio": 1.2}]))
        self.assertNotEqual(a.digest, b.digest)


if __name__ == "__main__":
    unittest.main()
