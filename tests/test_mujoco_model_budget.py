import copy
import hashlib
import json
import unittest

from mojum.mujoco_model_budget import (
    METADATA_SCHEMA, REPORT_SCHEMA, ModelBudgetError,
    audit_model_budget, available_profiles,
)


def sample(**changes):
    packet = {
        "schema": METADATA_SCHEMA, "engine_version": "3.17.0",
        "model_sha256": "a" * 64, "nbody": 4, "njnt": 3, "ngeom": 6,
        "nsite": 2, "nsensor": 1, "nactuator": 0, "ntendon": 0,
        "nmesh": 0, "nmeshvert": 0, "nmeshface": 0, "nflex": 0,
        "nflexvert": 0, "nflexedge": 0, "nflexelem": 0,
    }
    packet.update(changes)
    return packet


class MuJoCoModelBudgetTests(unittest.TestCase):
    def test_valid_small_model_is_admitted_but_never_authorized(self):
        report = audit_model_budget(sample())
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["status"], "within_budget")
        self.assertTrue(report["admissible"])
        self.assertFalse(report["authorizes_execution"])
        self.assertTrue(report["requires_native_engine_validation"])
        self.assertEqual(report["violations"], [])
        self.assertEqual(len(report["report_sha256"]), 64)

    def test_digest_is_deterministic_and_covers_report_body(self):
        left = audit_model_budget(sample())
        right = audit_model_budget(dict(reversed(list(sample().items()))))
        self.assertEqual(left, right)
        body = dict(left)
        digest = body.pop("report_sha256")
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=True,
                               allow_nan=False, separators=(",", ":")).encode("ascii")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), digest)

    def test_standard_profile_has_a_distinct_resource_ceiling(self):
        small = audit_model_budget(sample(nbody=65), "cpu_small")
        standard = audit_model_budget(sample(nbody=65), "cpu_standard")
        self.assertEqual(small["status"], "over_budget")
        self.assertEqual(standard["status"], "within_budget")
        self.assertEqual(small["violations"][0]["field"], "nbody")

    def test_reports_every_over_budget_resource_in_canonical_field_order(self):
        report = audit_model_budget(sample(nbody=65, ngeom=129, nsite=65))
        self.assertEqual([v["field"] for v in report["violations"]],
                         ["nbody", "ngeom", "nsite"])
        self.assertFalse(report["admissible"])

    def test_exact_profile_thresholds_are_inclusive(self):
        report = audit_model_budget(sample(
            nbody=64, njnt=32, ngeom=128, nsite=64, nsensor=32,
            nactuator=0, ntendon=16, nmesh=1, nmeshvert=12_000,
            nmeshface=24_000,
        ))
        self.assertEqual(report["status"], "within_budget")
        self.assertEqual(audit_model_budget(sample(nbody=65))["status"], "over_budget")

    def test_mesh_and_flex_cross_field_consistency(self):
        bad = (
            sample(nmeshvert=3), sample(nmesh=1), sample(nflexvert=3),
            sample(nflex=1, nflexvert=3, nflexedge=2),
        )
        for packet in bad:
            with self.subTest(packet=packet):
                with self.assertRaises(ModelBudgetError):
                    audit_model_budget(packet)
        good = audit_model_budget(sample(
            nmesh=1, nmeshvert=3, nmeshface=1,
            nflex=1, nflexvert=3, nflexedge=3, nflexelem=1,
        ), "cpu_standard")
        self.assertEqual(good["status"], "within_budget")

    def test_rejects_missing_extra_and_non_string_keys(self):
        for packet in (
            {"schema": METADATA_SCHEMA}, {**sample(), "extra": 1},
            {**sample(), 1: "unexpected"},
        ):
            with self.subTest(packet=packet):
                with self.assertRaises(ModelBudgetError):
                    audit_model_budget(packet)

    def test_rejects_large_extra_key_cardinality(self):
        packet = sample()
        packet.update({f"unexpected_{index}": index for index in range(20_000)})
        with self.assertRaises(ModelBudgetError):
            audit_model_budget(packet)

    def test_rejects_bool_float_negative_and_oversized_counts(self):
        for value in (True, 1.0, -1, 10_000_001, 10**1000):
            with self.subTest(value=str(value)[:16]):
                with self.assertRaises(ModelBudgetError):
                    audit_model_budget(sample(ngeom=value))

    def test_rejects_invalid_or_unbounded_version_and_model_digest(self):
        invalid = (
            sample(engine_version="3.17"), sample(engine_version="3.17.0\n"),
            sample(engine_version=3), sample(engine_version="1" * 129),
            sample(model_sha256="A" * 64), sample(model_sha256="x" * 64),
        )
        for packet in invalid:
            with self.subTest(packet=packet):
                with self.assertRaises(ModelBudgetError):
                    audit_model_budget(packet)

    def test_rejects_unknown_or_spoofed_profile(self):
        for profile in ("gpu", "CPU_SMALL", True, None):
            with self.subTest(profile=profile):
                with self.assertRaises(ModelBudgetError):
                    audit_model_budget(sample(), profile)

    def test_rejects_dict_subclass_and_int_subclass(self):
        class ForgedDict(dict):
            pass

        class ForgedInt(int):
            pass

        with self.assertRaises(ModelBudgetError):
            audit_model_budget(ForgedDict(sample()))
        with self.assertRaises(ModelBudgetError):
            audit_model_budget(sample(nbody=ForgedInt(4)))

    def test_input_is_not_mutated_and_result_is_detached(self):
        packet = sample()
        original = copy.deepcopy(packet)
        report = audit_model_budget(packet)
        self.assertEqual(packet, original)
        report["observed"]["nbody"] = 999
        report["limits"]["nbody"] = 999
        self.assertEqual(packet["nbody"], 4)
        self.assertEqual(audit_model_budget(packet)["observed"]["nbody"], 4)

    def test_profile_list_is_stable(self):
        self.assertEqual(available_profiles(), ("cpu_small", "cpu_standard"))


if __name__ == "__main__":
    unittest.main()
