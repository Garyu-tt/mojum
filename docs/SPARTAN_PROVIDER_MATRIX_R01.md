# Spartan provider capacity matrix R0.1 (#696, offline controlled spike)

This **opt-in stdlib-only planning gate** maps an explicitly supplied llama.cpp or LiteLLM **loopback** model inventory to a deterministic 1/2/4/8 concurrent request matrix. It does not start a provider, connect to any endpoint, import third-party libraries, run a prompt, reserve durable credit, or modify the Cezar scheduler. It is a conservative **capacity estimate, not measured throughput**. An `operator-measured` snapshot labels *inputs*; the plan itself is still not a live test.

The existing `tools/spartan_local_fleet.py` already discovers **actual** llama.cpp `/health` and `/slots` responses on selected, fixed local ports and has an opt-in live executor. This independent file adds a bounded preflight for the missing **RAM/VRAM + decode throughput + USD/retry envelope**, without editing its provider transport or the independent `tools/spartan_budget.py` durable ledger. The helper `validate_live_slots(snapshot, backends)` accepts its existing `Backend` instances and rejects mismatched port, name, slot count, context, or stale free-slot claims. **Calling that helper is an operator responsibility and does not authorize dispatch**. LiteLLM has no equivalent trusted llama.cpp `/slots` evidence here; operator snapshots are not trusted admission grants.

## Reproducible dry run

From repo root, Python 3.11+ (no network, credentials, model weights or pip installs):

```bash
python3 tools/spartan_provider_matrix.py \
  --snapshot examples/spartan_provider_matrix.synthetic.json \
  --worker-leases 300 --ram-mib 16384 --vram-mib 0 \
  --tokens 16384 --micro-usd 0 \
  --prompt-tokens 256 --completion-tokens 256 \
  --window-seconds 10 --max-retries 0
python3 -m unittest discover -s tests -p 'test_spartan_provider_matrix.py' -v
```

This obviously fictitious one-model fixture has four free model slots, 8 GiB resident RAM and 128 synthetic decode tokens/sec. Its 1/2/4/8 plan admits `[1, 2, 4, 4]`, **not 300** requests. The 300 number labels Cezar identities eligible to request work; it is NOT 300 simultaneously loaded model copies or even 300 successful inference calls. Each burst is independently reset to the same frozen snapshot. No latency, CPU%, joules, request rate or sustained resident set is actually measured by these simulations.

## Pinned inventory contract

`mojum.spartan.provider-capacity/1` accepts a maximum 64 KiB UTF-8 JSON document with a strict duplicate-key/unknown-key/NaN rejection boundary, one of `synthetic-fixture` or `operator-measured`, and 1..16 resident model records. Each record requires a unique ASCII id and loopback `http://127.0.0.1:<port>` endpoint; explicit transport `llama.cpp` or `litellm`; model name; literal 64-lowercase-hex **model artifact SHA-256**; pinned, non-`latest` runtime version; CPU/GPU classification; resident RAM and VRAM MiB; 1..16 total slots and observed free slots; context size; measured **aggregate** decode tokens/sec; and max estimated micro-USD/1000 tokens. Example hashes and version in the synthetic fixture are fake and **MUST NOT** be used as actual pins. Validate the digest against the local GGUF/model artifact before any live deployment. The version is an asserted pin, not automatically resolved or verified.

The planner deducts model resident RAM/VRAM even for busy models, refuses impossible context or free slot estimates, reserves total tokens and upper-bound cost across `1 + max_retries` attempts, and constrains aggregate output by `decode_tokens_per_sec × window_seconds`. A missing or inadequate source observation must be treated as *no authorization*. Error output contains a known denial category rather than the original JSON, prompts, secrets, tokens, hardware hostnames or file contents.

**Limits of preflight:** The snapshot can be stale; operator-reported RAM/tokens-per-second can be wrong. No locking, live lease fencing, GPU allocation, actual billing reconciliation, retry dispatch, backpressure, or execution is performed. An actual worker must repeat current slot/capacity checks **and** use the existing trusted, persistent pre-dispatch `spartan_budget.reserve()` at dispatch time, respect `dispatch_allowed=False` on duplicate IDs, and never auto-retry an ambiguous timeout. `micro_usd=0` is valid only for a truly free local backend; provider invoices and paid LiteLLM upstreams require independent provider-side hard limits. No untrusted generated patch may be executed inside a model-serving process.

## Upstream / license review (not vendored)

- [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) is MIT-licensed in its core repository, but model weights have **separate** licenses. No llama.cpp code or model weights are bundled in this PR.
- [LiteLLM](https://github.com/BerriAI/litellm) publishes open-source MIT portions **and commercial/enterprise licensed components**. Check the exact selected distribution and feature paths before deployment. No LiteLLM code is bundled or called in this PR.
- A future live adapter must pin a reviewed llama.cpp git SHA or release and a reviewed LiteLLM exact version/digest independently, recording the results and any license obligations. This spike has **zero runtime upstream dependencies**, so there is no installed upstream version to assert or benchmark.

## Acceptance checklist

- Tests exercise 1/2/4/8, RAM and VRAM exhaustion, context, busy slots, token/sec, USD ceiling, retry multipliers, malformed and hostile snapshot fields, nonfinite/duplicate JSON, invalid pins and stale llama.cpp `/slots` snapshots.
- Test data is fake; no PRIVATE Noosphere events, prompts, real host identity or credentials. No persistence, branch merge, API call, network or real hardware actuation.
- To upgrade from spike to operational admission: wire the planner under trusted Cezar lease/fencing **with atomic budgets**; capture independently measured resident CPU/GPU memory, real decoded token throughput, latency and RPO/RTO of model outage for 1/2/4/8 sustained real calls; keep the exact Git head CI and independent QA gate. This PR alone must **not** be treated as that acceptance.
