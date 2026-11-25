"""Adversarial tests for bounded synthetic cloth contact telemetry."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

from mojum.cloth_contact_telemetry import (
    MAX_SAMPLES, ClothTelemetryError, reduce_contacts,
)


def sample(tick=0, contact="body", force=100, strain=25):
    return {"tick": tick, "contact": contact,
            "normal_force_mn": force, "strain_ue": strain}


class DictChild(dict):
    pass


class ClothContactTelemetryTests(unittest.TestCase):
    def test_reduces_categories_and_returns_no_raw_samples(self):
        report = reduce_contacts([
            sample(1, "body", 120, -9),
            sample(1, "hand", 40, 15),
            sample(3, "world", 200, 4),
            sample(3, "body", 80, 2),
        ])
        self.assertEqual(report.sample_count, 4)
        self.assertEqual((report.tick_start, report.tick_end), (1, 3))
        self.assertEqual(
            [(x.kind, x.count, x.normal_force_sum_mn, x.normal_force_peak_mn)
             for x in report.contacts],
            [("body", 2, 200, 120), ("hand", 1, 40, 40), ("world", 1, 200, 200)],
        )
        self.assertEqual(report.max_abs_strain_ue, 15)
        self.assertFalse(hasattr(report, "samples"))

    def test_empty_input_is_well_defined(self):
        report = reduce_contacts([])
        self.assertEqual(report.sample_count, 0)
        self.assertIsNone(report.tick_start)
        self.assertIsNone(report.tick_end)
        self.assertEqual(report.max_abs_strain_ue, 0)
        self.assertTrue(all(x.count == 0 for x in report.contacts))

    def test_digest_is_deterministic_and_input_order_sensitive(self):
        a, b = sample(1), sample(1, "hand", 50, 10)
        self.assertEqual(reduce_contacts([a, b]), reduce_contacts([dict(a), dict(b)]))
        self.assertNotEqual(
            reduce_contacts([a, b]).input_sha256,
            reduce_contacts([b, a]).input_sha256,
        )

    def test_tick_order_is_non_decreasing(self):
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts([sample(2), sample(1)])

    def test_exact_native_scalar_types_required(self):
        bad = [
            sample(True), sample(0, force=True), sample(0, strain=False),
            sample(0, contact="BODY"),
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ClothTelemetryError):
                reduce_contacts([item])
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts([sample()], dt_ns=True)

    def test_sample_shape_and_container_are_strict(self):
        for item in (
            {**sample(), "extra": 1},
            {"tick": 0, "contact": "body", "normal_force_mn": 1},
            DictChild(sample()),
        ):
            with self.subTest(item=item), self.assertRaises(ClothTelemetryError):
                reduce_contacts([item])
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts(sample())

    def test_oversized_sample_shape_is_rejected_with_bounded_extra_memory(self):
        # Build the caller's dictionary before tracing. Rejection must not
        # allocate a tuple/set proportional to every unexpected field. Keep
        # the tracer isolated from any surrounding resource-warning audit.
        script = textwrap.dedent("""\
            import tracemalloc
            from mojum.cloth_contact_telemetry import ClothTelemetryError, reduce_contacts
            samples = [{f"unexpected_{i}": 0 for i in range(100_000)}]
            tracemalloc.start()
            try:
                reduce_contacts(samples)
            except ClothTelemetryError:
                pass
            else:
                raise AssertionError("oversized sample was accepted")
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            print(peak)
        """)
        environment = dict(os.environ)
        environment.pop("PYTHONTRACEMALLOC", None)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment,
            capture_output=True, text=True, timeout=15, check=True,
        )
        self.assertLess(int(result.stdout), 256 * 1024)

    def test_four_fields_still_require_exact_native_keys(self):
        class KeyAlias(str):
            pass

        for key in ("unknown", 0, KeyAlias("tick")):
            item = sample()
            item[key] = item.pop("tick")
            self.assertEqual(len(item), 4)
            with self.subTest(key=key), self.assertRaises(ClothTelemetryError):
                reduce_contacts([item])

    def test_ranges_and_classification_fail_closed(self):
        for item in (
            sample(-1), sample(force=-1), sample(force=1_000_000_001),
            sample(strain=10_000_001), sample(tick=(1 << 53)),
        ):
            with self.subTest(item=item), self.assertRaises(ClothTelemetryError):
                reduce_contacts([item])
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts([sample()], classification="PRIVATE")
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts([sample()], dt_ns=999)

    def test_total_force_overflow_budget_is_rejected(self):
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts([sample(force=1_000_000_000)
                             for _ in range(MAX_SAMPLES)])

    def test_sample_limit_is_enforced(self):
        with self.assertRaises(ClothTelemetryError):
            reduce_contacts(sample(i) for i in range(MAX_SAMPLES + 1))


if __name__ == "__main__":
    unittest.main()
