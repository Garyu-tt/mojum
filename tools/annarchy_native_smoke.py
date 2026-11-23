"""Bounded disposable-process runner for the optional ANNarchy native smoke.

This is an opt-in research harness, not a security sandbox. OS resource limits
are applied on POSIX; network isolation must be supplied by the CI/container
boundary and is not implemented by this Python module.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None

SCHEMA = "mojum.annarchy-native-smoke/1"
PROBE_SCHEMA = "mojum.neural.rate-shadow-probe/1"
MAX_WALL_SECONDS = 180
MAX_CPU_SECONDS = 120
MAX_ADDRESS_SPACE_BYTES = 3 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024
MAX_FRAMES = 64
DEFAULT_TOLERANCE = 1e-5


class SmokeError(ValueError):
    """Native smoke could not be safely started or validated."""


def _limits(cpu_seconds: int, memory_bytes: int, file_bytes: int) -> None:
    """Child pre-exec hook. Only POSIX resource limits are enforceable here."""
    if resource is None:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    if hasattr(resource, "RLIMIT_CORE"):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if hasattr(resource, "RLIMIT_NOFILE"):
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _environment(home: str) -> dict[str, str]:
    # Do not inherit API keys, cloud credentials, proxy settings or arbitrary
    # user configuration from the invoking process.
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    root = Path(__file__).resolve().parents[1]
    env.update({
        "HOME": home,
        "USERPROFILE": home,
        "TMPDIR": home,
        "TEMP": home,
        "TMP": home,
        "PYTHONPATH": str(root / "src"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CUDA_VISIBLE_DEVICES": "",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    })
    return env


def _child_code(frames: int) -> str:
    # Only a bounded integer is interpolated; all simulation data is synthetic.
    return (
        "import json\n"
        "from mojum.annarchy_rate_probe import RateSpec, compare_rate_traces\n"
        f"frames = [[(t + n * 3) % 11 / 10 for n in range(2)] for t in range({frames})]\n"
        "result = compare_rate_traces(frames, spec=RateSpec(neurons=2, dt_ms=0.5, tau_ms=10.0), "
        "backend='annarchy', enabled=True, tolerance=1e-5)\n"
        "print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))\n"
    )


def _read_bounded(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    if size > limit:
        raise SmokeError("native smoke output exceeded its byte budget")
    return path.read_bytes()


def _validate_result(raw: bytes, *, frames: int, tolerance: float) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_OUTPUT_BYTES:
        raise SmokeError("invalid native smoke output size")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise SmokeError("native smoke did not emit valid JSON") from exc
    if type(value) is not dict:
        raise SmokeError("native smoke result must be an object")
    required = {
        "schema", "mode", "backend", "tick_count", "neurons", "dt_ms",
        "tau_ms", "input_sha256", "reference_sha256", "observed_sha256",
        "final_rates", "max_abs_error", "motor_intention", "physical_contact",
    }
    if set(value) != required:
        raise SmokeError("native smoke result fields do not match the contract")
    if value["schema"] != PROBE_SCHEMA or value["mode"] != "shadow" or value["backend"] != "annarchy":
        raise SmokeError("native smoke result identity mismatch")
    if type(value["tick_count"]) is not int or value["tick_count"] != frames:
        raise SmokeError("native smoke frame count mismatch")
    if type(value["neurons"]) is not int or value["neurons"] != 2:
        raise SmokeError("native smoke neuron count mismatch")
    for key in ("dt_ms", "tau_ms", "max_abs_error"):
        item = value[key]
        if type(item) not in (int, float) or not math.isfinite(item):
            raise SmokeError("native smoke contains a non-finite metric")
    if value["dt_ms"] != 0.5 or value["tau_ms"] != 10.0:
        raise SmokeError("native smoke timing contract mismatch")
    if value["max_abs_error"] > tolerance:
        raise SmokeError("native ANNarchy trace exceeded the error tolerance")
    if type(value["final_rates"]) is not list or len(value["final_rates"]) != 2:
        raise SmokeError("native smoke final-state shape mismatch")
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in value["final_rates"]):
        raise SmokeError("native smoke final state is invalid")
    for key in ("input_sha256", "reference_sha256", "observed_sha256"):
        digest = value[key]
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise SmokeError("native smoke digest is malformed")
    if value["motor_intention"] is not None or value["physical_contact"] is not False:
        raise SmokeError("native smoke attempted to report authority or physical contact")
    return value


def run_native_smoke(*, wall_seconds: int = 90, cpu_seconds: int = 60,
                     memory_bytes: int = 2 * 1024 * 1024 * 1024,
                     frames: int = 32) -> dict[str, Any]:
    """Run pinned ANNarchy in a credential-stripped child with bounded resources."""
    if type(wall_seconds) is not int or not 1 <= wall_seconds <= MAX_WALL_SECONDS:
        raise SmokeError("wall-time budget is outside allowed bounds")
    if type(cpu_seconds) is not int or not 1 <= cpu_seconds <= MAX_CPU_SECONDS:
        raise SmokeError("CPU budget is outside allowed bounds")
    if type(memory_bytes) is not int or not 256 * 1024 * 1024 <= memory_bytes <= MAX_ADDRESS_SPACE_BYTES:
        raise SmokeError("address-space budget is outside allowed bounds")
    if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
        raise SmokeError("frame count is outside allowed bounds")
    if os.name != "posix":
        raise SmokeError("native resource limits require a POSIX runner")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mojum-annarchy-smoke-") as tmp:
        work = Path(tmp)
        stdout_path, stderr_path = work / "stdout.json", work / "stderr.log"
        env = _environment(str(work))
        kwargs: dict[str, Any] = {}
        if resource is not None:
            kwargs["preexec_fn"] = lambda: _limits(cpu_seconds, memory_bytes, MAX_FILE_BYTES)
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-c", _child_code(frames)],
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    cwd=str(work), env=env, close_fds=True, start_new_session=True,
                    **kwargs,
                )
            except OSError as exc:
                raise SmokeError("could not start disposable native process") from exc
            try:
                return_code = process.wait(timeout=wall_seconds)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (AttributeError, ProcessLookupError, PermissionError):
                    process.kill()
                process.wait()
                raise SmokeError("native compile/run exceeded wall-time budget") from exc
        stdout_raw = _read_bounded(stdout_path, MAX_OUTPUT_BYTES)
        # Stderr is deliberately not surfaced because third-party compilers may
        # echo paths or environment details. Only its bounded size is checked.
        _read_bounded(stderr_path, MAX_OUTPUT_BYTES)
        if return_code != 0:
            raise SmokeError(f"native process failed with exit status {return_code}")
        result = _validate_result(stdout_raw, frames=frames, tolerance=DEFAULT_TOLERANCE)
    return {
        "schema": SCHEMA,
        "status": "pass",
        "backend": "ANNarchy",
        "version": "5.0.4.1",
        "frames": frames,
        "wall_seconds": round(time.monotonic() - started, 6),
        "resource_limits": {
            "cpu_seconds": cpu_seconds,
            "address_space_bytes": memory_bytes,
            "per_file_bytes": MAX_FILE_BYTES,
        },
        "result": result,
        "network_isolation": "provided-by-runner-not-this-module",
        "physical_contact": False,
        "motor_intention": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=32)
    parser.add_argument("--wall-seconds", type=int, default=90)
    parser.add_argument("--cpu-seconds", type=int, default=60)
    parser.add_argument("--memory-mib", type=int, default=2048)
    args = parser.parse_args(argv)
    try:
        result = run_native_smoke(
            frames=args.frames, wall_seconds=args.wall_seconds,
            cpu_seconds=args.cpu_seconds, memory_bytes=args.memory_mib * 1024 * 1024,
        )
    except SmokeError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
