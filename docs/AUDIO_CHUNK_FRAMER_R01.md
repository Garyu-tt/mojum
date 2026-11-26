# Offline fragmented PCM16 assembler R0.1 (#952, parent #694)

This additive, CPU-first, stdlib-only component converts explicitly supplied
`PUBLIC_SYNTHETIC` **mono 16 kHz signed PCM16 little-endian** byte chunks into
**exact 20 ms / 640-byte** frames. It performs no microphone capture, audio
file parsing, ASR, VAD, interpretation, energy estimation, resampling, network,
disk writes, Noosphere journaling, or real device control.

## Python API

```python
from mojum.audio_chunk_framer import PCM16ChunkFramer

framer = PCM16ChunkFramer(start_ns=0, provenance="PUBLIC_SYNTHETIC")
out = []
out += framer.feed(bytes(1), chunk_index=0, byte_offset=0)
out += framer.feed(bytes(639), chunk_index=1, byte_offset=1)
assert len(out) == 1 and len(out[0].pcm) == 640
assert out[0].frame_index == 0
assert out[0].sample_offset == 0
assert out[0].observed_at_ns == 0
assert framer.finish()["frames_emitted"] == 1
```

- Chunks must be immutable native `bytes`, 1..10,240 bytes each.
- `chunk_index` starts at zero and increases by one; `byte_offset` starts
  at zero and must equal all previously accepted byte lengths. **Byte**
  offsets allow arbitrary byte fragmentation (including odd-byte splits).
- Accepted output is an immutable tuple of frozen `AudioFrame` records.
  Each frame has 320 samples, 640 bytes, 20,000,000 ns duration and exact
  synthetic sample-clock time `start_ns + frame_index * 20,000,000`.
- Pending audio is always less than one frame (640 bytes). Every `feed()`
  validates counters, bounds and all output timestamps **before** advancing
  the state. Rejected chunks can be corrected and retried.
- `finish()` is successful only if there is no partial trailing frame; it
  never pads or silently discards PCM data. Success prevents further feeds
  and returns only counters/schema/provenance, no audio.
- Hard limits: 100,000 chunks; 100,000 output frames; 64,000,000 input
  bytes; timestamps plus frame duration fit signed 64-bit ns.
- The framer is single-owner; do not call `feed()` concurrently. The
  returned frames hold their PCM bytes for the caller; the framer retains
  only the current bounded incomplete fragment, **not** past frames.
- `PUBLIC_SYNTHETIC` is an explicit caller provenance assertion, not
  a privacy enforcement or permission mechanism. Never send microphone,
  private, copyrighted or unconsented audio into this synthetic-only path.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_chunk_framer.py' -v
```

Tests cover one-byte/odd fragmentation, multi-frame equivalence, deterministic
sample clock, compatibility with the existing `audio_energy` frame reader,
strict types, duplicates/gaps/reordering, timestamp overflow atomicity,
partial-finalization and bounded buffering. No installation or Internet needed.

## Integration and limitations

The canonical Aquarium, microphone/audio capture, Noosphere and existing
energy/VAD/sherpa/whisper adapters are intentionally untouched. A future
integration must add explicit consent, clock-source validation, secure data
handling, backpressure, and independent CPU latency measurements. This feature
does **not** demonstrate live speech or organism audio perception.
