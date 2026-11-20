# Mesh R0.1 — canonical identifier QA

This branch is an **independent QA fix for PR #16**, not a connected or authenticated mesh implementation.

## Reproduction on unmodified PR #16
Seven original unit tests pass, but the contract accepts a peer ID consisting only of whitespace or control characters (including a NUL or newline), and malformed capability path tokens like `../secret`, `cpu..gpu`, `/`, `cpu//gpu`. In each case the original function returns a negotiated capability if two offers share the malformed token and the caller supplies `authenticated=True, enrolled=True`. The problem is contract ambiguity, **not a demonstrated network intrusion**.

## Corrected schema rules
- Node ID: 1–128 ASCII characters, segments of ASCII letters/digits separated by exactly one of `.`, `_`, `:`, `-`; no control characters, Unicode, whitespace, slashes, leading/trailing separators or repeated separators. Identifiers are case-sensitive.
- Capability: 1–128 ASCII lowercase characters, each alphanumeric segment may contain single `-` or `_` separators; use `.` or `/` only between valid segments. Examples: `cpu`, `memory.read`, `physics.mujoco`, `net/tcp`, `gpu_cuda`.
- Unknown or malformed tokens fail closed with `ValueError`. Duplicate capability detection and intersection ordering retained.
- Existing `authenticated` and `enrolled` boolean checks remain unchanged. **Booleans do not prove identity, trusted enrollment, authorization or secure transport.** A future production mesh must verify signed node identity *and bind* each offer to that verified identity.

## Verification
Run from repository root: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_mesh_contract.py' -v`. The 7 original tests and 6 added QA tests pass locally on Python 3.13 (13 total); GitHub CI is configured for Python 3.11/3.12/3.13. No network access, key material, production nodes, GPU, service restarts or identity changes.
