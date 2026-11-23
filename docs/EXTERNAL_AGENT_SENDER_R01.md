# Offline receipt sender for external agents, experimental #717 sub-slice

This is a **separate producer-only** component stacked on [PR #792](https://github.com/Garyu-tt/mojum/pull/792). It writes **one HMAC-signed JSON packet** to stdout from **one explicitly supplied observed receipt** on stdin. It is **not** a heartbeat daemon, remote connector, proof of worker execution, Cezar ingress API, auto-discovery service or new schedule. No events are emitted in the absence of explicit caller input.

## Safe local use (POSIX, private test fixture only)

1. Create a restricted directory and a fresh **raw binary** signing key. For example:

   ```sh
   umask 077
   mkdir -p ./private-test-keys
   python3 -c 'import os,secrets; fd=os.open("private-test-keys/agent.key",os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.write(fd,secrets.token_bytes(32)); os.close(fd)'
   ```

2. Independently record a **real observed agent event** matching the exact `mojum.external-agent-receipt/1` shape documented in `docs/EXTERNAL_AGENT_HEARTBEAT_R01.md`, including actual `seq`, `run_id`, `timestamp_ms`, `kind` and `status_code`. Do not infer a heartbeat from a planned schedule, PR, reserved slot, Telegram message or unverified process.

3. Sign that observed JSON without placing the secret in process arguments, environment variables, logs, or the event:

   ```sh
   PYTHONPATH=src python3 tools/external_agent_receipt_sender.py \
     --key-file "$(pwd)/private-test-keys/agent.key" \
     < observed-receipt.json > signed-receipt.json
   ```

Output is a single ASCII JSON line with exact keys `event` and `signature`. Do not publish signed operational receipts or credentials. Each event must have a fresh, ordered caller-managed sequence. This CLI does not persist sequence state or ensure that an observation occurred.

## Safety invariants

- Offline only, no sockets, HTTP, automatic retries, remote execution, or model requests.
- Raw binary HMAC key is at least 32 and at most 4096 bytes, from a **single-link, owner-owned, 0400/0600, absolute POSIX regular file**. Symlinks and unsafe file modes fail closed; Windows is currently unsupported because this prototype does not validate Windows ACLs. Never expose the key on CLI or through the source repository.
- Input is at most 2048 raw bytes, must be UTF-8 JSON, and rejects duplicate keys, NaN/Infinity, unknown fields and unsupported schemas. Receipt structure and HMAC are checked by the exact parent library functions.
- Rejects oversized input before parsing and prints only a fixed error category on failure. Error output must never contain raw input, private filenames or key bytes.
- The receiver **must independently verify** enrolled source identity, HMAC, monotonic seq, per-run transitions, server-observed TTL, transport authorization, and GitHub evidence. Packet generation is not receiver authorization.
- Key provisioning, revocation/rotation, authenticated transport, rate limits and production enrollment remain **future independent security work**, not implemented or silently assumed.

Run the isolated synthetic tests (parent #792 branch required):

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_external_agent_receipt_sender.py' -v
```

No PRIVATE Noosphere data, production journal, live hardware or automation is used.
