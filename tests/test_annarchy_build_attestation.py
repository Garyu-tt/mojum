"""Adversarial offline tests for the ANNarchy build metadata attestor."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest

from tools.annarchy_build_attestation import (
    MAX_ARTIFACT_BYTES,
    MAX_INPUT_BYTES,
    ManifestError,
    attest_build,
    canonical_json,
    parse_manifest_json,
    validate_manifest,
)


def valid_manifest() -> dict:
    return {
        "schema": "mojum.annarchy-build-manifest/1",
        "package": "ANNarchy",
        "package_version": "4.8.2",
        "backend": "openmp",
        "compiler": "gcc",
        "compiler_version": "13.2.0",
        "target": "linux-x86_64",
        "openmp": True,
        "threads": 4,
        "build_flags": ["-O2", "-std=c++17", "-fopenmp", "-DNDEBUG"],
        "source_revision": "a" * 40,
        "model_sha256": "b" * 64,
        "artifact_sha256": "c" * 64,
        "artifact_size_bytes": 1024,
    }


class AnnarchyBuildAttestationTests(unittest.TestCase):
    def test_valid_openmp_manifest(self):
        checked = validate_manifest(valid_manifest())
        self.assertEqual(checked["backend"], "openmp")
        self.assertEqual(checked["threads"], 4)

    def test_valid_single_thread_cpu_manifest(self):
        item = valid_manifest()
        item.update(backend="cpu", openmp=False, threads=1, build_flags=["-O3"])
        self.assertEqual(validate_manifest(item)["backend"], "cpu")

    def test_receipt_is_deterministic(self):
        first = attest_build(valid_manifest())
        second = attest_build(valid_manifest())
        self.assertEqual(first, second)
        self.assertEqual(first["manifest_sha256"], hashlib.sha256(canonical_json(valid_manifest())).hexdigest())

    def test_caller_mutation_does_not_change_validated_copy(self):
        item = valid_manifest()
        checked = validate_manifest(item)
        item["build_flags"].append("-O0")
        self.assertEqual(checked["build_flags"], ["-O2", "-std=c++17", "-fopenmp", "-DNDEBUG"])

    def test_unknown_field_rejected(self):
        item = valid_manifest()
        item["environment_dump"] = "not allowed"
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_missing_field_rejected(self):
        item = valid_manifest()
        del item["artifact_sha256"]
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_wrong_schema_rejected(self):
        item = valid_manifest()
        item["schema"] = "mojum.annarchy-build-manifest/2"
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_wrong_package_rejected(self):
        item = valid_manifest()
        item["package"] = "ANNarchy-fork"
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_bool_cannot_be_used_as_thread_count(self):
        item = valid_manifest()
        item["threads"] = True
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_cpu_backend_must_use_one_thread(self):
        item = valid_manifest()
        item.update(backend="cpu", openmp=False, threads=2)
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_openmp_requires_openmp_flag_and_multiple_threads(self):
        item = valid_manifest()
        item["openmp"] = False
        with self.assertRaises(ManifestError):
            validate_manifest(item)
        item = valid_manifest()
        item["threads"] = 1
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_thread_budget_is_bounded(self):
        item = valid_manifest()
        item["threads"] = 65
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_unsupported_target_rejected(self):
        item = valid_manifest()
        item["target"] = "linux-riscv64"
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_unsupported_compiler_rejected(self):
        item = valid_manifest()
        item["compiler"] = "unknown-compiler"
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_versions_must_be_numeric_triplets(self):
        item = valid_manifest()
        item["compiler_version"] = "gcc --version"
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_shell_fragments_and_unapproved_flags_rejected(self):
        for flag in ("-O2; touch /tmp/pwned", "@response.rsp", "-I/tmp/include", "-o"):
            item = valid_manifest()
            item["build_flags"] = [flag]
            with self.subTest(flag=flag), self.assertRaises(ManifestError):
                validate_manifest(item)

    def test_duplicate_flags_rejected(self):
        item = valid_manifest()
        item["build_flags"] = ["-O2", "-O2"]
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_flag_count_is_bounded(self):
        item = valid_manifest()
        item["build_flags"] = ["-O2"] * 33
        with self.assertRaises(ManifestError):
            validate_manifest(item)

    def test_malformed_digests_rejected(self):
        for name in ("source_revision", "model_sha256", "artifact_sha256"):
            item = valid_manifest()
            item[name] = "z" * len(item[name])
            with self.subTest(name=name), self.assertRaises(ManifestError):
                validate_manifest(item)

    def test_artifact_size_is_bounded_and_exact_integer(self):
        for size in (0, MAX_ARTIFACT_BYTES + 1, True, 1.5):
            item = valid_manifest()
            item["artifact_size_bytes"] = size
            with self.subTest(size=size), self.assertRaises(ManifestError):
                validate_manifest(item)

    def test_non_dict_input_rejected(self):
        with self.assertRaises(ManifestError):
            validate_manifest([])  # type: ignore[arg-type]

    def test_json_parser_rejects_duplicate_keys(self):
        raw = json.dumps(valid_manifest()).replace(
            '"package":"ANNarchy"', '"package":"ANNarchy","package":"ANNarchy"'
        ).encode()
        with self.assertRaises(ManifestError):
            parse_manifest_json(raw)

    def test_json_parser_rejects_non_finite_numbers(self):
        raw = json.dumps(valid_manifest()).replace('"threads": 4', '"threads": NaN')
        with self.assertRaises(ManifestError):
            parse_manifest_json(raw)

    def test_json_parser_rejects_invalid_utf8(self):
        with self.assertRaises(ManifestError):
            parse_manifest_json(b"\\xff")

    def test_json_parser_rejects_oversized_input(self):
        with self.assertRaises(ManifestError):
            parse_manifest_json(b" " * (MAX_INPUT_BYTES + 1))

    def test_json_parser_rejects_non_bytes_non_text(self):
        with self.assertRaises(ManifestError):
            parse_manifest_json({"schema": "bad"})  # type: ignore[arg-type]

    def test_canonical_json_is_ascii_and_stable(self):
        left = canonical_json(valid_manifest())
        right = canonical_json(copy.deepcopy(valid_manifest()))
        self.assertEqual(left, right)
        self.assertEqual(left.decode("ascii"), left.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
