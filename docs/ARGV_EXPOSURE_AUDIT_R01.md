# Offline argv exposure audit R01

Issue #1155, parent #11. This opt-in pure-stdlib advisory checker accepts a caller-supplied native list of PUBLIC_SYNTHETIC argument strings. It never inspects real process arguments, environment variables, files, network or a shell.

Usage:

```python
from mojum.argv_exposure_audit import audit_argv
report = audit_argv(["mojum", "--api-key=example"])
assert not report.safe
```

The immutable result includes only argument positions and finding categories, never argument contents. Its SHA-256 digest covers sanitized report metadata, **not input values**, and is not an authenticity signature. Limits: 256 arguments, 4096 characters per argument, 65536 UTF-8 bytes total. Exact native list/string types and PUBLIC_SYNTHETIC provenance are mandatory. Recognizes sensitive option names and basic URL userinfo. This is a heuristic, not a complete secret detector or authorization control. False positives and negatives are possible. No runtime wiring or process scanning is provided.

Run focused tests with `PYTHONPATH=src python -m unittest discover -s tests -p test_argv_exposure_audit.py`. Independent QA and exact-head CI are required before merge.
