"""No third-party package is needed for this deterministic gate."""
import contextlib
import io
import json
import math
import unittest
from unittest.mock import patch

from mojum.optional_pinocchio_benchmark import (
    PlanarRig, PinocchioFK, analytic_fk, analytic_ik, fixture_urdf,
    main, run_benchmark, SCHEMA, TOLERANCE_M,
)


class KinematicsBenchmarkTests(unittest.TestCase):
    def test_origin_and_known_geometry(self):
        rig = PlanarRig()
        self.assertAlmostEqual(analytic_fk(rig, (0.0, 0.0))[0], 0.7)
        self.assertAlmostEqual(analytic_fk(rig, (math.pi / 2, 0.0))[1], 0.7)
        self.assertAlmostEqual(analytic_fk(rig, (0.0, math.pi / 2))[0], 0.4)

    def test_ik_reconstructs_seeded_fk_in_both_elbow_directions(self):
        rig = PlanarRig()
        for q in ((0.4, 1.0), (-0.4, -1.0), (2.2, -0.4), (-1.8, 0.8)):
            target = analytic_fk(rig, q)
            recovered = analytic_ik(rig, target)
            self.assertLessEqual(math.dist(analytic_fk(rig, recovered), target), TOLERANCE_M)

    def test_unreachable_and_limit_only_targets_fail_closed(self):
        rig = PlanarRig()
        for bad in ((1., 1.), (0., 0.), (math.nan, 0.), (0., math.inf), (True, 0.)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                analytic_ik(rig, bad)

    def test_limits_and_numeric_schema_are_strict(self):
        rig = PlanarRig()
        for q in ((True, 0.1), (math.nan, 0.), (math.inf, 0.), (2.7, 0.),
                  (0., -2.6), (0.,), "1,2", (0., 0., 0.)):
            with self.subTest(q=q), self.assertRaises(ValueError):
                analytic_fk(rig, q)
        for kwargs in ({"link1_m": False}, {"link2_m": -1.},
                       {"joint_limits_rad": ((0., 3.5), (-2., 2.))},
                       {"joint_limits_rad": [(0., 1.), (-1., 1.)]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PlanarRig(**kwargs)

    def test_urdf_is_generated_without_external_io_or_assets(self):
        fixture = fixture_urdf(PlanarRig())
        self.assertIn('joint name="elbow"', fixture)
        self.assertIn('link name="tool_tip"', fixture)
        self.assertNotIn('filename=', fixture)
        self.assertNotIn('package://', fixture)

    def test_fixed_seed_evidence_repeats_and_bounds_runtime(self):
        first = run_benchmark(cases=48, seed=17)
        second = run_benchmark(cases=48, seed=17)
        for key in ("backend", "schema", "seed", "cases", "max_fk_error_m", "max_ik_error_m"):
            self.assertEqual(first[key], second[key])
        self.assertEqual(first["schema"], SCHEMA)
        self.assertTrue(first["virtual_only"])
        self.assertEqual(first["pin_distribution"], None)
        self.assertLessEqual(first["max_ik_error_m"], TOLERANCE_M)
        self.assertGreaterEqual(first["avg_case_ns"], 0)

    def test_foreign_backend_mismatch_is_not_hidden(self):
        with self.assertRaisesRegex(RuntimeError, "FK divergence"):
            run_benchmark(cases=1, _fk_override=lambda q: (0., 0.))
        with self.assertRaisesRegex(RuntimeError, "invalid coordinate"):
            run_benchmark(cases=1, _fk_override=lambda q: (float('nan'), 0.))

    def test_budget_and_unknown_backend_rejected(self):
        for kwargs in ({"cases": 0}, {"cases": 129}, {"cases": True},
                       {"seed": -1}, {"seed": True}, {"backend": "hw"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_benchmark(**kwargs)

    def test_optional_dependency_absence_fails_explicitly(self):
        with patch('mojum.optional_pinocchio_benchmark.metadata.version', side_effect=__import__('importlib').metadata.PackageNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "optional dependency missing"):
                PinocchioFK(PlanarRig())
        with patch('mojum.optional_pinocchio_benchmark.metadata.version', return_value="5.0.0"):
            with self.assertRaisesRegex(RuntimeError, "unreviewed pin version"):
                PinocchioFK(PlanarRig())

    def test_cli_emits_machine_readable_evidence_and_nonzero_on_error(self):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            self.assertEqual(main(["--cases", "3", "--seed", "2"]), 0)
        evidence = json.loads(capture.getvalue())
        self.assertEqual(evidence["cases"], 3)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--cases", "999"]), 2)


if __name__ == '__main__':
    unittest.main()
