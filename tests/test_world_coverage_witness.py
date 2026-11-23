"""Hermetic stdlib tests for #926; no live/private journal access."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from mojum.world_coverage_witness import (
    CLASSIFICATION, CoverageError, INPUT_SCHEMA, MAX_BYTES, MAX_STEPS,
    decode, main, witness,
)


def fixture():
    return {
        "schema": INPUT_SCHEMA,
        "classification": CLASSIFICATION,
        "room": {"width": 4, "height": 4, "obstacles": [[2, 1, .1, 2]]},
        "start": [1, 2],
        "cell_size_m": 1,
        "steps": [{"dx": .5, "dy": 0}, {"dx": 1, "dy": 0},
                  {"dx": 0, "dy": 0}, {"dx": -.5, "dy": 0}],
    }


class WorldCoverageWitnessTests(unittest.TestCase):
    def test_deterministic_collision_and_grid(self):
        source = fixture()
        result = witness(source)
        self.assertEqual(result, witness(copy.deepcopy(source)))
        self.assertEqual((result["ticks"], result["blocked_steps"],
                          result["moving_steps"], result["no_progress_steps"]),
                         (4, 1, 2, 1))
        self.assertEqual(result["final_position"], [1, 2])
        self.assertEqual(result["distance_m"], 1)
        self.assertEqual(result["visited_cells"], [[1, 2]])
        self.assertEqual(result["nominal_grid_cells"], 16)
        self.assertEqual(len(result["trace_sha256"]), 64)
        self.assertFalse(result["physical_contact"])

    def test_path_visits_multiple_cells(self):
        f = fixture()
        f["room"]["obstacles"] = []
        f["steps"] = [{"dx": 1, "dy": 0}, {"dx": 0, "dy": 1}]
        r = witness(f)
        self.assertEqual(r["visited_cells"], [[1, 2], [2, 2], [2, 3]])
        self.assertEqual(r["distance_m"], 2)
        self.assertEqual(r["moving_steps"], 2)
        changed = copy.deepcopy(f)
        changed["steps"].reverse()
        self.assertNotEqual(r["trace_sha256"], witness(changed)["trace_sha256"])

    def test_zero_steps_and_boundary_clamp(self):
        f = fixture()
        f["room"]["obstacles"] = []
        f["start"] = [4, 4]
        f["steps"] = []
        r = witness(f)
        self.assertEqual(r["visited_cells"], [[3, 3]])
        self.assertEqual(r["ticks"], 0)
        self.assertEqual(r["distance_m"], 0)

    def test_private_rejected(self):
        f = fixture()
        f["classification"] = "PRIVATE"
        with self.assertRaises(CoverageError):
            witness(f)

    def test_invalid_movement_and_collision_start(self):
        f = fixture()
        f["start"] = [2, 2]
        with self.assertRaises(CoverageError):
            witness(f)
        f = fixture()
        f["steps"][0]["dx"] = float("nan")
        with self.assertRaises(CoverageError):
            witness(f)

    def test_budget_limit(self):
        f = fixture()
        f["steps"] = [{"dx": 0, "dy": 0}] * (MAX_STEPS + 1)
        with self.assertRaises(CoverageError):
            witness(f)
        f = fixture()
        f["cell_size_m"] = .0001
        with self.assertRaises(CoverageError):
            witness(f)
        f = fixture()
        f["room"]["obstacles"] *= 129
        with self.assertRaises(CoverageError):
            witness(f)
        with self.assertRaises(CoverageError):
            decode(b" " * (MAX_BYTES + 1))

    def test_reject_hostile_structures_and_numbers(self):
        f = fixture()
        f["steps"] = [{"dx": True, "dy": 0}]
        with self.assertRaises(CoverageError):
            witness(f)
        f = fixture()
        f["room"]["width"] = 10**500
        with self.assertRaises(CoverageError):
            witness(f)
        f = fixture()
        f["room"]["unused"] = "x"
        with self.assertRaises(CoverageError):
            witness(f)
        f = fixture()
        f["steps"][0] = {"dx": 1, "dy": 0, "private": "x"}
        with self.assertRaises(CoverageError):
            witness(f)

    def test_json_duplicate_nan_and_depth(self):
        with self.assertRaises(CoverageError):
            decode(b'{"a":1,"a":2}')
        with self.assertRaises(CoverageError):
            decode(b'{"x":NaN}')
        with self.assertRaises(CoverageError):
            decode(b'['*1500 + b'0' + b']'*1500)
        with self.assertRaises(CoverageError):
            decode(b'\xff')
        with self.assertRaises(CoverageError):
            decode(b'{"x":' + b'1'*5000 + b'}')

    def test_decoded_json_cli_round_trip_and_error(self):
        f = fixture()
        encoded = json.dumps(f).encode("utf-8")
        self.assertEqual(witness(decode(encoded)), witness(f))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "room.json"
            path.write_bytes(encoded)
            self.assertEqual(main([str(path)]), 0)
            path.write_bytes(b'{}')
            self.assertEqual(main([str(path)]), 2)

    def test_inconsistent_successful_float_no_progress(self):
        f = fixture()
        f["room"]["obstacles"] = []
        f["steps"] = [{"dx": 1e-300, "dy": 0}]
        r = witness(f)
        self.assertEqual(r["no_progress_steps"], 1)
        self.assertEqual(r["blocked_steps"], 0)
        self.assertEqual(r["moving_steps"], 0)


if __name__ == "__main__":
    unittest.main()
