"""Audit billing totals stay exact under callers' Decimal contexts."""
import contextlib
from decimal import localcontext, ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN
from fractions import Fraction
import io
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.spartan_inflight_audit import AuditError, audit, main


class ExactCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "collector.jsonl"

    def write_costs(self, costs):
        events = []
        for index, cost in enumerate(costs):
            start = dict(at_ms=index * 2, kind="start", request_id=f"r{index}",
                         worker="worker", model="model")
            finish = dict(start, at_ms=index * 2 + 1, kind="finish", outcome="ok",
                          input_tokens=1, output_tokens=2, cost_usd=cost,
                          response_id=f"response{index}")
            events.extend((start, finish))
        self.path.write_text("".join(json.dumps(event) + "\n" for event in events),
                             encoding="utf-8")
        return self.path

    def test_low_precision_and_rounding_cannot_change_exact_total(self):
        self.write_costs(["0.123456789", "0.000000001"])
        for precision in (1, 3, 6, 28):
            for rounding in (ROUND_DOWN, ROUND_UP, ROUND_HALF_EVEN):
                with self.subTest(precision=precision, rounding=rounding), localcontext() as ctx:
                    ctx.prec = precision
                    ctx.rounding = rounding
                    self.assertEqual(audit(self.path)["cost_usd"], "0.123456790")

    def test_extreme_exponents_and_all_traps_preserve_context(self):
        self.write_costs(["999999.999999999", "0.000000001", "1000000"])
        with localcontext() as ctx:
            ctx.prec, ctx.Emax, ctx.Emin = 1, 1, -1
            ctx.rounding = ROUND_DOWN
            for signal in ctx.traps:
                ctx.traps[signal] = True
            ctx.clear_flags()
            before = ctx.copy()
            self.assertEqual(audit(self.path)["cost_usd"], "2000000.000000000")
            self.assertEqual((ctx.prec, ctx.Emax, ctx.Emin, ctx.rounding),
                             (before.prec, before.Emax, before.Emin, before.rounding))
            self.assertEqual(ctx.traps, before.traps)
            self.assertEqual(ctx.flags, before.flags)

    def test_cli_under_trapping_context_emits_exact_success_receipt(self):
        self.write_costs(["0.123456789", "0.000000001"])
        stdout, stderr = io.StringIO(), io.StringIO()
        with localcontext() as ctx, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            ctx.prec = 1
            for signal in ctx.traps:
                ctx.traps[signal] = True
            code = main(["--input", str(self.path), "--target", "1", "--require-target"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["cost_usd"], "0.123456790")
        self.assertEqual((result["completed"], result["input_tokens"], result["output_tokens"]),
                         (2, 2, 4))

    def test_scale_and_empty_log_compatibility(self):
        cases = (([], "0"), (["0"], "0"), (["0.000000000"], "0.000000000"),
                 (["1", "2"], "3"), (["1.20", "2.3000"], "3.5000"),
                 (["999999.999999999", "0.000000001"], "1000000.000000000"))
        for costs, expected in cases:
            with self.subTest(costs=costs):
                self.assertEqual(audit(self.write_costs(costs))["cost_usd"], expected)

    def test_random_costs_match_independent_rational_sum_in_any_order(self):
        rng = random.Random(1498)
        costs = []
        for _ in range(1000):
            places = rng.randrange(10)
            whole = rng.randrange(1_000_000)
            fraction = rng.randrange(10**places)
            costs.append(str(whole) + (f".{fraction:0{places}d}" if places else ""))
        expected = sum(map(Fraction, costs), Fraction(0))
        for ordering in (costs, list(reversed(costs))):
            with localcontext() as ctx:
                ctx.prec = 2
                report = audit(self.write_costs(ordering))
            self.assertEqual(Fraction(report["cost_usd"]), expected)
            self.assertEqual(len(report["cost_usd"].partition(".")[2]), 9)

    def test_per_event_admission_is_unchanged_under_hostile_context(self):
        for cost in ("1000000.000000001", "-1", "1e-9", "0.0000000001", "NaN", 0.1):
            with self.subTest(cost=cost), localcontext() as ctx:
                ctx.prec = 1
                for signal in ctx.traps:
                    ctx.traps[signal] = True
                with self.assertRaises(AuditError):
                    audit(self.write_costs([cost]))


if __name__ == "__main__":
    unittest.main()
