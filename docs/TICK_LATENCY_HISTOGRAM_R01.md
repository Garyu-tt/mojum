# Offline tick latency histogram R01

Issue #893. This optional, read-only diagnostic accepts **only PUBLIC_SYNTHETIC** JSONL traces.
Each line must have exactly `tick` (strict increasing nonnegative integer),
`duration_us` (finite nonnegative numeric microseconds, maximum 1e9),
and `visibility: "PUBLIC_SYNTHETIC"`.

Run: `python tools/tick_latency_histogram.py synthetic.jsonl`.
Tests: `python -m unittest discover -s tests -p test_tick_latency_histogram.py`.

Output includes nearest-rank p50/p95/p99, min/max, and fixed non-overlapping
microsecond histogram buckets. Empty traces produce count zero and null percentiles.
The entire input is bounded to 100000 records, 16 MiB, 4096 bytes per line.
No PRIVATE journal access, live telemetry, dependencies, or network calls.
These are **measured supplied durations**, not proof of real-time performance.
