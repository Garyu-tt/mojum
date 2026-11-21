# Mojum Spartan pool R0.1: 300 addressable workers

The aim is **up to 300 concurrently leased tasks**, not 300 ChatGPT
scheduled tasks and not a claim that 300 model inference processes are live.

## Components

- **Dispatcher**: durable SQLite lease ledger in
  `tools/spartan_coordinator.py`. Runs on a designated coordinator host.
  One worker slot has at most one live task. A task has one owner.
  One functional scope has at most one active task, avoiding obvious file
  collisions. Work expires and is safely retried at most three times.
- **Model workers**: separate, sandboxed executors. Each lease carries an
  immutable task ID and a functional scope. The executor implements its own
  model provider, Git worktree, credentials, tests and draft PR submission.
  Those adapters are **not implemented by this R0.1 ledger**.
- **Independent acceptance**: reviewers fetch the actual PR diff and verify
  exact HEAD+BASE, CI, compatibility and policy in
  `docs/MERGE_ACCEPTANCE_POLICY.md`. Workers may submit a PR for review, but
  they cannot mark work accepted by calling the dispatcher.
- **Coordinator**: a single writer authority for leases. Never copy a live
  SQLite database between nodes or put it on a network share without a
  consistency protocol. Backup and failover need fencing before adding HA.

## Quick start (CPU-only, no credentials)

```sh
python3 tools/spartan_coordinator.py --db /var/lib/mojum/spartan.sqlite3 init --slots 300
python3 tools/spartan_coordinator.py --db /var/lib/mojum/spartan.sqlite3 enqueue --json tasks.json
python3 tools/spartan_coordinator.py --db /var/lib/mojum/spartan.sqlite3 claim --worker 1
python3 tools/spartan_coordinator.py --db /var/lib/mojum/spartan.sqlite3 heartbeat --worker 1 --task issue-123 --attempt 1
python3 tools/spartan_coordinator.py --db /var/lib/mojum/spartan.sqlite3 finish --worker 1 --task issue-123 --attempt 1 --result review --artifact "https://github.com/Garyu-tt/mojum/pull/123"
python3 tools/spartan_coordinator.py --db /var/lib/mojum/spartan.sqlite3 status
```

`claim` returns the task's `attempts` value. Supply that value as the mandatory
`--attempt` fencing token on every `heartbeat` or `finish`. Do **not** reuse
an old attempt after its lease expires, even if the same numbered worker slot
reclaims the task. This fencing token prevents stale processes from changing a
new lease; it is **not authentication** and must never replace worker identity
verification. A worker that exhausted all three attempts must finish `blocked`,
not requeue. On the next claim, the coordinator also quarantines exhausted
`queued` rows left by an older ledger instead of granting a fourth lease.
Re-enqueueing an existing task ID is idempotent only if its scope, priority and
payload match exactly; conflicting batches roll back atomically.

`tasks.json` is an array such as:
```json
[
  {"id": "issue-123", "scope": "aquarium.viewer", "priority": 10,
   "payload": {"issue": 123}},
  {"id": "issue-124", "scope": "noosphere.archive", "priority": 8,
   "payload": {"issue": 124}}
]
```

Run the test suite from the repository root:
```sh
python3 -m unittest discover -s tests -p 'test_spartan_coordinator.py' -v
```

## Explicit non-goals and activation gates

- 300 slots **do not** mean 300 model processes, API requests or successful
  code changes. The admin must provide providers, quotas, budget controls and
  a sandboxed worker fleet. Maintain `running_models`, `ready_workers`,
  request-rate and cost metrics separately from active leases.
- Do not place API keys or GitHub write tokens inside model prompts or in
  untrusted generated tests. Test candidate patches in separate credential-free
  environments and publish only validated, bounded artifacts as draft PRs.
- Preserve CPU-first runtime, isolated Git worktrees, bounded changes,
  deduplication against existing open PRs and model diversity for cross-checks.
  `scope` is a coarse exclusion key; the future dispatcher must also track
  changed-file overlap and PR dependency graph.
- Don't activate 300 workers until a smaller load test demonstrates that
  model quotas, test runners, acceptance throughput and GitHub rate limits
  handle the proposed load. GitHub Actions matrices support at most 256
  jobs per matrix; 300 requires sharding and sufficient runner capacity.
- The dispatcher only transitions `queued → running → review`,
  `running → blocked`, or lease-expiry retry. Only an independent intake
  service can merge to `main` under the documented exact-head policy.
