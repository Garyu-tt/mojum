"""Issue #646: bounded chronological attestation for v3 escape snapshots.

Only synthetic measured outcomes are used: no SQLite, network or hardware.
"""
import copy
import unittest

from mojum.cell_policy_v3_escape import (
    MAX_CELLS, MAX_ORDERED_OUTCOMES, ORDERED_TAIL_SCHEMA, SCHEMA,
    CellExplorerV3Escape, CellV3EscapeError, Proprioception, STEP_METERS,
)


def fabricated(rests, probes, outcomes):
    """Create a historically ambiguous aggregate with a proposed event order."""
    counts = {}
    for _, cell, action, blocked in outcomes:
        entry = counts.setdefault(cell, {}).setdefault(action, [0, 0])
        entry[0] += 1
        entry[1] += int(blocked)
    recent = {}
    for tick, cell, _, _ in outcomes:
        recent[cell] = tick
    return {
        "schema": SCHEMA,
        "cell_size_m": STEP_METERS,
        "last_tick": len(outcomes) - 1,
        "updates": len(outcomes),
        "records": counts,
        "last_seen_at": recent,
        "evictions": 0,
        "forgotten_observations": 0,
        "consecutive_rests": rests,
        "consecutive_blocked_probes": probes,
        "ordered_tail_schema": ORDERED_TAIL_SCHEMA,
        "ordered_from_tick": 0,
        "ordered_outcomes": [
            {"tick": tick, "cell": cell, "action": action, "blocked": blocked}
            for tick, cell, action, blocked in outcomes
        ],
    }


class OrderedTail646Tests(unittest.TestCase):
    def test_rest_move_rest_cannot_claim_two_terminal_rests(self):
        state = fabricated(2, 0, [
            (0, "0:0", "rest", False),
            (1, "0:0", "x+", False),
            (2, "0:0", "rest", False),
        ])
        with self.assertRaisesRegex(CellV3EscapeError, "interleaved"):
            CellExplorerV3Escape.from_snapshot(state, require_ordered_tail=True)
        # Without the ordered extension these exact same counts are ambiguous.
        old = {k: v for k, v in state.items() if not k.startswith("ordered_")}
        self.assertEqual(CellExplorerV3Escape.from_snapshot(old).snapshot(), old)
        with self.assertRaisesRegex(CellV3EscapeError, "legacy"):
            CellExplorerV3Escape.from_snapshot(old, require_ordered_tail=True)

    def test_blocked_success_blocked_cannot_claim_two_failed_probes(self):
        state = fabricated(0, 2, [
            (0, "0:0", "x+", True),
            (1, "0:0", "x+", False),
            (2, "0:0", "x+", True),
        ])
        with self.assertRaisesRegex(CellV3EscapeError, "interleaved"):
            CellExplorerV3Escape.from_snapshot(state, require_ordered_tail=True)

    def test_valid_cross_cell_terminal_blocked_streak(self):
        policy = CellExplorerV3Escape()
        for tick in range(4):
            sense = Proprioception(
                tick, "0" * 64, (tick + 1) * STEP_METERS, 2.0, True
            )
            intent = policy.decide(sense)
            self.assertNotEqual(intent["action"], "rest")
            policy.observe(sense, intent, True)
        state = policy.snapshot()
        self.assertEqual(state["consecutive_blocked_probes"], 4)
        self.assertEqual(len(state["ordered_outcomes"]), 4)
        self.assertEqual(len({x["cell"] for x in state["ordered_outcomes"]}), 4)
        self.assertEqual(
            CellExplorerV3Escape.from_snapshot(
                state, require_ordered_tail=True
            ).snapshot(), state
        )

    def test_real_cross_cell_rests_roundtrip(self):
        policy = CellExplorerV3Escape()
        for tick in range(6):
            sense = Proprioception(
                tick, "0" * 64, 1.0 if tick < 5 else 2.0, 2.0, tick == 4
            )
            intent = policy.decide(sense)
            policy.observe(sense, intent, False)
        state = policy.snapshot()
        self.assertEqual(state["consecutive_rests"], 2)
        self.assertEqual(
            [row["action"] for row in state["ordered_outcomes"][-2:]],
            ["rest", "rest"],
        )
        self.assertEqual(
            CellExplorerV3Escape.from_snapshot(
                state, require_ordered_tail=True
            ).snapshot(), state
        )

    def test_tail_is_bounded_with_512_cell_lru_evictions(self):
        policy = CellExplorerV3Escape()
        for tick in range(MAX_CELLS + 8):
            sense = Proprioception(
                tick, "0" * 64, (tick + 1) * STEP_METERS, 5.0, False
            )
            intent = policy.decide(sense)
            policy.observe(sense, intent, False)
        state = policy.snapshot()
        self.assertEqual(len(state["records"]), MAX_CELLS)
        self.assertEqual(state["evictions"], 8)
        self.assertEqual(len(state["ordered_outcomes"]), MAX_ORDERED_OUTCOMES)
        self.assertTrue(all(
            row["cell"] in state["records"] for row in state["ordered_outcomes"]
        ))
        self.assertEqual(
            CellExplorerV3Escape.from_snapshot(
                state, require_ordered_tail=True
            ).snapshot(), state
        )

    def test_rejects_forged_version_order_type_and_aggregate(self):
        good = fabricated(1, 0, [
            (0, "0:0", "x+", False),
            (1, "0:0", "rest", False),
        ])
        self.assertEqual(
            CellExplorerV3Escape.from_snapshot(
                good, require_ordered_tail=True
            ).snapshot(), good
        )
        cases = [
            ("unknown ordered tail version", lambda x:
                x.update(ordered_tail_schema="other-version")),
            ("invalid ordered tail length", lambda x:
                x["ordered_outcomes"].pop()),
            ("invalid ordered tail event", lambda x:
                x["ordered_outcomes"][0].update(tick=True)),
            ("invalid ordered tail event", lambda x:
                x["ordered_outcomes"][1].update(blocked=True)),
            ("ordered tail exceeds measured aggregates", lambda x:
                x["records"]["0:0"]["rest"].__setitem__(1, 1)),
            ("ordered tail disagrees with cell last_seen_at", lambda x:
                x["last_seen_at"].update({"0:0": 0})),
        ]
        for message, mutate in cases:
            with self.subTest(message=message):
                bad = copy.deepcopy(good)
                mutate(bad)
                with self.assertRaises(CellV3EscapeError):
                    CellExplorerV3Escape.from_snapshot(
                        bad, require_ordered_tail=True
                    )

    def test_missing_partial_or_boolean_attestation_fails_closed(self):
        old = fabricated(1, 0, [(0, "0:0", "rest", False)])
        for key in ("ordered_tail_schema", "ordered_from_tick", "ordered_outcomes"):
            partial = copy.deepcopy(old)
            del partial[key]
            with self.subTest(key=key), self.assertRaises(CellV3EscapeError):
                CellExplorerV3Escape.from_snapshot(partial)
        with self.assertRaisesRegex(CellV3EscapeError, "invalid"):
            CellExplorerV3Escape.from_snapshot(old, require_ordered_tail=1)

    def test_historical_snapshot_migration_preserves_nonattested_history(self):
        model = CellExplorerV3Escape()
        for tick in range(2):
            sense = Proprioception(
                tick, "0" * 64, (tick + 1) * STEP_METERS, 2.0, True
            )
            model.observe(sense, model.decide(sense), True)
        new = model.snapshot()
        historic = {k: v for k, v in new.items() if not k.startswith("ordered_")}
        migrated = CellExplorerV3Escape.from_snapshot(historic)
        self.assertEqual(migrated.snapshot(), historic)
        with self.assertRaisesRegex(CellV3EscapeError, "legacy"):
            CellExplorerV3Escape.from_snapshot(
                historic, require_ordered_tail=True
            )
        tick = 2
        sense = Proprioception(
            tick, "0" * 64, 3 * STEP_METERS, 2.0, True
        )
        migrated.observe(sense, migrated.decide(sense), True)
        state = migrated.snapshot()
        self.assertEqual(state["ordered_from_tick"], 2)
        self.assertEqual(len(state["ordered_outcomes"]), 1)
        # The old two blocked probes are not proven by the new single row.
        with self.assertRaisesRegex(CellV3EscapeError, "legacy streak"):
            CellExplorerV3Escape.from_snapshot(
                state, require_ordered_tail=True
            )
        self.assertEqual(
            CellExplorerV3Escape.from_snapshot(state).snapshot(), state
        )


if __name__ == "__main__":
    unittest.main()
