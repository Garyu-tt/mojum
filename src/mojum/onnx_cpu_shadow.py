"""Opt-in, strictly shadow-only ONNX Runtime CPU inference boundary (#757).

Never writes to journals or dispatches motor actions. Models must be trusted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import importlib
import os
from pathlib import Path
import re
import stat

SCHEMA = "mojum.onnx-cpu-shadow/1"
ORT_VERSION = "1.32.0"
MAX_MODEL_BYTES = 16 * 1024 * 1024
MAX_FEATURES = 512
MAX_OUTPUTS = 64
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9.]{0,63}\Z")


class ShadowInferenceError(ValueError):
    """Fail-closed validation or unavailable optional backend."""


@dataclass(frozen=True)
class ShadowSpec:
    schema: str
    model_sha256: str
    input_name: str
    output_name: str
    features: int
    outputs: int
    runtime_version: str = ORT_VERSION

    def validate(self) -> None:
        if (type(self.schema) is not str or self.schema != SCHEMA
                or type(self.runtime_version) is not str or self.runtime_version != ORT_VERSION):
            raise ShadowInferenceError("unsupported shadow schema or runtime pin")
        if type(self.model_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.model_sha256):
            raise ShadowInferenceError("invalid model digest")
        if not all(type(s) is str and _NAME.fullmatch(s) for s in (self.input_name, self.output_name)):
            raise ShadowInferenceError("invalid tensor name")
        if type(self.features) is not int or not (1 <= self.features <= MAX_FEATURES):
            raise ShadowInferenceError("invalid feature budget")
        if type(self.outputs) is not int or not (1 <= self.outputs <= MAX_OUTPUTS):
            raise ShadowInferenceError("invalid output budget")


def _verified_model_bytes(path: str | Path, expected_sha256: str) -> bytes:
    p = Path(path)
    if p.suffix != ".onnx" or p.is_symlink():
        raise ShadowInferenceError("model must be a regular non-symlink .onnx file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(p, flags)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not (1 <= before.st_size <= MAX_MODEL_BYTES):
                raise ShadowInferenceError("model size or type not permitted")
            data = stream.read(MAX_MODEL_BYTES + 1)
            after = os.fstat(stream.fileno())
    except (OSError, ValueError) as exc:
        if isinstance(exc, ShadowInferenceError):
            raise
        raise ShadowInferenceError("model unavailable or changed") from exc
    if len(data) != before.st_size or len(data) > MAX_MODEL_BYTES or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ShadowInferenceError("model changed while reading")
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_sha256):
        raise ShadowInferenceError("model SHA-256 mismatch")
    return data


def _bounded_real(value: object, limit: int) -> bool:
    """Fail closed on NaN, infinity and huge ints without float conversion.

    math.isfinite(10**1000) raises OverflowError; bounded native comparisons
    instead reject extreme integers without bypassing ShadowInferenceError.
    """
    return type(value) in (int, float) and -limit <= value <= limit


def _metadata_matches(items: object, name: str, width: int) -> bool:
    if not isinstance(items, (list, tuple)) or len(items) != 1:
        return False
    item = items[0]
    return (getattr(item, "name", None) == name
            and getattr(item, "type", None) == "tensor(float)"
            and getattr(item, "shape", None) == [1, width])


class ShadowRunner:
    def __init__(self, spec: ShadowSpec, session: object, tensor_factory: object):
        self.spec = spec
        self._session = session
        self._tensor_factory = tensor_factory

    def predict(self, features: object, *, source_classification: str) -> dict:
        """Only PUBLIC_SYNTHETIC vectors; output is NEVER a motor command."""
        if type(source_classification) is not str or source_classification != "PUBLIC_SYNTHETIC":
            raise ShadowInferenceError("source not admitted to offline shadow")
        if type(features) not in (list, tuple) or len(features) != self.spec.features:
            raise ShadowInferenceError("invalid input vector width")
        if any(not _bounded_real(v, 1000) for v in features):
            raise ShadowInferenceError("unsafe input value")
        try:
            tensor = self._tensor_factory([list(map(float, features))])
            raw = self._session.run([self.spec.output_name], {self.spec.input_name: tensor})
        except Exception as exc:
            raise ShadowInferenceError("shadow inference failed") from exc
        if type(raw) not in (list, tuple) or len(raw) != 1:
            raise ShadowInferenceError("unexpected output tensor count")
        output = raw[0]
        if hasattr(output, "shape") and list(output.shape) != [1, self.spec.outputs]:
            raise ShadowInferenceError("unexpected output dimensions")
        output = output.tolist() if hasattr(output, "tolist") else output
        if (type(output) is not list or len(output) != 1 or type(output[0]) is not list
                or len(output[0]) != self.spec.outputs):
            raise ShadowInferenceError("unexpected output dimensions")
        if any(not _bounded_real(v, 1_000_000) for v in output[0]):
            raise ShadowInferenceError("unsafe model output")
        return {"schema": SCHEMA, "mode": "SHADOW_ONLY", "provider": "CPUExecutionProvider",
                "model_sha256": self.spec.model_sha256, "scores": tuple(float(x) for x in output[0])}


def create_shadow_runner(model_path: str | Path, spec: ShadowSpec, *, enabled: bool = False,
                         runtime: object = None, tensor_factory: object = None) -> ShadowRunner:
    """Trusted local model only. Optional runtime injection supports offline tests."""
    if enabled is not True:
        raise ShadowInferenceError("shadow inference must be explicitly enabled")
    if type(spec) is not ShadowSpec:
        raise ShadowInferenceError("typed shadow manifest required")
    spec.validate()
    data = _verified_model_bytes(model_path, spec.model_sha256)
    if runtime is None:
        try:
            runtime = importlib.import_module("onnxruntime")
        except ImportError as exc:
            raise ShadowInferenceError("optional onnxruntime package unavailable") from exc
    runtime_version = getattr(runtime, "__version__", None)
    if type(runtime_version) is not str or runtime_version != spec.runtime_version:
        raise ShadowInferenceError("onnxruntime version differs from pin")
    if tensor_factory is None:
        try:
            np = importlib.import_module("numpy")
        except ImportError as exc:
            raise ShadowInferenceError("optional numpy package unavailable") from exc
        tensor_factory = lambda values: np.asarray(values, dtype=np.float32)
    try:
        opts = runtime.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = runtime.GraphOptimizationLevel.ORT_ENABLE_BASIC
        opts.enable_mem_pattern = False
        session = runtime.InferenceSession(data, sess_options=opts, providers=["CPUExecutionProvider"])
        session.disable_fallback()
        if session.get_providers() != ["CPUExecutionProvider"]:
            raise ShadowInferenceError("execution provider mismatch")
        if not _metadata_matches(session.get_inputs(), spec.input_name, spec.features):
            raise ShadowInferenceError("input tensor contract mismatch")
        if not _metadata_matches(session.get_outputs(), spec.output_name, spec.outputs):
            raise ShadowInferenceError("output tensor contract mismatch")
    except ShadowInferenceError:
        raise
    except Exception as exc:
        raise ShadowInferenceError("CPU shadow session initialization failed") from exc
    return ShadowRunner(spec, session, tensor_factory)
