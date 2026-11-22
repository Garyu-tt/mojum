# Spartan model-operation capacity probe R0.1 (#478)

This is a **CPU-only, offline, per-Linux-boot instrumentation helper** for
measuring *reported provider call intervals*. It intentionally does not use
Spartan dispatcher leases as evidence of a running model operation. The
300-overlap unit test uses synthetic timestamps to check the accounting
algorithm: **it is not proof of 300 deployed workers**.

## How to instrument a real provider

Initialize a fresh, local, PRIVATE SQLite database for each experiment:

```sh
python3 tools/spartan_capacity_probe.py --db ./spartan_metrics_PRIVATE.sqlite3 init --run ramp-4
```

In the actual provider adapter, call `begin` **immediately before** the
outbound inference request, and `finish` after the model response or exception:

```python
import uuid
from tools.spartan_capacity_probe import connect, begin, finish

op = uuid.uuid4().hex
conn = connect("spartan_metrics_PRIVATE.sqlite3")
try:
    begin(conn, op, "worker-001", "provider-model-version")
    try:
        response = call_real_model_provider()  # implemented in adapter, not here
    except Exception:
        finish(conn, op, "error")
        raise
    else:
        finish(conn, op, "success", input_tokens=response.input_tokens,
               output_tokens=response.output_tokens,
               cost_microusd=response.cost_microusd)
finally:
    conn.close()
```

`cost_microusd` is an **integer number of millionths of USD** as reported or
computed from the provider's current billing rules. It is not an enforced
budget, and missing usage fields are not proof of zero consumption.

Report a ramp run (4, then 16, then 32, and only scale further if allowed by
actual provider quotas, hardware and acceptance throughput):

```sh
python3 tools/spartan_capacity_probe.py --db ./spartan_metrics_PRIVATE.sqlite3 report --min-simultaneous-successful-workers 4
python3 -m unittest discover -s tests -p 'test_spartan_capacity_probe.py' -v
```

The report distinguishes `peak_successful_calls` from
`peak_simultaneous_successful_workers` so 300 overlapping calls from one worker
cannot falsely pass a 300-worker gate. Only **closed** operations count toward
peak; crashed or unclosed operations remain `unfinished` and are excluded.
Failures, token counts, reported cost and approximate run throughput are
included separately. Report is capped at 200,000 spans per run; rotate files.

## Safety and interpretation

- This probe records no prompts, responses, API keys or bearer tokens. Do not
  place secrets in operation IDs, worker IDs or model identifiers. The PRIVATE
  local SQLite file still exposes worker/activity metadata and is therefore
  protected on POSIX: new databases, WAL and SHM are owner-only (0600) even
  with umask 022. Existing group/world-accessible files, symlinked DBs,
  writable/replaceable directory chains and SQLite memory/URI pseudo-paths
  fail closed before opening. Use an operator-owned, non-group/world-writable
  directory (e.g. a private subdirectory of /tmp). This does not silently
  chmod existing records or defend against same-UID/root attackers. Inspect
  and secure existing unsafe files deliberately before retrying.
- The Linux boot ID is pinned at initialization. Reusing a database across a
  reboot is blocked because monotonic timestamps are not comparable. A fresh
  run must use a fresh database.
- WAL is for multiple **processes on the same host**. Never use a network
  filesystem or copy a live SQLite database between nodes to claim fleet-wide
  concurrency. Multi-host validation needs a separate authenticated collector
  with trustworthy interval provenance and clock uncertainty handling.
- Metrics are **self-reported by the worker wrapper**, not independently
  provider-attested; synthetic spans can be fabricated. Confirm provider-side
  request receipts/billing and sampled runtime CPU/RAM/network counters
  separately before claiming real deployment. A passing threshold does not
  prove that requests wrote useful patches or passed QA.
- The probe makes no API call and cannot start, stop, schedule or pay for any
  models. A separate bounded-cost provider adapter and independent QA gate
  must enforce operational and merge safety.
