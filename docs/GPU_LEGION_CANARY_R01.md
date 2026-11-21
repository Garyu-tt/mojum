# GPU Legion: credential-free loopback canary (R0.1)

This independent smoke-test tool measures whether already-running local
OpenAI-compatible model endpoints can serve small inference requests. It does
**not** install a model, start llama.cpp, change any systemd services, free VRAM,
touch ALT, open GitHub PRs or alter the 300-slot Spartan dispatcher.

## Safe local usage after explicit host selection

Use an approved compute host with a pinned GGUF already independently checked
by PR #504 and an operator-started, isolated llama-server. All endpoints MUST
be numeric IPv4 loopback addresses.

\`\`\`sh
python3 tools/gpu_legion_canary.py \
  --endpoint http://127.0.0.1:18180/v1/chat/completions \
  --endpoint http://127.0.0.1:18181/v1/chat/completions \
  --model gpt-oss-20b --parallel 2 --samples 2 --timeout 30
\`\`\`

Each endpoint receives only a hardcoded, harmless prompt, never repository
contents, private Noosphere memory or credentials. Transport rejects redirects
and disables proxy configuration. No generated text is stored or printed.
Response JSON is bounded to 64 KiB. At most 8 endpoints, 64 total requests
and 16 concurrent HTTP calls are permitted. Failure means exit code 2.

`peak_overlapping_http_calls` counts measured attempt intervals, including
HTTP errors, failed response validation and timeouts. Those failures must not
make concurrent load invisible. The separate
`peak_overlapping_successful_http_calls` preserves the successful-only count.
Both are client-side call intervals, **not** evidence that GPU kernels ran
simultaneously; pre-request Python scheduling can also contribute to the
interval around a failed call.

The report contains model **self-reported** names and token usage, response
latency and peak overlapping HTTP request intervals. Even 2 overlapping calls
do NOT prove GPU kernel overlap, two separately loaded model processes,
inference throughput, code-generation quality, correct gpt-oss-20b weights,
or the ability to run 300 simultaneous 20B replicas. HTTP requests may overlap
while the server queues actual inference.

A safe ramp therefore requires:

1. Restore a trusted path to the intended GPU host, without guessing network
   addresses or bypassing remote-execution policy.
2. Verify source revision, GGUF size and hash with the artifact gate in PR #504.
3. Confirm the Pascal GPU and actual llama.cpp CUDA load, layer placement,
   VRAM reservations and prompt throughput; **ALT remains running** unless
   a separately approved cutover and rollback have been validated.
4. Independently confirm one canary success on a real endpoint, then two
   isolated server endpoints and sustained model performance.
5. Record GPU UUID assignment and real kernel/engine metrics. Integrate into
   Spartan jobs only after queue fencing and independent QA/SHIP approval.

### Local tests, no GPU or remote host

\`\`\`sh
python3 -m unittest discover -s tests -p 'test_gpu_legion_canary.py' -v
\`\`\`

Tests use local HTTP fixtures only. Their success does not imply a deployed
GPU model.
