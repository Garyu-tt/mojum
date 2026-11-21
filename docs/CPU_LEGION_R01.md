# CPU Legion R0.1: isolated CPU worker for the legacy server

**Deployment target:** the old Proxmox node `anakin`, after an independently verified SSH host key.
**Dependency:** PR #477 (`tools/spartan_coordinator.py`).
**Current state:** tested source package, not yet installed or running on `anakin`.

This worker uses an existing local-only OpenAI-compatible CPU model server
(for example llama.cpp with a small quantized coding model). It never calls a
cloud LLM, handles API keys, executes model-generated commands, runs untrusted
tests, pushes Git commits or merges its own proposals.

Each worker atomically leases one task from the dispatcher. It reads at most
five explicitly permitted tracked Python files, requests a minimal diff from
the local model, validates file scope and `git apply --check`, and writes an
0600-permission patch artifact. A separate CI/publisher and independent
acceptance service must test and turn that artifact into a PR.

The runner computes parallelism as the minimum of requested slots, model
server concurrency, CPU capacity and free RAM (after reserving 2 GiB).
300 configured slots never implies 300 models running in parallel.

## Deployment steps after target SSH fingerprint confirmation

1. Confirm host identity independently via Proxmox console or trusted host inventory.
   Observed, not authenticated, ED25519 fingerprint:
   `SHA256:ObQRBtgpqHN1mOebGZjGcQz/w5TVPKQZk/mHPcAV+fY`.
   Do not trust the network scan alone or turn off SSH host-key checking.
2. Inspect actual CPU, RAM and disk on `anakin`; benchmark the installed model
   with 1, 2, 4, 8 sequences, and set `--model-parallel` accordingly.
3. Install the reviewed repository commit as a dedicated unprivileged service
   account with isolated checkout and local model server bound to loopback.
4. Populate a scoped queue with instruction and paths per task. Do not share
   a live SQLite file across multiple hosts; use a fenced coordinator service.
5. Run a one-shot smoke test before enabling repeat execution:

```sh
python3 tools/spartan_coordinator.py --db /var/lib/mojum/legion.sqlite3 init --slots 300
python3 tools/cpu_legion.py --db /var/lib/mojum/legion.sqlite3 \
  --repo /opt/mojum --out /var/lib/mojum/patches \
  --model LOCAL_MODEL_ID --workers 300 --model-parallel 2
```

The example is a deployment template, not a command already run on the target.
No production/VM changes or live model deployment are authorized by this PR.
