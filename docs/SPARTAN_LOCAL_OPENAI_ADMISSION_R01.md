# #696 Spartan: optional llama.cpp / LiteLLM loopback admission R0.1

Status: **experimental, OFF by default**. No new mandatory dependency, model process, credential, GitHub publisher, workflow scheduler, or acceptance authority. The adapter consumes tools/spartan_budget.py's existing private SQLite admission ledger; it does not own or duplicate the spartan_coordinator.py lease manager. spartan_local_fleet.py already has a legacy llama.cpp-specific execution path; this new path offers a small, independently testable OpenAI-compatible boundary and must not run alongside another dispatcher for the same task/attempt.

## Reference use (operator-only, credential-free)

~~~python
from contextlib import closing
from pathlib import Path
from tools import spartan_budget as budget
from tools.spartan_openai_provider import LocalOpenAIProvider, ProviderPolicy

# This policy is deliberately an example, not an auto-discovery/deployment.
policy = ProviderPolicy(provider="llama_cpp", model="local-model-id", port=18182,
                        enabled=True, max_inflight=1, requests_per_minute=8,
                        tokens_per_second=64, max_output_tokens=256,
                        timeout_seconds=30, retries=0,
                        declared_ram_mib=4096, ram_ceiling_mib=8192,
                        declared_vram_mib=0, vram_ceiling_mib=0)
with closing(budget.connect(Path("/private/operator-owned/spartan-budget.sqlite"))) as db:
    budget.schema(db)
    # One-time setup on a NEW database, never change an existing immutable cap:
    budget.configure(db, token_limit=100000, micro_usd_limit=0)
    adapter = LocalOpenAIProvider(policy)
    result = adapter.complete(db=db, worker=1, request_id="issue-696-attempt-1-call-1",
                              prompt="A bounded coding prompt without PRIVATE data")
    # Treat result["text"] as UNTRUSTED; patch validation and publication are separate.
~~~

The operator must start llama-server separately and bind it to 127.0.0.1. The transport uses only POST http://127.0.0.1:<port>/v1/chat/completions, no HTTP proxy, redirect, auth token, URL override, cloud fallback, shell, tool calls or automatic retries. The `litellm_proxy` policy name is reserved for future integration, but `complete()` **always refuses LiteLLM proxy dispatch before ledger reservation or transport**, even if `enabled=True`. A proxy on 127.0.0.1 can still forward to billable cloud upstreams. Zero-USD reserve/settle would silently bypass the durable USD ceiling, and this adapter cannot certify upstream routes. Enable proxy calls only in a future independently reviewed change with verifiable local-only routing **or** conservative nonzero USD reservation, trusted settlement and provider-side hard spending limits. Currently only the `llama_cpp` route can dispatch. Response model must exactly equal the configured local model; mismatches reject the call.

## Admission and recovery guarantees

- **Before each model call:** validate opt-in, provider/model identity, bounded UTF-8 input, process-local concurrent calls (1..8), process-local per-minute request count and reserved-token throughput, declared RAM/VRAM profile against operator ceilings, then reserve tokens in the durable SQLite budget. A single request reserves len(prompt.encode('utf-8')) + max_output_tokens, conservatively assuming no more than one prompt token per byte. A duplicate request ID can never trigger a second dispatch, including after worker restart.
- **On success:** require one assistant choice, finish_reason=stop, no tool calls, bounded output, exact model identity and coherent nonnegative usage integers; settle reported actual tokens in SQLite. Reported use above the reservation permanently freezes the spending ledger. A missing/bogus receipt leaves debit unresolved; never silently refund.
- **On timeout/crash/redirect/HTTP failure:** no replay, no retry and no refund. An external operator must reconcile uncertain billing separately, using a *new* task/attempt only after fenced lease verification. Input prompts and model outputs are never logged by this adapter.
- **Limits of guarantees:** process-local rate/concurrency limits are **not** cluster-wide. SQLite budget is durable across worker processes only when pointed to the same secure on-host ledger, and its finite lifetime limit is not a true per-day quota. The adapter's RAM/VRAM fields are *operator-declared admission ceilings*, **not actual measurements**. CPU/GPU/RAM, token/s, cost, model session count, 1/2/4/8 ramp, isolation and independent acceptance must be measured/verified before any production activation. No guarantee is made for vendor billing, remote LiteLLM routes, or prompt token accounting that violates the assumption above.
- **Separations:** caller verifies coordinator lease UUID/worker/attempt fencing and path scope first. This code only returns untrusted text. It never executes that text, accesses Noosphere, reads private journals, publishes PRs, controls hardware or merges. Keep provider memory and credentials away from untrusted patch tests.

## Reproducibility and upstream review

- Python 3.11+ stdlib only; CPU-only controlled mock tests: python -m unittest discover -s tests -p 'test_spartan_openai_provider.py' -v.
- Upstream candidates: [llama.cpp](https://github.com/ggml-org/llama.cpp), MIT license; [LiteLLM](https://github.com/BerriAI/litellm), community components MIT with separate enterprise licensing. No upstream binary or Python package is installed by this PR. **The exact upstream commit/tag is deliberately not yet pinned**, because this PR validates only an HTTP protocol fixture, not a specific server version. Pin and review each actual server version, dependency and model license before any live benchmark or acceptance.
- Performance claim for this PR: **zero real model requests made and zero measured inference concurrency**. The 300-lease dispatcher is not a 300-model deployment. Run independent exact-HEAD/BASE CI and reviewer approval before merge.
