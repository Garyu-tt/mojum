"""Adversarial, pure-stdlib tests for isolated world trace minimization."""
import copy
import json
import unittest

from mojum.world_trace_minimize import (
    TraceMinimizeError, loads_case, minimize_case, MAX_MOVES,
)


def make_case(moves=None, kind="blocked", pos=None):
    witness = {"kind": kind}
    if pos is not None:
        witness["position"] = pos
    return {
        "schema": "mojum.world_trace_minimize/1",
        "data_classification": "PUBLIC_SYNTHETIC",
        "room": {"width": 4, "height": 4, "obstacles": [[2, 1, .01, 2]]},
        "start": [1, 2],
        "moves": moves if moves is not None else [
            {"dx": 0, "dy": 0}, {"dx": .5, "dy": 0},
            {"dx": .1, "dy": 0}, {"dx": 1, "dy": 0},
            {"dx": 0, "dy": 0}],
        "witness": witness,
    }


class TraceMinimizeTests(unittest.TestCase):
    def test_reduces_irrelevant_moves_and_replay_is_exact(self):
        case = make_case()
        before = copy.deepcopy(case)
        result = minimize_case(case)
        self.assertTrue(result["complete"])
        self.assertTrue(result["witness_preserved"])
        self.assertLess(result["minimized_moves"], result["source_moves"])
        self.assertEqual(case, before)
        self.assertEqual(minimize_case(result["replay_case"])["replay_case"],
                         result["replay_case"])

    def test_requires_causal_prefix_for_blocked_at_position(self):
        case = make_case([
            {"dx": .25, "dy": 0}, {"dx": .5, "dy": 0},
            {"dx": .5, "dy": 0}, {"dx": .5, "dy": 0},
            {"dx": -.25, "dy": 0}, {"dx": .6, "dy": 0},
        ], kind="blocked_at_position", pos=[1.75, 2])
        # .25 + .5 reaches exactly 1.75 in binary, then .5 blocks.
        out = minimize_case(case)
        self.assertTrue(out["witness_preserved"])
        self.assertEqual(out["final_position"], [1.75, 2])
        self.assertLess(out["minimized_moves"], len(case["moves"]))

    def test_end_position_witness_and_empty_trace(self):
        case = make_case([{"dx": 0, "dy": 0}] * 5, "end_position", [1, 2])
        out = minimize_case(case)
        self.assertEqual(out["minimized_moves"], 0)
        self.assertEqual(out["final_position"], [1, 2])
        self.assertTrue(out["complete"])

    def test_no_witness_fails_closed(self):
        with self.assertRaisesRegex(TraceMinimizeError, "does not satisfy"):
            minimize_case(make_case([{"dx": .1, "dy": 0}]))

    def test_deterministic_digest_and_indices(self):
        a, b = minimize_case(make_case()), minimize_case(make_case())
        self.assertEqual(a, b)
        self.assertEqual(len(a["replay_sha256"]), 64)
        self.assertEqual(len(a["retained_indices"]), a["minimized_moves"])
        self.assertEqual(sorted(a["retained_indices"]), a["retained_indices"])

    def test_probes_exhausted_is_not_called_minimal(self):
        case = make_case()
        case["max_probes"] = 1
        out = minimize_case(case)
        self.assertFalse(out["complete"])
        self.assertEqual(out["minimized_moves"], len(case["moves"]))
        self.assertEqual(out["probes"], 1)

    def test_bad_witness_and_classification(self):
        for field in ("PRIVATE", "PUBLIC", False):
            with self.subTest(field=field):
                case = make_case()
                case["data_classification"] = field
                with self.assertRaises(TraceMinimizeError):
                    minimize_case(case)
        for kind in ("motor", "", True, None):
            case = make_case()
            case["witness"]["kind"] = kind
            with self.assertRaises(TraceMinimizeError):
                minimize_case(case)

    def test_hostile_boolean_nan_infinity_big_int(self):
        for value in (True, False, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(value=str(value)[:20]):
                case = make_case()
                case["moves"][0]["dx"] = value
                with self.assertRaises(TraceMinimizeError):
                    minimize_case(case)

    def test_strict_schema_rejects_extra_fields(self):
        for key, value in (("notes", "PRIVATE"), ("shell", "echo hi")):
            case = make_case()
            case[key] = value
            with self.assertRaises(TraceMinimizeError):
                minimize_case(case)
        case = make_case()
        case["moves"][0]["revision"] = "forged"
        with self.assertRaises(TraceMinimizeError):
            minimize_case(case)

    def test_move_obstacle_and_probe_budgets(self):
        case = make_case([{"dx": 0, "dy": 0}] * (MAX_MOVES + 1))
        with self.assertRaises(TraceMinimizeError):
            minimize_case(case)
        case = make_case()
        case["room"]["obstacles"] *= 129
        with self.assertRaises(TraceMinimizeError):
            minimize_case(case)
        for n in (0, True, 257):
            case = make_case()
            case["max_probes"] = n
            with self.assertRaises(TraceMinimizeError):
                minimize_case(case)

    def test_strict_json_duplicates_nonfinite_and_limit(self):
        self.assertEqual(loads_case(json.dumps(make_case())), make_case())
        for raw in ('{"schema":1,"schema":2}', '{"x":NaN}',
                    '{"x":Infinity}', "[1,2]", "{bad", "{}" * 30000):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(TraceMinimizeError):
                    loads_case(raw)

    def test_closed_thin_wall_remains_blocked(self):
        case = make_case([{"dx": 2, "dy": 0}])
        out = minimize_case(case)
        self.assertEqual(out["minimized_moves"], 1)
        self.assertEqual(out["blocked_events"], 1)
        self.assertEqual(out["final_position"], [1, 2])

    def test_room_mutations_do_not_leak_to_output(self):
        case = make_case()
        output = minimize_case(case)
        case["room"]["obstacles"][0][0] = 300
        case["moves"][0]["dx"] = 400
        self.assertEqual(output["replay_case"]["room"]["obstacles"], [[2, 1, .01, 2]])
        self.assertNotEqual(output["replay_case"]["moves"][0]["dx"], 400)

    def test_bad_position_and_missing_witness_fields(self):
        for witness in ({"kind": "blocked", "position": [0, 0]},
                        {"kind": "end_position"},
                        {"kind": "end_position", "position": [True, 2]}):
            case = make_case()
            case["witness"] = witness
            with self.assertRaises(TraceMinimizeError):
                minimize_case(case)


if __name__ == "__main__":
    unittest.main()
