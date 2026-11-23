"""Hermetic CPU-only tests for authoring-only point-room occupancy raster."""

import copy
import json
import unittest

from mojum.world_room import Room, advance, initial_state
from mojum.world_occupancy import (
    MAX_AXIS_CELLS, MAX_TOTAL_CELLS, rasterize, verify_raster,
)


class OccupancyTests(unittest.TestCase):
    def test_nonsquare_orientation_and_preview_is_repeatable(self):
        room = Room(3, 2, ((2.25, 1.25, 0.5, 0.5),))
        first = rasterize(room, columns=3, rows=2)
        self.assertEqual(first["blocked_rows"], ["...", "..#"])
        self.assertEqual(first["row_order"], "bottom_to_top")
        self.assertEqual(first, rasterize(room, columns=3, rows=2))
        self.assertTrue(verify_raster(room, json.loads(json.dumps(first))))

    def test_thin_wall_not_missed_by_cell_center_sampling(self):
        room = Room(4, 1, ((1.49, 0.2, 0.001, 0.6),))
        self.assertEqual(rasterize(room, columns=4, rows=1)["blocked_rows"], [".#.."])
        state = initial_state(room, 0.5, 0.5)
        _, observation = advance(room, state, tick=1, dx=2, dy=0,
                                 expected_revision=state.revision)
        self.assertTrue(observation["blocked"])

    def test_inclusive_edge_and_corner_contact(self):
        edge = Room(2, 1, ((1, 0.2, 0.1, 0.5),))
        self.assertEqual(rasterize(edge, columns=2, rows=1)["blocked_rows"], ["##"])
        corner = Room(2, 2, ((1, 1, 0.1, 0.1),))
        self.assertEqual(rasterize(corner, columns=2, rows=2)["blocked_rows"],
                         ["##", "##"])

    def test_room_edges_are_not_obstacles(self):
        room = Room(1, 1)
        self.assertEqual(rasterize(room, columns=1, rows=1)["blocked_rows"], ["."])
        self.assertEqual(rasterize(room, columns=2, rows=2)["blocked_rows"], ["..", ".."])

    def test_reject_malformed_or_excessive_grid_sizes(self):
        room = Room(2, 3)
        for columns, rows in ((True, 1), (1, False), (0, 1), (-1, 1),
                              (1.5, 1), (1, "2"), (MAX_AXIS_CELLS + 1, 1),
                              (1, MAX_AXIS_CELLS + 1), (1e100, 2)):
            with self.subTest(columns=columns, rows=rows):
                with self.assertRaises(ValueError):
                    rasterize(room, columns=columns, rows=rows)
        self.assertEqual(len(rasterize(room, columns=128, rows=128)["blocked_rows"]), 128)
        self.assertEqual(MAX_TOTAL_CELLS, 128 * 128)
        with self.assertRaises(ValueError):
            rasterize(None)

    def test_max_obstacles_full_coverage_stays_bounded(self):
        room = Room(2, 2, ((0, 0, 2, 2),) * 4096)
        raster = rasterize(room, columns=128, rows=128)
        self.assertEqual(raster["blocked_rows"], ["#" * 128] * 128)
        self.assertTrue(verify_raster(room, raster))

    def test_subnormal_coordinate_resolution_is_rejected(self):
        room = Room(5e-324, 1)
        with self.assertRaisesRegex(ValueError, "resolution collapses"):
            rasterize(room, columns=2, rows=1)
        self.assertEqual(rasterize(room, columns=1, rows=1)["blocked_rows"], ["."])

    def test_large_finite_dimensions_do_not_overflow_grid_edges(self):
        room = Room(1e308, 1e308, ((1e307, 1e307, 1e307, 1e307),))
        preview = rasterize(room, columns=16, rows=16)
        self.assertEqual(len(preview["blocked_rows"]), 16)
        self.assertTrue(verify_raster(room, preview))

    def test_preview_tampering_or_stale_geometry_is_rejected(self):
        room = Room(4, 1, ((1.4, 0.2, 0.01, 0.6),))
        baseline = rasterize(room, columns=4, rows=1)
        samples = []
        corrupt_bitmap = copy.deepcopy(baseline)
        corrupt_bitmap["blocked_rows"][0] = "...."
        samples.append(corrupt_bitmap)
        wrong_digest = copy.deepcopy(baseline)
        wrong_digest["geometry_sha256"] = "0" * 64
        samples.append(wrong_digest)
        unknown_key = copy.deepcopy(baseline)
        unknown_key["private"] = "forbidden"
        samples.append(unknown_key)
        bool_columns = copy.deepcopy(baseline)
        bool_columns["columns"] = True
        samples.append(bool_columns)
        invalid_rows = copy.deepcopy(baseline)
        invalid_rows["blocked_rows"] = [1]
        samples.append(invalid_rows)
        for candidate in samples:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    verify_raster(room, candidate)
        with self.assertRaises(ValueError):
            verify_raster(Room(4, 1, ((1.6, 0.2, 0.01, 0.6),)), baseline)

    def test_mutable_caller_obstacles_cannot_change_existing_room(self):
        obstacles = [[0.5, 0.5, 0.1, 0.1]]
        room = Room(2, 2, obstacles)
        before = rasterize(room, columns=4, rows=4)
        obstacles[0][0] = 1.5
        self.assertEqual(before, rasterize(room, columns=4, rows=4))

    def test_output_is_plain_json_data_not_privileged_policy_state(self):
        frame = rasterize(Room(2, 2, ((0.5, 0.5, 0.2, 0.2),)), columns=2, rows=2)
        self.assertTrue(verify_raster(Room(2, 2, ((0.5, 0.5, 0.2, 0.2),)),
                                      json.loads(json.dumps(frame))))
        self.assertFalse(set(frame).intersection({"tick", "brain", "organism_id", "senses", "motor"}))
        self.assertEqual(len(frame["blocked_rows"]), frame["rows"])
        self.assertTrue(all(len(row) == frame["columns"] for row in frame["blocked_rows"]))


if __name__ == "__main__":
    unittest.main()
