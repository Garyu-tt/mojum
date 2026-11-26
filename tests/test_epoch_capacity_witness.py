"""Deterministic read-only version-pinned v4 journal capacity checks."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from mojum.epoch_capacity_witness import (
    EpochCapacityError,
    main,
    plan_epoch_capacity,
)


class EpochCapacityWitnessTests(unittest.TestCase):
    def plan(self, tick, epoch, events, steps):
        return plan_epoch_capacity(
            absolute_tick=tick, epoch_index=epoch,
            stored_events=events, steps=steps,
        )

    def test_genesis_and_boundary_crossing(self):
        zero = self.plan(0, 0, 1, 0)
        self.assertEqual(zero["stored_events_after"], 1)
        self.assertEqual(zero["marker_absolute_ticks"], [])
        a = self.plan(9998, 0, 9999, 1)
        self.assertEqual(a["absolute_end_tick"], 9999)
        self.assertEqual(a["new_epoch_markers"], 0)
        b = self.plan(9999, 0, 10000, 1)
        self.assertEqual(b["marker_absolute_ticks"], [9999])
        self.assertEqual(b["stored_events_after"], 10002)
        self.assertEqual(b["end_epoch_index"], 1)

    def test_recovered_marker_before_frame_must_not_double_append(self):
        pending = self.plan(9999, 0, 10000, 1)
        already_durable = self.plan(9999, 1, 10001, 1)
        self.assertEqual(pending["stored_events_after"], 10002)
        self.assertEqual(already_durable["stored_events_after"], 10002)
        self.assertEqual(already_durable["new_epoch_markers"], 0)
        self.assertEqual(self.plan(9999, 0, 10000, 0)["required_new_events"], 0)
        self.assertEqual(self.plan(9999, 1, 10001, 0)["required_new_events"], 0)

    def test_many_crossings_and_terminal_budget(self):
        full = self.plan(0, 0, 1, 99990)
        self.assertEqual(full["new_epoch_markers"], 9)
        self.assertEqual(full["marker_absolute_ticks"], [9999 * x for x in range(1, 10)])
        self.assertEqual(full["stored_events_after"], 100000)
        self.assertEqual(full["remaining_event_slots"], 0)
        self.assertEqual(full["end_epoch_index"], 9)
        self.assertEqual(self.plan(99990, 9, 100000, 0)["required_new_events"], 0)
        with self.assertRaises(EpochCapacityError):
            self.plan(99990, 9, 100000, 1)
        with self.assertRaises(EpochCapacityError):
            self.plan(99990, 10, 100001, 0)

    def test_rejects_inconsistent_cursor_count_and_phase(self):
        for args in (
            (1, 1, 3, 0), (10000, 0, 10001, 1),
            (9999, 0, 10001, 1), (9999, 1, 10000, 1),
            (9998, 0, 10000, 1), (0, 0, 0, 0),
            (89991, 8, 89999, 1), (99990, 8, 99999, 0),
        ):
            with self.subTest(args=args), self.assertRaises(EpochCapacityError):
                self.plan(*args)

    def test_rejects_bool_float_negative_huge_and_noncanonical_input(self):
        valid = dict(absolute_tick=9999, epoch_index=0, stored_events=10000, steps=1)
        for field, wrong in (
            ("absolute_tick", True), ("absolute_tick", -1),
            ("absolute_tick", 10**1000), ("absolute_tick", "9999"),
            ("epoch_index", True), ("epoch_index", 1.0),
            ("stored_events", False), ("stored_events", 10000.0),
            ("steps", True), ("steps", -1),
            ("steps", 10**1000),
        ):
            with self.subTest(field=field, wrong=type(wrong)), self.assertRaises(EpochCapacityError):
                plan_epoch_capacity(**{**valid, field: wrong})

    def test_cli_emits_no_partial_json_on_rejected_input(self):
        output = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as raised:
                main(["--absolute-tick", "9999", "--epoch-index", "0",
                      "--stored-events", "10001", "--steps", "1"])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("committed event count", error.getvalue())

    def test_cli_json_is_stable_and_contains_no_private_frames(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--absolute-tick", "9999", "--epoch-index", "1",
                           "--stored-events", "10001", "--steps", "1"])
        self.assertEqual(json.loads(output.getvalue()), result)
        self.assertEqual(result["remaining_event_slots"], 100000 - 10002)
        self.assertEqual(result["evidence"], "arithmetic_only_not_authenticated_not_write_authority")


if __name__ == "__main__":
    unittest.main()
