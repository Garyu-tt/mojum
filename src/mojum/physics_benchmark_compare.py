"""Strict solver-neutral comparison of bounded PUBLIC_SYNTHETIC physics runs."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math, re
from typing import Any

SCHEMA = "mojum.physics-benchmark/1"
COMPARISON_SCHEMA = "mojum.physics-benchmark-comparison/1"
MAX_REPORT_BYTES = 256 * 1024
MAX_STEPS = 10_000
MAX_WARMUP_STEPS = 100_000
MAX_TIME_NS = 10**12
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,63}$")
_FIELDS = {"schema", "visibility", "workload_sha256", "rig_sha256", "solver",
           "model_sha256", "seed", "timestep_ns", "warmup_steps",
           "measured_steps", "platform", "step_time_ns"}
_PLATFORM = {"os", "arch", "python", "cpu_class"}
_COMPAT = ("workload_sha256", "rig_sha256", "seed", "timestep_ns",
           "warmup_steps", "measured_steps", "platform")


class BenchmarkReportError(ValueError):
    """Malformed, over-budget, or incomparable benchmark report."""


@dataclass(frozen=True)
class _Validated:
    data: dict[str, Any]
    digest: str
    median_num: int
    median_den: int
    p95_ns: int


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _token(value: Any) -> bool:
    return type(value) is str and 1 <= len(value) <= 64 and _TOKEN.fullmatch(value) is not None


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise BenchmarkReportError("report is not canonical JSON data") from exc


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkReportError("duplicate JSON object key")
        result[key] = value
    return result


def load_report_json(payload: str | bytes) -> dict[str, Any]:
    """Parse bounded JSON, refusing duplicate keys, invalid UTF-8 and NaN."""
    if type(payload) is bytes:
        if len(payload) > MAX_REPORT_BYTES:
            raise BenchmarkReportError("report exceeds byte budget")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BenchmarkReportError("report is not UTF-8") from exc
    elif type(payload) is str:
        if len(payload) > MAX_REPORT_BYTES:
            raise BenchmarkReportError("report exceeds byte budget")
        try:
            encoded = payload.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise BenchmarkReportError("report is not valid UTF-8 text") from exc
        if len(encoded) > MAX_REPORT_BYTES:
            raise BenchmarkReportError("report exceeds byte budget")
        text = payload
    else:
        raise BenchmarkReportError("payload must be exact str or bytes")

    def reject_constant(_value: str) -> None:
        raise BenchmarkReportError("non-finite JSON number is forbidden")
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_pairs,
                            parse_constant=reject_constant)
    except BenchmarkReportError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise BenchmarkReportError("invalid JSON report") from exc
    if type(parsed) is not dict:
        raise BenchmarkReportError("report root must be an object")
    return parsed


def _validate(report: Any) -> _Validated:
    if (type(report) is not dict or any(type(key) is not str for key in report)
            or set(report) != _FIELDS):
        raise BenchmarkReportError("report fields do not match schema")
    if (type(report["schema"]) is not str or len(report["schema"]) != len(SCHEMA)
            or type(report["visibility"]) is not str
            or len(report["visibility"]) != len("PUBLIC_SYNTHETIC")
            or report["schema"] != SCHEMA
            or report["visibility"] != "PUBLIC_SYNTHETIC"):
        raise BenchmarkReportError("unsupported schema or visibility")
    for name in ("workload_sha256", "rig_sha256", "model_sha256"):
        value = report[name]
        if type(value) is not str or len(value) != 64 or _HEX.fullmatch(value) is None:
            raise BenchmarkReportError("invalid " + name)
    if not _token(report["solver"]):
        raise BenchmarkReportError("invalid solver identifier")
    if not _integer(report["seed"], 0, 2**31 - 1):
        raise BenchmarkReportError("invalid seed")
    if not _integer(report["timestep_ns"], 1, 1_000_000_000):
        raise BenchmarkReportError("invalid timestep_ns")
    if not _integer(report["warmup_steps"], 0, MAX_WARMUP_STEPS):
        raise BenchmarkReportError("invalid warmup_steps")
    count = report["measured_steps"]
    if not _integer(count, 3, MAX_STEPS):
        raise BenchmarkReportError("measured_steps must be in 3..10000")
    platform = report["platform"]
    if (type(platform) is not dict or any(type(key) is not str for key in platform)
            or set(platform) != _PLATFORM):
        raise BenchmarkReportError("platform fields do not match schema")
    for name in _PLATFORM:
        if not _token(platform[name]):
            raise BenchmarkReportError("invalid platform." + name)
    samples = report["step_time_ns"]
    if type(samples) is not list or len(samples) != count:
        raise BenchmarkReportError("sample count must equal measured_steps")
    ordered = []
    for sample in samples:
        if not _integer(sample, 1, MAX_TIME_NS):
            raise BenchmarkReportError("step_time_ns contains invalid sample")
        ordered.append(sample)
    ordered.sort()
    middle = count // 2
    if count % 2:
        median_num, median_den = ordered[middle], 1
    else:
        median_num, median_den = ordered[middle - 1] + ordered[middle], 2
    divisor = math.gcd(median_num, median_den)
    median_num //= divisor
    median_den //= divisor
    p95_index = (95 * count + 99) // 100 - 1
    canonical = _canonical(report)
    # Retain a detached JSON-native snapshot, not the caller's mutable mapping.
    snapshot = json.loads(canonical.decode("utf-8"))
    return _Validated(snapshot, hashlib.sha256(canonical).hexdigest(),
                      median_num, median_den, ordered[p95_index])


def _ratio(candidate: _Validated, baseline: _Validated) -> tuple[int, int]:
    numerator = candidate.median_num * baseline.median_den
    denominator = candidate.median_den * baseline.median_num
    divisor = math.gcd(numerator, denominator)
    return numerator // divisor, denominator // divisor


def compare_reports(baseline_report: Any, candidate_report: Any) -> dict[str, Any]:
    """Compare reports with identical workload/rig/execution envelopes.

    A candidate/baseline median ratio below one means a lower declared median.
    This is arithmetic over supplied samples, not solver attestation.
    """
    baseline, candidate = _validate(baseline_report), _validate(candidate_report)
    for name in _COMPAT:
        if baseline.data[name] != candidate.data[name]:
            raise BenchmarkReportError("incomparable reports: " + name + " differs")
    ratio_num, ratio_den = _ratio(candidate, baseline)
    result = {
        "schema": COMPARISON_SCHEMA,
        "visibility": "PUBLIC_SYNTHETIC",
        "comparable": True,
        "workload_sha256": baseline.data["workload_sha256"],
        "rig_sha256": baseline.data["rig_sha256"],
        "seed": baseline.data["seed"],
        "timestep_ns": baseline.data["timestep_ns"],
        "measured_steps": baseline.data["measured_steps"],
        "baseline": {
            "solver": baseline.data["solver"],
            "report_sha256": baseline.digest,
            "median_step_time_ns": {"numerator": baseline.median_num,
                                    "denominator": baseline.median_den},
            "p95_step_time_ns": baseline.p95_ns,
        },
        "candidate": {
            "solver": candidate.data["solver"],
            "report_sha256": candidate.digest,
            "median_step_time_ns": {"numerator": candidate.median_num,
                                    "denominator": candidate.median_den},
            "p95_step_time_ns": candidate.p95_ns,
        },
        "candidate_over_baseline_median": {"numerator": ratio_num,
                                           "denominator": ratio_den},
        "interpretation": "caller-supplied timing samples only; not engine attestation",
    }
    result["comparison_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def compare_json_reports(baseline_payload: str | bytes,
                         candidate_payload: str | bytes) -> dict[str, Any]:
    """Parse and compare two bounded JSON reports."""
    return compare_reports(load_report_json(baseline_payload),
                           load_report_json(candidate_payload))
