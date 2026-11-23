# Issue #696: opt-in llama.cpp provider admission / LiteLLM safety boundary

**Status: integration slice for independent review; not production admission.**
`tools/spartan_provider_admission.py` adds a real, local HTTP probe and single-call
OpenAI-compatible completion boundary beside the existing
`tools/spartan_local_fleet.py`. The existing Spartan coordinator remains the **only**
owner of SQLite leases, retries, fencing and QA routing. This code does not
create workers, run model-generated patches, publish PRs or modify Mojum's
canonical Graph IR, Noosphere, Aquarium or SQLite journals.

## What works (stdlib-only)

- Explicit `Provider(kind="llama_cpp")` pins the exact server-advertised model ID
  and one of the existing **127.0.0.1-only** Spartan endpoint ports. It validates
  `/v1/models`, `/health` and `/slots` before each **single** completion.
  Duplicate model names or slot IDs, missing real boolean busy flags, bad
  context, unexpected response model, invalid or oversized JSON and excessive
  returned token usage fail closed.
- `admit()` checks **all-or-nothing** requested concurrency against observed
  free slots, per-process cap, conservative context/token limits, explicit
  operator-supplied RAM/VRAM headroom and integer micro-USD allowances. The
  `mojum.spartan.provider-admission/1` result is advisory and contains no prompt.
- `Session.complete()` calls only `POST /v1/chat/completions` using the pinned
  model and zero-temperature, non-streaming request; returns the text in memory
  without logging it. It probes afresh first and limits simultaneous requests
  through its own in-process semaphore. No retries after uncertain delivery.
- Fixed API paths/ports and literal IPv4 loopback; Python `ProxyHandler({})`
  disables proxy environment variables, redirects are denied, timeouts and
  request/response byte sizes are bounded. There are **no** API keys,
  environment reads, shell calls or persistent artifact writes.
- LiteLLM `127.0.0.1:4000` can be *discovered* through `/v1/models`, but the
  execution gate deliberately refuses it: a local proxy can route to a
  remote/cloud provider or another backend, and its model catalog does **not**
  prove local data residency or physical capacity. No prompt is sent to it.

## Controlled developer example

Run from repository root with Python 3.11+ and an **already started, trusted,
loopback-bound** llama.cpp server. Use the **actual** ID returned by
`GET /v1/models`, not the fleet's friendly display name. The numeric headroom
below is an **illustrative input**, not a measured or authorized server budget.
If actual current available memory and per-call peak requirements have not been
measured, do **not** execute the completion.

```python
from tools.spartan_provider_admission import Provider, Headroom, Session, admit, probe

provider = Provider(
    kind="llama_cpp", port=18182, model_id="OPERATOR_PINNED_MODEL_ID",
    max_concurrent_calls=1, max_completion_tokens=256,
    min_context_tokens=2048, ram_per_call_mib=256, vram_per_call_mib=0,
)
observation = probe(provider)                       # real GETs to localhost
headroom = Headroom(ram_mib=512, vram_mib=0, max_cost_micro_usd=0)
permit = admit(provider, observation, calls=1,
               prompt_tokens_cap=256, completion_tokens_cap=256,
               headroom=headroom)
# Explicitly invoked only inside an authorized, fenced Spartan worker:
proposal_text = Session(provider).complete(
    "Propose a bounded virtual-only regression test for the current task.",
    prompt_tokens_cap=256, completion_tokens_cap=256, headroom=headroom)
# Treat proposal_text as untrusted and never execute it in this process.
```

For a real worker, **do not log** `proposal_text`, and do not feed PRIVATE
organism prompts or credentials to either endpoint. The task/attempt/fence must
be verified by the existing coordinator before invoking this module, and a
separate isolated worktree runner and credential-separated publisher must
validate every generated artifact before any draft PR. The provider here is
**not** a substitute for the existing `spartan_local_fleet` per-backend locks,
resource budget ledger, or Cezar task ownership.

## Gates and measured limitations

| Gate | This slice | Still required before deployment |
| --- | --- | --- |
| Local wire | Real loopback `/models`, `/health`, `/slots`, `/chat/completions`; deterministic test HTTP server | Exact running llama.cpp build, model file SHA-256, actual chat-template behavior |
| Concurrency | Validated free-slot snapshot and in-process semaphore | Cross-process/host reservations; handling the probe-to-call TOCTOU race |
| Cost | Integer **configured** maximum and no silent paid-cloud provider | Provider receipts, power/hosting accounting, durable cross-worker budgets |
| RAM/VRAM | Explicit **operator estimates**, fail when not sufficient | Real p50/p95 memory and token throughput on target hardware and 1/2/4/8 physical calls |
| LiteLLM | Model-catalog probe only; zero admitted calls | Audited entirely local routes, scoped keys, vulnerability/version checks, measured underlying slots, controlled egress |
| Safety | No production activation, writes, external telemetry, or patch execution | Independent QA, exact HEAD/BASE CI, security review of supplied prompts and sandbox |

**Upstream/license evidence:** [llama.cpp server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [llama.cpp LICENSE](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) (MIT); [LiteLLM license](https://github.com/BerriAI/litellm/blob/main/LICENSE) (MIT outside enterprise directory), [LiteLLM routing](https://docs.litellm.ai/docs/routing). No upstream code or model weights are vendored; exact runtime/library revisions, transitive packages and GGUF asset/model licenses remain **unverified and must be pinned by the operator**. The default repository license remains unresolved. In particular, do not deploy unreviewed LiteLLM images or hand it privileged keys as a shortcut for this adapter.

### Offline test

```sh
PYTHONPATH=. python -m unittest discover -s tests \
  -p 'test_spartan_provider_admission.py' -v
```

Test suite uses a purely local mock HTTP server on an ephemeral port (per-test
allowlist patch) and injected fake responses. It covers positive request wire,
model and slot mismatches, busy slots, context/budget exhaustion, invalid
numbers/JSON, proxy non-authority, and concurrent-call refusal. **These tests
are not evidence that an external llama.cpp service, local model weights,
LiteLLM or 300 concurrent agents were deployed or benchmarked.**
