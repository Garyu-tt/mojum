# GPU Legion R0.1: multiple gpt-oss-20b servers, no host hardcoding

This is a **read-only placement planner**, not a claim that models are running.
It supports the Spartan coordinator from #477 and the GPU expansion of issue
#478 without modifying the CPU Legion executor, real hosts, VM settings,
GitHub Actions, secrets or merge rules.

## Intended model

Use the open-weight `openai/gpt-oss-20b` (20.9B total parameters, roughly
3.6B active parameters per token), with a **verified local GGUF** and
`llama.cpp` CUDA backend. Official sources:

- OpenAI: https://openai.com/index/introducing-gpt-oss/
- GGUF llama.cpp guide: https://github.com/ggml-org/llama.cpp/discussions/15396
- Multi-GPU layer split: https://github.com/ggml-org/llama.cpp/blob/master/docs/multi-gpu.md

Official guidance says the 20B model can run in ~16 GB of memory with MXFP4.
A practical GGUF MXFP4 file is about 11.3 GiB, but runtime GPU buffers,
per-device allocation, KV cache and context all add to the budget. The planner
defaults to **15 GiB usable free GPU memory per model replica**, after
**768 MiB reserved per participating GPU**, and short 2048-token context.
Neither budget nor CUDA compatibility is guaranteed; benchmark and adjust.

### 7 x 6 GiB Pascal example (synthetic, not live)

With seven fully free P106-100 cards, six can hypothetically be divided into
two nonoverlapping three-GPU groups, with one GPU left unassigned. That is
**two independent 20B model processes**, not 300 independent 20B models.
The coordinator may queue 300 virtual workers across those endpoints.
As observed through JediControl on VM200, ALT is still active and consuming
VRAM; two full-size replicas cannot currently be inferred from the actual
free-memory readings. The synthetic two-replica scenario applies only after
resources have been safely released and remeasured.
This is a capacity estimate, **not proof** of an executable model on Pascal.
Older NVIDIA Pascal (compute capability 6.1) requires a compatible CUDA
build, typically CUDA 12 and a llama.cpp build targeting `sm_61`.
Native MXFP4 GPU acceleration is unavailable on this generation. Expect
performance and layer/offload behavior to differ from modern GPUs; slow PCIe
links can dominate. Never assume that 42 GiB on seven cards is one contiguous
42 GiB GPU.

Before claiming real instances, measure per-GPU free memory and actual
CUDA/model compatibility, load the exact GGUF, verify layer placement, test
sample coding prompts, check throughput, GPU power and thermal behavior and
confirm no OOM during sustained two-server operation.

## Pinned GGUF provenance (verified from upstream HTTP metadata)

Source repository: `ggml-org/gpt-oss-20b-GGUF` on Hugging Face.
Use this exact immutable repository revision when staging files:

```text
Revision: ccd679448b30b7438e83f3662ea0108c4b6011c3
File: gpt-oss-20b-mxfp4.gguf
Expected bytes: 12109566560
Expected SHA-256: be37a636aca0fc1aae0d32325f82f6b4d21495f06823b5fbc1898ae0303e9935
URL: https://huggingface.co/ggml-org/gpt-oss-20b-GGUF/resolve/ccd679448b30b7438e83f3662ea0108c4b6011c3/gpt-oss-20b-mxfp4.gguf
```

Check file size and `sha256sum` before renaming a downloaded `.part`
file into place. Never overwrite the existing ALT 120B checkpoint. The model
is intentionally **not** stored inside this GitHub repository. The observed
Mikoshi VM200 mount `/srv/alt` has its own filesystem; however, remote
model staging has **not completed**, and the old `alt-factory-3.service`
remains active until a tested replacement exists.

## Read-only planning

On a machine with NVIDIA drivers installed:

```sh
python3 tools/gpu_legion_plan.py --model /models/gpt-oss-20b-mxfp4.gguf \
  --require-replicas 2 > gpu-placement.json
```

The planner probes only local `nvidia-smi` and emits a machine-readable plan.
It **never executes** the emitted commands or contacts an LLM endpoint.
The default new endpoint is **127.0.0.1:18180** (next: 18181), deliberately
avoiding the existing ALT factory service on port 18080. The output field
`hardware_verified` is **always false**: even live `nvidia-smi` is an
observation, not authenticated device attestation or proof of model loading.
Only a real direct probe sets `observed_local_inventory=true` in CLI output;
an offline `--inventory` file cannot do so even if its JSON says
`source: nvidia-smi`. No generated plan authorizes starting a service. If
`nvidia-smi` is absent, invalid, or the required number of replicas cannot
be placed within the budget, it exits 2. If a host has fewer than two viable
replicas, omit `--require-replicas 2` to see its actual proposal.

For a unit-tested, synthetic seven-card scenario without GPUs:

```sh
python3 tools/gpu_legion_plan.py \
  --inventory examples/gpu_legion_7xp106.synthetic.json \
  --require-replicas 2
python3 -m unittest discover -s tests -p 'test_gpu_legion_plan.py' -v
```

### Heterogeneous-fleet packing

For up to **12 supported GPUs**, placement now searches bounded device masks
to maximize the number of disjoint model replicas first, then minimizes GPU
card count and wasted usable VRAM. Greedy selection by smallest card count can
underestimate feasible concurrency: with usable VRAM of
`[3, 3, 3, 3, 9, 9]` GiB and a 15 GiB model budget, choosing the 9+9 pair
yields only one replica, but two groups of 9+3+3 fit. The planner's
`placement_strategy: exact_max_replicas` marks that bounded exact search.

For **more than 12 supported GPUs**, the planner deliberately uses a
deterministic greedy fallback, reports `placement_strategy: bounded_greedy`,
and warns that the estimated number can be **lower** than the feasible maximum.
This prevents accidental exponential work in a read-only capacity preflight.
Neither strategy proves physical CUDA compatibility, memory peaks, or
running instances.

Each proposed replica includes:

- nonoverlapping GPU indices, unique loopback inference port, and usable VRAM
- `CUDA_VISIBLE_DEVICES` mapping and an argument-vector for `llama-server`
- bounded `--ctx-size 2048`, `--parallel 1` and layer splitting across GPUs

The model source is revision-pinned above. Hugging Face's current default
branch may expose a different case-sensitive filename and byte count than
that immutable revision. Do not substitute a newer main-branch download while
reusing the pinned hash; verify the resolved revision, filename, size and
SHA-256 as one unit.

The plan uses the **current index order** reported by nvidia-smi. Index order
can change across reboots or GPU resets. An actual launcher must re-probe and
verify physical GPU UUIDs immediately before launch, avoid sharing devices
between replicas, and reject changed inventory. Never feed an untrusted
JSON-derived command to `eval` or a shell. No launcher is implemented here.

## Activation gate

1. Explicitly approve a GPU compute host. No default server is selected.
   This change does not contact or administer any node.
2. Capture trusted real GPU inventory, UUIDs, NVIDIA driver and CUDA version,
   compute capability, power draw, PCIe links, and available memory. A
   `source: synthetic` snapshot never authorizes placement.
3. Compile/verify a known llama.cpp CUDA build with support for the target
   architecture; securely stage a SHA-pinned GGUF. For Pascal, validate MXFP4
   compatibility in the actual installed runtime.
4. Run a single isolated replica manually, bound to 127.0.0.1. Confirm memory
   and GPU layer allocations, correctness, code-task acceptance, and tokens/s.
5. Validate two isolated replicas with sustained parallel calls. Record the
   actual number of concurrent model calls using a separate provider-flight
   probe (for example PR #497), not the count of dispatcher slots.
6. Connect approved local endpoints to scoped workers only after lease
   fencing (#488), local-only transport hardening (#499), bounded costs,
   isolated patch execution, and independent QA/SHIP integration. PRs must
   pass the exact-head/base merge acceptance gate.

If the GPUs are unavailable, stop. Do not silently deploy CPU-only processes
or switch to a remote cloud endpoint. The model servers should never have
GitHub publish/merge rights or secrets from Noosphere.


## Local GGUF integrity gate (read-only)

Before any GPU execution, verify the staged file against **one selected
revision**, without altering ALT. The stdlib-only verifier rejects changed
bytes, wrong GGUF magic, symlinks, pipes and size mismatches. It reads the
whole model in bounded chunks, without downloading it or launching a model.

For the **immutable ccd6794... revision listed above only**:

```sh
python3 tools/gpu_legion_artifact_check.py \
  --file /srv/alt/models/gpt-oss-20b-mxfp4.gguf \
  --bytes 12109566560 \
  --sha256 be37a636aca0fc1aae0d32325f82f6b4d21495f06823b5fbc1898ae0303e9935
```

The separate **current upstream main snapshot** observed 2026-11-22 uses
the case-sensitive filename `gpt-oss-20b-MXFP4.gguf`, size 12109566624 and
LFS SHA-256
`27cd6c432c7672cb812a92f611cf3ba7bbc35928262bb1e1253ff4ee6ae35901`.
Those values must **never** be mixed with the older immutable revision.
Pin its own full repository revision before creating a production manifest:
`https://huggingface.co/api/models/ggml-org/gpt-oss-20b-GGUF/tree/main`
is a **mutable** source, not a stable deployment pin.

A passing verifier means only that bytes match the chosen expected digest.
It does not prove the source of the supplied digest, CUDA compatibility,
model quality, throughput, VRAM sufficiency or launch readiness. Require
separate isolated llama.cpp tests and the independent CI/QA gate.

Run the new unit tests without any GPU or model download:

```sh
python3 -m unittest discover -s tests -p 'test_gpu_legion_artifact_check.py' -v
```
