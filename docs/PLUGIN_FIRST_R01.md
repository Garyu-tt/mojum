# Plugin-first development R01 (migration proposal)
 
Goal: no merge from main into plugin branches solely because main moved.
The existing main acceptance policy remains in force throughout migration.

* main: core, stable versioned contract, plugin registry and trusted acceptance policy.
* plugin/<slug>: stable independent module line. Branch contents should be limited to the plugin, its tests and minimal CI scaffolding.
* dev/<slug>/<task>: short-lived branch based on plugin/<slug>; target that plugin only.
* Each plugin release is pinned by commit SHA, supported core API range and dependency pins; the future core loader must verify these and roll back incompatible releases. Do not merge plugin branches back into main as a release mechanism.
* Core CI must test all currently supported plugin contract versions against the proposed core. Plugin CI must test the proposed merge against a pinned core release, not moving main.
* Scope, dependency/contract and module tests must be required checks authored and controlled by maintainers. Do not trust a PR to define its own acceptance policy or silently modify tests/workflows. Review actual changes; run PR code only on isolated unprivileged runners.
* Existing PRs remain intact. Classify first, migrate only reviewed plugin-local changes, leave cross-cutting changes on the current main gate.

## Dispatcher (implemented in this PR)
 
From a trusted checkout, with authenticated gh and Python 3.11+:
 
    python tools/pfd_accept.py
    python tools/pfd_accept.py --apply
 
Default is report-only. Empty allowlist intentionally means no merges.
For each independently configured plugin, the script enumerates open PRs, requires exact successful workflow/job names, approved independent review, clean mergeability, same-repository dev/<slug>/... origin, and verified strict branch protection (trusted app IDs, stale approval dismissal, last-push approval, no admin bypass or force pushes/deletion). The server enforces the protected base at merge time; the CLI pins the head. No admin override, no automatic branch updates, no PR code execution. This deliberately does not modify main's existing merge gate. It does not replace independent review or prove check-run provenance by itself. A failed or blocked item does not prevent examination of later PRs.
 
Example trusted acceptance config shape (illustrative IDs only, do not copy them):
 
    {"repository":"Garyu-tt/mojum","plugins":{"demo":{"checks":{"Plugin CI / contract":123,"Plugin CI / scope":123}}}}
 
Before adding any real entry: independently verify actual GitHub App IDs and workflow provenance, configure and audit branch protection and trusted scope/contract jobs; verify on a test branch with dry run. A ruleset-only branch is currently unsupported and will fail closed if classic protection cannot be verified. The script is not scheduled by this PR.
 
## Pending rollout
 
1. Select a small genuinely separable pilot and publish its versioned API.
2. Implement pinned release registry and core compatibility CI.
3. Create its protected branch, trusted scope/contract checks, and populate the allowlist.
4. Exercise dry run, independent review and one guarded merge before enabling periodic execution.
5. Gradually migrate the backlog without rebasing every plugin on main.
 
Tests: python -m unittest discover -s tests -p test_pfd_accept.py
