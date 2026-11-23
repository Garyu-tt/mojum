"""Regression tests for offline environment name auditing."""
import unittest

from mojum.env_exposure_audit import audit_environment_names


class EnvironmentExposureAuditTests(unittest.TestCase):
    def test_safe(self):
        self.assertEqual(audit_environment_names(["PATH", "MOJUM_MODE"])["findings"], [])

    def test_sensitive_and_collision(self):
        result = audit_environment_names(["API_TOKEN", "api_token", "PATH", "PATH"])
        self.assertEqual(
            result["findings"],
            [{"name": "API_TOKEN", "code": "sensitive_name"},
             {"name": "PATH", "code": "duplicate"},
             {"name": "api_token", "code": "case_collision"},
             {"name": "api_token", "code": "sensitive_name"}],
        )

    def test_repeated_mixed_case_values_distinguish_collision_and_duplicate(self):
        for names, expected in (
            (["A", "a", "a"], [
                {"name": "A", "code": "case_collision"},
                {"name": "a", "code": "duplicate"},
            ]),
            (["a", "A", "A"], [
                {"name": "A", "code": "duplicate"},
                {"name": "A", "code": "case_collision"},
            ]),
        ):
            with self.subTest(names=names):
                self.assertEqual(audit_environment_names(names)["findings"], expected)

    def test_reject_invalid(self):
        for names in (["BAD=VALUE"], ["X" * 129], [None], "PATH"):
            with self.subTest(names=names):
                with self.assertRaises((TypeError, ValueError)):
                    audit_environment_names(names)

    def test_rejects_hostile_string_subclass(self):
        class HostileString(str):
            def upper(self):
                raise AssertionError("caller-controlled upper() must not execute")

        with self.assertRaises(TypeError):
            audit_environment_names([HostileString("TOKEN")])

    def test_bound(self):
        with self.assertRaises(ValueError):
            audit_environment_names(("X%d" % i for i in range(1025)))

    def test_deterministic(self):
        names = ["SECRET_KEY", "NORMAL", "normal"]
        self.assertEqual(audit_environment_names(names), audit_environment_names(iter(names)))


if __name__ == "__main__":
    unittest.main()
