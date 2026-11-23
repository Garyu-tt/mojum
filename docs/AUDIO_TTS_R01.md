# Offline TTS adapter contract R0.1 (Mojum #694)

This package provides an **opt-in adapter boundary**, not a speech engine. It is CPU-first and standard-library-only. Importing the module does not import sherpa-onnx, load a model, access a microphone, open a network connection, write a Noosphere journal, or play audio.

## Minimal synthetic example

```python
from mojum.audio_tts import TTSRequest, synthesize

class LocalPresetBackend:
    backend_id = "local.example"
    backend_version = "1.0.0"
    local_only = True

    def iter_pcm16le(self, request):
        # Replace with an operator-installed, licensed local preset voice.
        # This fixture is silence-shaped synthetic output, not speech.
        yield b"\x00\x00" * 240

request = TTSRequest(
    text="Hello, Dante.",
    language="en",
    voice_id="preset.neutral",
    sample_rate_hz=24_000,
    max_audio_ms=10,
    content_class="PUBLIC_SYNTHETIC",
    consent=True,
)
result = synthesize(request, LocalPresetBackend())
assert result.channels == 1
assert len(result.pcm16le) == result.frames * 2
```

The example produces only a short synthetic PCM buffer. It does not produce intelligible speech.

## Contract

- Caller creates an exact immutable `TTSRequest` with explicit `consent=True`, a selected local preset `voice_id`, language tag, and a `content_class` of `PUBLIC_SYNTHETIC` or `USER_PROVIDED`.
- No PRIVATE/RESTRICTED journal data is accepted as a documented content class. The adapter does not read records or decide whether a user's text contains sensitive information. A caller must select/authorize text and a licensed voice before calling it.
- Backend is injected by the caller. It must declare bounded ASCII `backend_id`, `backend_version`, and `local_only=True`, and implement `iter_pcm16le(request)`.
- Backend yields **mono signed PCM16 little-endian byte chunks**. Output is copied into immutable bytes and accompanied by an SHA-256 content digest.
- Output is consumed incrementally. Maximum request text is 2,000 Unicode characters; accepted sample rates are 16/24/48 kHz; duration is 1–30,000 ms; per-chunk bytes are capped at 64 KiB; total chunks are capped at 4,096; total bytes are capped by sample rate × 2 × requested duration.
- Every chunk must be exact native `bytes`, contain complete 16-bit samples, and stay within the per-chunk and aggregate limits. No audio is returned when the backend errors or violates the contract.

## Security and privacy boundaries

`local_only=True` is a backend declaration, **not** a sandbox or proof that a plugin cannot use networking. Run third-party engines in an operator-controlled OS sandbox with egress disabled and least privilege. Backend code executes inside the caller's process unless the caller provides isolation.

The adapter can enforce output-size limits while consuming the iterator, but cannot forcibly interrupt a backend blocked inside `iter_pcm16le()` or `next()`. A production integration must place the backend in a separately supervised process with an OS-enforced wall-time and memory budget. Do not pretend a Python generator timeout is a hard resource boundary.

This contract does not implement VAD, ASR, audio playback, streaming microphone capture, voice cloning, identity imitation, model download, voice licensing verification, or age/persona policy. Use only locally installed preset voices with documented provenance, permission and license. No neural voice identity may be inferred from a `voice_id`.

No model is bundled, pinned or downloaded here. Backend/model version, license and provenance must be audited by the integrator before enabling a concrete engine. No objective quality, latency, CPU/RAM or Android performance claim is made.

## Reproduce tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_tts.py' -v
```

The hermetic tests use an in-memory fake backend; they require no model, device, external dependency or network.

## Scope / limitations

This additive slice implements a stable schema boundary and bounded PCM handoff. It does not integrate with Aquarium, GENESIS, the canonical SQLite journal, world state, or any physical actuator. It must remain opt-in until a concrete backend passes its own pinned-model, license, process-isolation, quality, resource and platform acceptance.

Ownership for this slice: `src/mojum/audio_tts.py`, `tests/test_audio_tts.py`, and this document only. It is a child implementation under #694; do not close #694 or claim TTS capability based on a fake-backend contract.
