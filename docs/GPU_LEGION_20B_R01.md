# GPU Legion R0.1: multiple gpt-oss-20b servers, no host hardcoding

This is a **read-only placement planner**, not a claim that models are running.
It supports the Spartan coordinator from #477 and the GPU expansion of issue
#478 without modifying the CPU Legion executor, real hosts, VM settings,
GitHub Actions, secrets or merge rules.

## Intended model

Use the open-weight \`openai/gpt-oss-20b\` (20.9B total parameters, roughly
3.6B active parameters per token), with a **verified local GGUF** and
\`llama.cpp\` CUDA backend. Official sources:

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
This is a capacity estimate, **not proof** of an executable model on Pascal.
Older NVIDIA Pascal (compute capability 6.1) requires a compatible CUDA
build, typically CUDA 12 and a llama.cpp build targeting \`sm_61\`.
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

\`\`\`sh
python3 tools/gpu_legion_plan.py --model /models/gpt-oss-20b-mxfp4.gguf \
  --require-replicas 2 > gpu-placement.json
\`\`\`

The planner probes only local \`nvidia-smi\` and emits a machine-readable plan.
It **never executes** the emitted commands or contacts an LLM endpoint. If
\`nvidia-smi\` is absent, invalid, or the required number of replicas cannot
be placed within the budget, it exits 2. If a host has fewer than two viable
replicas, omit \`--require-replicas 2\` to see its actual proposal.

For a unit-tested, synthetic seven-card scenario without GPUs:

\`\`\`sh
python3 tools/gpu_legion_plan.py \
  --inventory examples/gpu_legion_7xp106.synthetic.json \
  --require-replicas 2
python3 -m unittest discover -s tests -p 'test_gpu_legion_plan.py' -v
\`\`\`

Each proposed replica includes:

- nonoverlapping GPU indices, unique loopback inference port, and usable VRAM
- \`CUDA_VISIBLE_DEVICES\` mapping and an argument-vector for \`llama-server\`
- bounded \`--ctx-size 2048\`, \`--parallel 1\` and layer splitting across GPUs

The plan uses the **current index order** reported by nvidia-smi. Index order
can change across reboots or GPU resets. An actual launcher must re-probe and
verify physical GPU UUIDs immediately before launch, avoid sharing devices
between replicas, and reject changed inventory. Never feed an untrusted
JSON-derived command to \`eval\` or a shell. No launcher is implemented here.

## Activation gate

1. Explicitly approve a GPU compute host. No default server is selected.
   This change does not contact or administer any node.
2. Capture trusted real GPU inventory, UUIDs, NVIDIA driver and CUDA version,
   compute capability, power draw, PCIe links, and available memory. A
   \`source: synthetic\` snapshot never authorizes placement.
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
