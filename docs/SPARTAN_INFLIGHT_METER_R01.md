# Spartan R0.1: measured RPC spans are not leased slots

Relates to issue #478 and dispatcher PR #477. This CPU-only stdlib component provides a **separate SQLite ledger of instrumented provider call spans**. It does not deploy 300 LLM processes, invoke a model, grant provider credentials, execute generated code, enforce spending limits or merge PRs. Synthetic unit tests only measure the SQLite mechanism.

## Trusted provider integration

Integrate at the **real synchronous model API boundary**. Only a trusted adapter may wrap an actual RPC in `track_call` and report a success after the response returns. Opaque request IDs must be unique across the ledger; models and IDs may not contain secrets, prompt text or user identifiers.

```python
from tools.spartan_inflight_meter import connect, create_schema, track_call

db = connect("/var/lib/mojum/spartan-meter.sqlite3")
create_schema(db)
with track_call(db, 1, "rpc-17a3", "provider.model") as done:
    response = trusted_client.generate(...)   # real, trusted model API call
    done(input_tokens=response.input_tokens,
         output_tokens=response.output_tokens,
         cost_microusd=response.cost_microusd)
```

Token and cost values must come from trusted provider usage or billing data. Missing values are SQL NULL/unknown, **not zero**; output distinguishes cumulative reported usage from the number of reported values. Costs use integer micro-US dollars. This is a meter, **not** a budget gate or invoice verification.

The default 120-second expiry prevents dead workers from appearing live forever. An active adapter must heartbeat before expiration if the call is still running. Calls that expire are terminal and cannot subsequently report success. Workers must use separate SQLite connections per thread or process. This meter uses WAL on **one designated host** and is not a distributed multi-node consistency protocol.

```sh
python3 tools/spartan_inflight_meter.py --db /var/lib/mojum/spartan-meter.sqlite3
python3 -m unittest discover -s tests -p test_spartan_inflight_meter.py -v
```

`active` and lifetime `peak_active` count instrumented spans, not actual provider-side inference sessions. A broken adapter can start a span without issuing an RPC. Confirm 300 real concurrent operations only with provider-side request records, real worker process health, clock-aligned data across hosts, rate-limit outcomes, cost data and the independent acceptance service. **Do not sum per-host peaks**, as they need not overlap in time.

No API keys, GitHub write tokens, prompts, replies or exception messages enter this telemetry database. Keep the meter and dispatcher in separate local databases; never run untrusted generated tests with their credentials. Independent QA must still check exact PR HEAD and BASE, CI completion, and policy before any merge.
