# QA exact-head workflow evidence R0.1

Pure, opt-in admission helper for caller-supplied GitHub Actions workflow-run snapshots. It checks whether each required workflow ID has a latest successful `pull_request` run for exactly the requested PR HEAD SHA.

## Contract
- Python standard library only; no SDK, network, SQLite, scheduler or runtime wiring.
- Caller supplies a bounded list of exact-field run records and a non-empty workflow-ID allowlist.
- Latest attempt is selected by attempt number, then run ID. A newer pending attempt supersedes an older success.
- Only `status=completed` plus `conclusion=success` passes. Failure, cancellation, pending, missing and stale-head evidence produce HOLD.
- Empty evidence is HOLD. Stale-head runs never satisfy a requirement.
- Receipt is sorted and detached. SHA-256 is a reproducibility witness, not authentication.

## Usage
```python
from mojum.qa_workflow_evidence import evaluate_workflow_evidence
receipt = evaluate_workflow_evidence(workflow_runs, expected_head_sha='0123456789abcdef0123456789abcdef01234567', required_workflow_ids=[12345, 67890])
if receipt.state != 'PASS':
    raise RuntimeError('exact-head CI evidence is incomplete')
```

## Limits
This module does not fetch runs, prove snapshot authenticity, establish branch protection, inspect required-check policy, validate source contents or authorize merges. A trusted caller must fetch complete Actions workflow-run data and separately enforce freshness, permissions and release policy. Empty commit-status responses and LLM statements are never evidence.

## Reproduction
```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_qa_workflow_evidence.py' -v
```
