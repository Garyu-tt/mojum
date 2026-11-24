# QA lifecycle transition policy R0.1

This is an opt-in, pure-stdlib validator for proposed supervisor lifecycle transitions. It is not a scheduler, queue, CI client, reviewer, authorization service, or merge executor.

## Contract

- States and edges are explicit; invalid edges and transitions out of MERGED are rejected.
- CLAIMED, REVIEWING, TESTING, RETESTING, READY and blocked/escalated states require typed evidence.
- CI evidence must name the exact current 40-character lowercase HEAD SHA.
- READY requires CI-head, review identifier and base SHA.
- MERGED requires a separately supplied `mojum.release-receipt/1` object with matching HEAD, CI pass and explicit human-authorization booleans. This validator does not authenticate that receipt or verify GitHub state; it only validates shape and consistency. It does not perform or authorize a merge.
- Inputs are bounded and strict; returned evidence is detached and immutable; digest is a consistency hash, not a signature.

## Usage

```python
from mojum.qa_lifecycle import propose_transition

proposal = propose_transition(
    "DISCOVERED", "CLAIMED", reason="worker accepted",
    evidence={"worker_id": "worker-1"},
)
print(proposal.digest)
```

## Verification

Run: `PYTHONPATH=src python -m unittest tests.test_qa_lifecycle -v`

Python 3.11–3.13; standard library only. No network, filesystem, private Noosphere records, credentials, model calls, or runtime wiring.
