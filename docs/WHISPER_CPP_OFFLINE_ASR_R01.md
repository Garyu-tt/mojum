# whisper.cpp offline ASR comparison boundary (R0.1)

**Tracking:** #694, independent whisper.cpp CLI comparator slice. This is **not** the sherpa-onnx/PCM observation boundary, microphone ingestion, model training, or an accepted real-organism speech system. No default Mojum import, install, network action, or change to canonical GENESIS/Noosphere/world journals.

## Scope and upstream pin

- External candidate: [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp), **v1.9.5** (commit d1be6fde11ac6e0407606b4e42fe72d34add8037, released 2026-10-06), MIT for code. Review each model weights and speech dataset license separately.
- CLI boundary: version-tagged [whisper-cli README](https://github.com/ggml-org/whisper.cpp/blob/v1.9.5/examples/cli/README.md) documents -m, -f, -l, -t, -p, -ng, -nt, -otxt, -of and -np.
- Production status: **candidate/controlled spike**. No upstream executable or speech model was installed/executed by this change; all unit tests use a deterministic fake executable. Do not claim ASR accuracy, latency, or real-model compatibility before independent real-backend validation.
- Source revision is caller-supplied provenance, not independently attested build identity. Executable and model SHA-256 are checked against the exact bytes copied into an owner-private temporary staging directory (0700 executable, 0600 model). The subprocess uses **only those staged paths**. Changing the original paths between verification and spawn no longer changes the executed/loaded bytes. Hashes mitigate substitution but do not make an untrusted executable safe.

## Explicit opt-in, local-only boundary

Test from the repository root:

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_whisper_cpp_probe.py' -v
~~~

Call the module deliberately, never during automatic Aquarium startup:

~~~python
from pathlib import Path
from mojum.whisper_cpp_probe import AUDIO_SCHEMA, run_whispercpp_probe

public_synthetic_wav = Path("public-tone.wav").read_bytes()  # PUBLIC fixture
result = run_whispercpp_probe(
    public_synthetic_wav,
    {"schema": AUDIO_SCHEMA, "clip_id": "public.test.001",
     "visibility": "PUBLIC", "origin": "synthetic_fixture", "opt_in": True},
    enabled=True,
    executable_path="/opt/pinned-whispercpp/bin/whisper-cli",
    executable_sha256="<64 lowercase hex: sha256sum of binary>",
    model_path="/opt/pinned-whispercpp/models/ggml-tiny.en.bin",
    model_sha256="<64 lowercase hex: sha256sum of model>",
    backend_revision="d1be6fde11ac6e0407606b4e42fe72d34add8037",
    language="en", threads=2, timeout_s=30,
    frozen_reference="public synthetic reference transcription",
)
~~~

Replace the placeholder hashes with computed SHA-256 hashes; this example cannot launch as written. No default executable/model path, install or network fetch exists. Only PUBLIC synthetic fixtures are admitted; origin and opt_in are **unverified caller assertions**, not independent consent verification or proof that bytes are not private. Never submit PRIVATE, proprietary, participant-recorded or live-microphone bytes.

- WAV: strict RIFF/WAVE uncompressed mono PCM16 at 16 kHz, 1 sample to 10 seconds, maximum about 324 KiB including headers.
- Boundaries: 1–8 CPU threads, one processor, -ng GPU disabled, 1–120 second wall timeout, model <=1 GiB, executable <=128 MiB, transcript <=4 KiB.
- Process: argument list only, no shell invocation; no inherited tokens, proxies, home or PATH; stdout/stderr discarded. The staged executable/model, audio and output are kept together in an owner-only temporary directory, deleted after use. **Temporary disk budget may reach approximately 1.125 GiB plus WAV/output bytes** for the maximum permitted artifacts; operators must impose an OS disk quota and use a trusted temporary filesystem.
- **Not an OS sandbox:** even a pinned native process could read host files, make network requests, or exceed RAM/disk budgets. Real trials need unprivileged, network-denied OS sandbox/container, cgroup limits, and access only to trusted model and PUBLIC fixtures. Timeout does not cap memory.
- Result: hash-based provenance, wall-time, canonical whitespace-normalized transcript, optional frozen-text word error rate, and motor_action=None. It is an ASR observation, not neural control, a school grade or proof of comprehension. Timing is variable; tests compare only stable fields.

## Evidence and remaining gates

The stdlib unit suite covers explicit opt-in, PRIVATE/RESTRICTED and unknown-field denial, **exact string-type schema/source discriminators** (spoofed comparisons are rejected), malformed WAV, wrong channels/rates, missing/wrong artifact pins, relative/symlink paths, **private SHA-verified staging and mutation of original binary/model just before spawn**, language/thread/timeout bounds, nonzero/missing/invalid-UTF8/oversized output, process timeout, fake CLI flags and frozen WER. The dedicated opt-in Actions workflow runs synthetic tests on Python 3.11 and 3.13; verify its result on the exact final head. These tests **do not** establish real ASR accuracy.

Independent QA should run licensed synthetic spoken WAV clips against an exact v1.9.5 binary and separately licensed weights, with frozen references and network isolation. Record peak CPU/RSS/latency and held-out en/ru/uk WER against no-ASR baseline. Keep Python 3.11+ stdlib default and canonical Noosphere untouched.
