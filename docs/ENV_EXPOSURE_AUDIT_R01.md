# Offline environment exposure audit R0.1

Issue: #1152, parent #11.

`mojum.env_exposure_audit.audit_environment_names(names)` checks **names only**.
It never reads `os.environ`, file contents, or environment variable values.

- Max 1024 names; max 128 characters per name.
- Strict ASCII shell-style identifier syntax.
- Detects exact duplicates and case-fold collisions (important across hosts).
- Flags names containing sensitive marker segments (TOKEN, SECRET, PASSWORD,
  PASSWD, API_KEY, PRIVATE_KEY, ACCESS_KEY, CREDENTIALS, AUTH_KEY).
- Returns deterministic `mojum.env-exposure-audit/1` JSON-compatible findings.
- Invalid input fails closed with `TypeError` or `ValueError`.

This is advisory preflight, not proof that secrets are exposed. False positives
and false negatives are possible; values are intentionally never inspected.
No launch behavior or existing runtime contracts are modified.

Run: `PYTHONPATH=src python -m unittest discover -s tests -p test_env_exposure_audit.py`
