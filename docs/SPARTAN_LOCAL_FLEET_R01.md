# Mojum Spartan Local CPU+GPU Fleet R0.1

Goal: accelerate coding, QA and architecture work in **one** Mojum
repository with up to 300 **durable logical worker identities** and a
measured, bounded number of concurrent model requests.

This builds on the existing SQLite WAL coordinator at
\`tools/spartan_coordinator.py\`. The fleet itself **does not** implement
a trusted patch executor, tests of model-generated code, GitHub writes,
or independent acceptance. Each model reply is an **untrusted,
unreviewed proposal**, stored in a private 0600 artifact with its exact
lease and backend ID. A separate isolated worker executor must validate
patches, run CI and create draft PRs, and QA must independently accept.

## Real VM200 deployment inventory (verify before reuse)

| Port | Model | Resource | Quota |
| --- | --- | --- | --- |
| 18180 | GPT-OSS-20B MXFP4 | 4 × P106-100 | Live /slots |
| 18181 | GPT-OSS-20B MXFP4 | 3 × P106-100 | Live /slots |
| 18182 | Qwen2.5-Coder-7B Q4_K_M | **CPU-only** | Live /slots; 20 GiB systemd MemoryMax, CPUQuota=800% |

Qwen source is \`bartowski/Qwen2.5-Coder-7B-Instruct-GGUF\`,
revision \`1f629da0c8bed16b9e50cee91c70693650e66c35\`,
\`Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf\` exactly **4,683,074,336
bytes**, SHA-256
\`1664fccab734674a50763490a8c6931b70e3f2f8ec10031b54806d30e5f956b6\`.
Artifact at \`/srv/alt/cpu-coder/\`. Download and full SHA-256 were
verified on VM200. CPU-only Qwen service uses \`--gpu-layers 0\`,
\`CUDA_VISIBLE_DEVICES=\`, fixed loopback \`18182\`, and
\`--parallel 4 --ctx-size 8192\`. This model does not consume VRAM,
so its processes do not bypass \`mikoshi-gpu\`'s exclusive GPU leases.
Health/readiness and actual slot count must be checked after launch.

The 300 logical agents **are not 300 active model processes**.
If the three physical backends expose 1+1+4 free slots, at most six
independent model calls should be admitted simultaneously. The
dispatcher can hold up to 300 tasks and leases; load is measured
separately from queue depth, achieved tokens/sec and accepted PRs.

## Operator quick start (no GitHub token)

From a *checked-out* Mojum repository on the same VM200 as the model
servers, prepare a private work directory (700) and SQLite state:

~~~sh
install -d -m 700 /srv/alt/spartans /srv/alt/spartans/proposals
python3 tools/spartan_coordinator.py \
  --db /srv/alt/spartans/tasks.sqlite3 init --slots 300
python3 tools/spartan_coordinator.py \
  --db /srv/alt/spartans/tasks.sqlite3 enqueue --json tasks.json
python3 -m tools.spartan_local_fleet \
  --db /srv/alt/spartans/tasks.sqlite3 \
  --output-dir /srv/alt/spartans/proposals --status
python3 -m tools.spartan_local_fleet \
  --db /srv/alt/spartans/tasks.sqlite3 \
  --output-dir /srv/alt/spartans/proposals --agents 300 --max-jobs 8
~~~

The SQLite database contains task prompts and must be private, not just the proposal files.
The coordinator creates new SQLite DB/WAL/SHM files with owner-only permissions,
rejects unsafe pre-existing files or symlinks, and refuses a group/other-writable
parent directory. Before reopening an older deployment with insecure state files,
stop its workers and migrate the database into an audited private directory;
do not blindly chmod arbitrary paths while workers are running.

Example \`tasks.json\` entries must be **real independently testable
Mojum work**, not 300 copies of one vague architecture idea:

~~~json
[
  {
    "id": "issue-478-local-model-budget-review",
    "scope": "spartan.model-budget",
    "priority": 10,
    "payload": {
      "role": "review",
      "clones": 2,
      "max_tokens": 256,
      "prompt": "Review the supplied model budget accounting module for concrete race conditions. Give tests and a minimal change proposal. Do not claim changes have been applied."
    }
  },
  {
    "id": "issue-11-aquarium-initialization-tests",
    "scope": "aquarium.initialization",
    "priority": 9,
    "payload": {
      "role": "test",
      "clones": 1,
      "max_tokens": 256,
      "prompt": "Propose three narrowly scoped tests for a CPU-first offline aquarium startup with failure injection, expected assertions and a minimal patch outline."
    }
  }
]
~~~

Each distinct \`scope\` can have at most one active lease, preventing
two workers from writing the same logical module at once.

When \`clones:2\`, two separate model calls use **distinct physical
backends**, preferring different model weights (Qwen + GPT-OSS). Each
model slot is represented by a bounded semaphore; a secondary call
cannot exceed that backend's actual live free-slot count within the
fleet process. Cloning produces competing proposals for independent QA,
not two accepted patches.

## Execution and safety boundaries

- Fixed loopback backend allowlist; outgoing proxies and HTTP redirects
  disabled. No GitHub/model API credentials in worker processes.
- Read live \`/health\` and \`/slots\` before every batch.
- Atomic 300-agent SQLite task claims with attempt fencing,
  TTL and global coordination lock. No network-shared live SQLite.
- Concurrency capped by observed free slots, **not** 300 claims;
  command-level max-jobs caps 32 primary worker tasks; clone calls also
  share the observed physical backend semaphores.
- Per-task model max_tokens 32..1024, bounded prompt and response sizes.
- Models never execute generated code, invoke shell, push branches,
  merge PRs or access arbitrary host files.
- Generated proposals are stored in unique 0600 files. Text is not
  printed to stdout or transmitted anywhere outside loopback.
- Invalid tasks are blocked for review; no attempt to silently
  reinterpret untrusted content as executable instructions.
- A separate sandboxed patch executor with immutable worktrees,
  allowlisted paths, credential-free tests, bounded spend and
  independent exact-head acceptance is mandatory before claiming
  autonomous coding farm acceptance (issue #478).
- No Qwen GPU launch is authorized while both \`mikoshi-gpu\` clusters
  are exclusively leased to GPT-OSS. Opportunistic VRAM sharing must
  be implemented in the broker with per-process memory guard, not by
  bypassing the exclusive lease.

## Next gates

1. Prove the Qwen CPU endpoint loads and returns valid completions.
2. Execute a *bounded* set of legitimate 8–16 Mojum issue sub-tasks,
   save proposals and independently rate acceptance quality.
3. Wire a credential-free, isolated git-worktree patch/test worker,
   then a separately token-scoped PR publisher with SHA fencing.
4. Expand active local inference only where benchmark results show
   improved accepted PRs/hour. Optimize for useful changes, not
   occupied RAM.
5. Report real active model processes, slots, in-flight requests,
   queue depth, tokens/sec, tests passing and accepted PRs separately.

## Partial fleet failure visibility

A single SQLite connect/schema/claim failure is recorded as
`claim_unavailable`, without claiming lease recovery or exposing the SQL
error message. Unexpected worker-future exceptions are sanitized as
`worker_failed`, and completed peers' private proposals remain available.
The worker lease is uncertain after a crash and must be reconciled through
the existing TTL/fencing protocol; no automatic retry is performed.

The batch includes `claim_unavailable` and `worker_failures` counters.
The CLI prints the complete partial JSON summary and returns exit status
**2** if any worker has an unavailable claim, unexpected crash, blocked task,
queued retry or expired lease. These include `claim_unavailable`,
`worker_failures`, `blocked`, `retry_queued` and `lease_expired` counters.
An idle batch containing only `no_task` workers is not an error. A partial
batch is not a successful all-worker run and may have already consumed
inference tokens.

## Instrumented concurrency accounting

`scheduled_primary_workers` is the number of worker tasks submitted and is **not** actual concurrent model calls. `configured_physical_call_limit` is the sum of admitted per-backend slots. `model_invocation_attempts` counts `call_model()` attempts, including clones and failures. `observed_peak_inflight_model_calls` measures overlapping application-layer calls **after** physical semaphore admission. These are measured separately from proposals, completed model responses and the 300 registered identities. They are not proof of 300 simultaneous models or distinct provider processes.
