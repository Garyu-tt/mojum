# Mojum Piper TTS offline output probe, R0.1 (#761)

**Experimental, opt-in, PUBLIC_SYNTHETIC only.** This is a bounded local **text → PCM16 mono WAV** output adapter in `mojum.piper_tts`, not a neural speech system included with Mojum. It does **not** install, download or embed Piper, play audio, open a microphone, read Noosphere, modify any journal, or actuate anything. The `main` Aquarium remains Python 3.11+ stdlib-only.

## Upstream and legal gate

- Current supported CLI lineage: [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl), [CLI](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md), reference release **v1.6.1** for the `python -m piper --input-file` shape. The engine is **GPL-3.0-or-later**. This is **not** a permissively licensed dependency; ensure Mojum's eventual distribution/linkage strategy is legally reviewed before production use.
- The old [rhasspy/piper](https://github.com/rhasspy/piper) repository is archived and MIT licensed. Its compatibility with the current CLI is **not asserted**.
- **Voices are independent third-party assets** and cannot inherit the engine's license. Review the exact local ONNX voice's `MODEL_CARD`, its `Training` provenance lineage (every fine-tuned ancestor), usage/redistribution rights, and checksum. In particular, Lessac-derived voices can carry research-only limitations. This adapter never bundles voice weights, cards, binaries or training data. `voice_license_reviewed=True` is an explicit operator assertion, **not automated license clearance**. [Official voice licensing](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md).
- The current upstream `piper-tts` package version is **read from the selected Python interpreter via installed metadata**, not inferred from filenames. `expected_piper_version` must exactly match it. Pin and approve the interpreter (SHA-256), ONNX and ONNX JSON config for each deployment. The SHA of Python alone does not pin all installed package/transitive code; use a locked, read-only venv and maintain a full software bill of materials independently.

## API

```python
import hashlib
from pathlib import Path
from mojum.piper_tts import PiperProfile, synthesize_public_wav

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

# Preinstalled in a dedicated, non-networked venv. No actual paths or voice bundled.
python = "/absolute/venv/bin/python"  # symlinks accepted for conventional venvs
voice = "/absolute/voice.onnx"
profile = PiperProfile(
    python_executable=python, python_sha256=sha(python),
    model_onnx=voice, model_sha256=sha(voice),
    config_sha256=sha(voice + ".json"),
    expected_piper_version="1.6.1",  # replace with independently approved installed pin
    voice_license_reviewed=True,      # ONLY after reviewing exact voice provenance
)
result = synthesize_public_wav("Hello, synthetic world!", profile, enabled=True)
assert result.schema == "mojum.speech.piper-output/1"
# result.wav: bounded in-memory WAV bytes. Caller chooses if/how to output it.
```

Text max **2048 UTF-8 bytes** and NFC; no control characters, newlines or raw phoneme markers. Only `provenance="PUBLIC_SYNTHETIC"`; there is no private text inference or trust classifier. Do not call from the organism's spontaneous memory, untrusted dialogue, or network ingress without a separate consent/privacy authority. `enabled=False` by default.

The adapter sends text as owner-only `text.txt` in a private temporary directory (`0600`), never command arguments; discards subprocess stdout/stderr and creates no persistent outputs. Uses installed Piper's `python -m piper -m <local ONNX> -f <private WAV> --input-file <private text>` CLI with a minimal child environment. Returns in-memory WAV plus SHA and non-sensitive measurements. Enforces mono PCM16 WAV, 8–48 kHz, <=12 seconds, <=1.2 MB, exact RIFF length. Refuses unsafe model/config symlinks, absent/mismatched pins, unexpected version, process failures, malformed files. Interpreter symlinks are allowed for venvs; their resolved target bytes are SHA-checked, but path-swap TOCTOU still needs filesystem protections.

## Resource and failure envelope

- `timeout_seconds`: 1–60, default 15, measured wall time; two subprocesses (version check and synthesis) are each bounded by this timeout. `cpu_threads`: 1–4, default 1, sets `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and `OPENBLAS_NUM_THREADS` hints. **Hints are not a CPU quota.** WAV output limited to 1.2 MB; temporary model residency / native RSS **is not capped in-process**.
- Native Piper/ONNX executes **with the caller's privilege**. Temporary file permissions, hashes, a minimal environment and timeouts **do not constitute a sandbox or prevent network access**. Production spike must use separate OS user, network namespace blocked egress, cgroup CPU/memory/PID/IO limits and read-only approved voice/runtime paths. Do not grant access to PRIVATE Noosphere data or hardware.
- Handle timeouts as hard failure and remove temporary files. There is no automatic retry, fallback model, synthesis event replay, queue, network endpoint, loudspeaker control or output persistence. The deterministic fake tests do not prove real TTS quality or speed.

## Validation

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_piper_tts.py' -v
```

Tests use an isolated pinned fake executable (no Piper/ONNX dependency or network) and cover consent gate, escaping/surrogates/oversize, SHA mismatch, missing/symlink voice assets, venv-style Python symlink, package version mismatch, non-zero exit, timeout, malformed/stereo/oversized/symlink WAV, CPU/time budget, no credential env inheritance and reproducible WAV hash. For real Piper acceptance record actual installed package + exact model card provenance/hash, Python/ONNX/runtime pinned versions, CPU wall p50/p95, peak RSS, worst-case audio duration, language/voice quality and failure/recovery on a sealed offline worker. **No actual model inference or hardware output has been measured by this PR.**

## Relationship / merge gate

Independent of #694 ASR (sherpa-onnx/whisper.cpp); does not alter existing input PCM energy observation, speech recognition, neural policy, GPU or audio hardware. Refs #761, #6. Independent reviewer + exact-head CI before merge. Do not silently promote an adapter probe into a living agent's unrestricted voice or an accepted integration.
