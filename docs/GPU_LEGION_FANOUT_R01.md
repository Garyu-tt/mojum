# GPU Legion Fanout R0.1: 300 agent identities, bounded GPU slots

This introduces `tools/gpu_legion_fanout.py`, a small **CPU-first**
admission and dispatch layer that routes model requests to **two already
running** GPT-OSS-20B backends using their actual llama.cpp
`/health` and `/slots` output. No server process, GPU model,
VM, GitHub worker or sandbox is created by this script.

**Separation of quantities is mandatory:**

- **300 logical agent IDs**: addressable work identities, not model copies.
- **Two physical GPT-OSS-20B model replicas**: model weights loaded
  into distinct 4-GPU and 3-GPU groups on Mikoshi VM200.
- **Observed inference slots**: queried live before each batch.
  At the last independently verified deployment state, **1 slot
  per physical model, 2 concurrently admitted requests total**.
- **Probe result, not production:** 4 slots × 2048 context tokens on
  cluster1 completed 4 concurrent requests and still had VRAM margin.
  The attempt to also enable four slots on cluster2 triggered the
  existing 75-second watchdog's two-failure fallback during sequential
  model reload, not an observed VRAM OOM. Both services were rolled
  back to the stable one-slot configuration and production monitoring
  was restored. An updated remote rollout was denied by execution
  policy; do not claim 8 slots deployed.

## Agent duplication

For each task, `clones: 2` sends two independent text-only inference
requests to the **two distinct physical replicas**. They share the same
model weights *within* each server but have separate invocation
contexts and clone IDs. Unlike loading a new 12 GB GGUF on GPU,
the incremental memory cost is predominantly KV cache and request
buffers. This is not model diversity or independent validation of
model correctness because both replicas have identical weights.

A one-clone task is assigned to the backend with the lowest
`assigned_requests/free_slots` ratio for the current batch.
Each backend has its own bounded thread pool of size equal to
its currently free slots; execution can never exceed the actual
reported per-backend slot count at admission time. The llama.cpp
server remains the final authority on actual runtime capacity.
If a backend is down, duplicated tasks **fail closed** instead of
silently running both clones against the same process.

All URLs are a fixed allowlist:
`http://127.0.0.1:18180` and `http://127.0.0.1:18181`.
Proxies and redirects are disabled. The implementation does not
carry API credentials, execute generated code, merge PRs,
claim SQLite task leases or open public network listeners.

## Example workload

```json
[
  {
    "id": "spartan-001-review",
    "agent_id": 1,
    "clones": 2,
    "system": "You examine a provided code fragment and report risks.",
    "prompt": "Review this supplied fragment without running it.",
    "max_tokens": 128
  },
  {
    "id": "spartan-002-plan",
    "agent_id": 2,
    "clones": 1,
    "prompt": "Outline a bounded test plan.",
    "max_tokens": 128
  }
]
```

## Commands (on the VM200 loopback, after independent acceptance)

```sh
# Read-only capacity discovery
python3 tools/gpu_legion_fanout.py --status

# Plan only (default). No generated prompts sent to models.
python3 tools/gpu_legion_fanout.py --tasks tasks.json

# Explicit inference, private output file created with 0600 mode,
# no overwrite. Default cap is only 32 expanded requests per batch.
python3 tools/gpu_legion_fanout.py --tasks tasks.json \
  --execute --output /srv/alt/gpu-legion-stage/agent-replies.json

# To permit 300 unique requests, raise the explicit budget:
python3 tools/gpu_legion_fanout.py --tasks tasks-300.json \
  --max-jobs 300 --execute --output /srv/alt/gpu-legion-stage/agents-300.json
```

## Private output directory preflight

Before `--execute`, the POSIX output directory must be owned by the operator,
not writable by group/others, and must have no symlink or non-sticky writable
ancestor. Sticky shared directories such as `/tmp` are allowed only above a
private owner-controlled output directory. Unsafe paths are refused before
model inference; the output itself is exclusively created with mode `0600`.
This is not a sandbox against same-UID filesystem mutation or hostile root.

Agent outputs may contain sensitive task content, so they are not
printed to stdout in execution mode. This prototype writes local
output with exclusive-create semantics, retains no persistent chat
history, and does not give models shell or tool privileges.
For durable agent memory, attach the agent-specific Noosphere context
through a separately permissioned application.

## Partial result preservation and retry boundary

When a model request raises an exception but other requests in the same
batch succeed, execution records a sanitized per-request failure containing
the task, logical agent, clone, physical port and exception **class** only.
No exception message, generated prompt or failed response content is logged.
Completed peer proposals are still committed to the exclusive-create 0600
JSON output; the report distinguishes `inference_requests_completed`,
`inference_requests_failed`, and `partial_batch`. The CLI returns exit
status **2** for a partially failed batch even though the private report is
available for recovery and independent review.

A failure receipt does **not** prove zero upstream token spend: a server may
have generated tokens before the timeout. There is no automatic retry and no
claim of exactly-once model execution. A caller must inspect the private report,
reconcile the uncertain request cost and explicitly resubmit only selected
tasks. An infrastructure failure outside individual calls can still abort
the batch and delete the incomplete reservation, so this is not a crash-durable
inference journal or an executor sandbox.

Offline CPU tests:

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_gpu_legion_fanout.py' -v
```

## Required engineering before 8+ simultaneous contexts

1. Health watchdog and rolling restarts must negotiate a **bounded
   maintenance phase** while retaining an automatic fallback.
   A 503 during a known initial 60-second model load should not
   count as two independent crashes. Do not disable the watchdog
   as a convenience.
2. Test `--parallel 2`, then `--parallel 4` per model with
   real VRAM, KV context and concurrent response measurements.
3. Reserve at least 0.8–1 GiB VRAM per card until empirical peak
   scratch/KV requirements are established; never extrapolate
   total free memory into another physically loadable model
   without per-card placement.
4. Wire a separately sandboxed Spartan worker executor to the
   existing SQLite lease coordinator. A text-only model reply
   is **not** a validated code change, PR or accepted task.
5. Independently review full tests and model identity checks before
   enabling any production fanout service. This branch is research
   and deployment-neutral.

The ALT 120B research in DedupArc R06 found only about **3.76%**
sample compression with R04 on 24 real MXFP4 slices, below the
roughly 29% storage reduction needed to place even a single
63.4 GB 120B checkpoint wholly in 42 GiB aggregate VRAM.
The 120B CPU/RAM expert-paging path needs a separate performance
study; it is not activated by this router.
