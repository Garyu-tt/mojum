"""Offline stdlib-only contract/failure tests for optional ONNX CPU shadow (#757)."""
import hashlib
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from mojum.onnx_cpu_shadow import (
    SCHEMA, ORT_VERSION, ShadowInferenceError, ShadowSpec, create_shadow_runner,
)


class FakeSession:
    providers = ["CPUExecutionProvider"]
    input_shape = [1, 3]
    output_shape = [1, 2]
    result = [[0.25, -0.5]]
    fallback_disabled = False

    def disable_fallback(self):
        self.fallback_disabled = True

    def get_providers(self):
        return self.providers

    def get_inputs(self):
        return [SimpleNamespace(name="input", type="tensor(float)", shape=self.input_shape)]

    def get_outputs(self):
        return [SimpleNamespace(name="scores", type="tensor(float)", shape=self.output_shape)]

    def run(self, outputs, inputs):
        assert self.fallback_disabled and outputs == ["scores"] and "input" in inputs
        return [self.result]


class FakeORT:
    __version__ = ORT_VERSION
    SessionOptions = type("Opts", (), {})
    ExecutionMode = SimpleNamespace(ORT_SEQUENTIAL="sequential")
    GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_BASIC="basic")
    session = FakeSession()

    @classmethod
    def InferenceSession(cls, model, *, sess_options, providers):
        assert isinstance(model, bytes) and providers == ["CPUExecutionProvider"]
        assert sess_options.intra_op_num_threads == 1
        assert sess_options.inter_op_num_threads == 1
        cls.session = FakeSession()
        return cls.session


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model = Path(self.tmp.name) / "fixture.onnx"
        self.model.write_bytes(b"PUBLIC SYNTHETIC TEST BYTES, NOT A REAL ONNX MODEL")
        self.spec = ShadowSpec(SCHEMA, hashlib.sha256(self.model.read_bytes()).hexdigest(), "input", "scores", 3, 2)

    def load(self, spec=None, path=None):
        return create_shadow_runner(path or self.model, spec or self.spec, enabled=True,
                                    runtime=FakeORT, tensor_factory=lambda x: x)

    def test_shadow_contract(self):
        x = self.load().predict([1, 2.5, -3], source_classification="PUBLIC_SYNTHETIC")
        self.assertEqual(x["mode"], "SHADOW_ONLY")
        self.assertEqual(x["scores"], (0.25, -0.5))
        self.assertEqual(x["model_sha256"], self.spec.model_sha256)

    def test_default_disabled(self):
        with self.assertRaises(ShadowInferenceError):
            create_shadow_runner(self.model, self.spec, runtime=FakeORT, tensor_factory=lambda x: x)

    def test_version_pin(self):
        with self.assertRaises(ShadowInferenceError):
            self.load(ShadowSpec(SCHEMA, self.spec.model_sha256, "input", "scores", 3, 2, "0.0.1"))

    def test_bad_digest(self):
        with self.assertRaisesRegex(ShadowInferenceError, "SHA-256"):
            self.load(ShadowSpec(SCHEMA, "0" * 64, "input", "scores", 3, 2))

    def test_model_mutated(self):
        self.model.write_bytes(b"SPOOFED MODEL")
        with self.assertRaises(ShadowInferenceError):
            self.load()

    def test_symlink_rejected(self):
        alias = Path(self.tmp.name) / "alias.onnx"
        try:
            alias.symlink_to(self.model)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ShadowInferenceError):
            self.load(path=alias)

    def test_bad_shapes(self):
        original = FakeSession.input_shape
        try:
            FakeSession.input_shape = [None, 3]
            with self.assertRaisesRegex(ShadowInferenceError, "input tensor"):
                self.load()
        finally:
            FakeSession.input_shape = original

    def test_bad_output_shape(self):
        original = FakeSession.output_shape
        try:
            FakeSession.output_shape = [1, 3]
            with self.assertRaises(ShadowInferenceError):
                self.load()
        finally:
            FakeSession.output_shape = original

    def test_provider_fail_closed(self):
        original = FakeSession.providers
        try:
            FakeSession.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            with self.assertRaisesRegex(ShadowInferenceError, "provider"):
                self.load()
        finally:
            FakeSession.providers = original

    def test_private_rejected(self):
        runner = self.load()
        with self.assertRaisesRegex(ShadowInferenceError, "source not admitted"):
            runner.predict([0, 0, 0], source_classification="PRIVATE")

    def test_nonfinite_input_rejected(self):
        runner = self.load()
        for features in ([float("nan"), 0, 0], [float("inf"), 0, 0], [True, 0, 0], [1001, 0, 0]):
            with self.subTest(features=features), self.assertRaises(ShadowInferenceError):
                runner.predict(features, source_classification="PUBLIC_SYNTHETIC")

    def test_huge_integer_inputs_fail_closed(self):
        # math.isfinite(10**1000) raises OverflowError on CPython.
        # The public boundary must raise the documented rejection instead.
        runner = self.load()
        for value in (10**1000, -(10**1000)):
            with self.subTest(value_sign=value > 0):
                with self.assertRaisesRegex(ShadowInferenceError, "unsafe input value"):
                    runner.predict([value, 0, 0], source_classification="PUBLIC_SYNTHETIC")

    def test_huge_integer_outputs_fail_closed(self):
        original = FakeSession.result
        try:
            for value in (10**1000, -(10**1000)):
                with self.subTest(value_sign=value > 0):
                    FakeSession.result = [[value, 0]]
                    with self.assertRaisesRegex(ShadowInferenceError, "unsafe model output"):
                        self.load().predict([0, 0, 0], source_classification="PUBLIC_SYNTHETIC")
        finally:
            FakeSession.result = original

    def test_bad_input_width_rejected(self):
        with self.assertRaises(ShadowInferenceError):
            self.load().predict([1, 2], source_classification="PUBLIC_SYNTHETIC")

    def test_nonfinite_output_rejected(self):
        original = FakeSession.result
        try:
            FakeSession.result = [[float("nan"), 0]]
            with self.assertRaisesRegex(ShadowInferenceError, "unsafe model output"):
                self.load().predict([0, 0, 0], source_classification="PUBLIC_SYNTHETIC")
        finally:
            FakeSession.result = original

    def test_wrong_output_dimensions_rejected(self):
        original = FakeSession.result
        try:
            FakeSession.result = [[0.1]]
            with self.assertRaisesRegex(ShadowInferenceError, "dimensions"):
                self.load().predict([0, 0, 0], source_classification="PUBLIC_SYNTHETIC")
        finally:
            FakeSession.result = original

    def test_reproducible_fake_replay(self):
        runner = self.load()
        a = runner.predict([1, 2, 3], source_classification="PUBLIC_SYNTHETIC")
        b = runner.predict([1, 2, 3], source_classification="PUBLIC_SYNTHETIC")
        self.assertEqual(a, b)


    def test_non_native_wire_discriminators_rejected_before_equality(self):
        class ForgedLabel:
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        class ForgedString(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        runner = self.load()
        for forged in (ForgedLabel(), ForgedString("PRIVATE")):
            with self.subTest(kind=type(forged).__name__):
                with self.assertRaisesRegex(ShadowInferenceError, "source not admitted"):
                    runner.predict([0, 0, 0], source_classification=forged)
                for field in ("schema", "runtime_version"):
                    with self.subTest(field=field):
                        with self.assertRaisesRegex(ShadowInferenceError, "unsupported shadow"):
                            replace(self.spec, **{field: forged}).validate()

    def test_hostile_comparison_callbacks_are_not_invoked(self):
        class ExplosiveLabel:
            def __eq__(self, other):
                raise RuntimeError("untrusted equality callback")

            def __ne__(self, other):
                raise RuntimeError("untrusted inequality callback")

        forged = ExplosiveLabel()
        for field in ("schema", "runtime_version"):
            with self.subTest(field=field):
                with self.assertRaises(ShadowInferenceError):
                    replace(self.spec, **{field: forged}).validate()
        with self.assertRaises(ShadowInferenceError):
            self.load().predict([0, 0, 0], source_classification=forged)

    def test_manifest_and_runtime_pin_require_native_strings(self):
        class StrSubclass(str):
            pass

        for field, value in (
            ("model_sha256", StrSubclass(self.spec.model_sha256)),
            ("input_name", StrSubclass("input")),
            ("output_name", StrSubclass("scores")),
            ("schema", StrSubclass(SCHEMA)),
            ("runtime_version", StrSubclass(ORT_VERSION)),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ShadowInferenceError):
                    replace(self.spec, **{field: value}).validate()

        class RuntimeWithSpoofedVersion(FakeORT):
            __version__ = StrSubclass(ORT_VERSION)

        with self.assertRaisesRegex(ShadowInferenceError, "runtime version differs"):
            create_shadow_runner(self.model, self.spec, enabled=True,
                                 runtime=RuntimeWithSpoofedVersion,
                                 tensor_factory=lambda x: x)

    def test_invalid_manifest(self):
        for width in (True, 0, 513):
            with self.subTest(width=width), self.assertRaises(ShadowInferenceError):
                self.load(ShadowSpec(SCHEMA, self.spec.model_sha256, "input", "scores", width, 2))


if __name__ == "__main__":
    unittest.main()
