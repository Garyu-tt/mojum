# Audio frame timeline witness R0.1 (issue #949, parent #694)

`mojum.audio_timeline_witness.witness(frames)` is a pure, optional,
stdlib-only admission check for *already provided* PUBLIC_SYNTHETIC PCM16LE
mono audio frame bytes. It is **not** an audio decoder, VAD, ASR engine,
speaker, recording service or runtime integration. No file, network,
permission, journal or hardware calls are made.

## Wire contract

Each frame is a native `dict` with **exactly**:

- `schema`: `mojum.audio.pcm16-frame/1`
- `scope`: `PUBLIC_SYNTHETIC`
- `session_id`: 1..64 ASCII safe characters
- `sequence`, `tick`, `sample_offset`, `captured_at_ns`: native uint63
- `sample_rate_hz`: exact integer 16000; `channels`: exact integer 1
- `pcm16le`: native immutable bytes of length 320/640/960
- `network_access`, `physical_actuation`: exact `False`

Sequence starts at 0 and sample offset at 0. Frames are exactly 10/20/30 ms
(160/320/480 little-endian signed PCM samples), contiguous, capped at
256 frames and 245,760 PCM bytes total. `captured_at_ns` is a **declared
start sample-clock timestamp** with an exact 62,500-ns step per sample,
not an observed wall-clock timestamp. Ticks are nondecreasing and need
not be 1:1 with frames. All calculations are bounded at 2^63-1.

The `mojum.audio.frame-timeline-witness/1` receipt includes session,
frame/sample counts, tick range, start/end-exclusive timestamps, duration,
and a SHA-256 chain over ordered frame metadata and frame-byte SHA-256
values. Never returns audio bytes or arbitrary input metadata.

**Threat boundary:** This is tamper-evident **only relative to an independently
preserved trusted chain head**, not a signature, proof of consent, source
authentication or proof that timestamps match physical device time. Rejects
PRIVATE and real-microphone material by scope contract; the caller remains
responsible for truthfully labelling its source.

## Use

```python
from mojum.audio_timeline_witness import witness
receipt = witness(frames)  # explicit public synthetic frame fixtures
```

Hermetic tests (Python 3.11+):

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_audio_timeline_witness.py' -v
```

Distinct from #866 PCM energy, #897 WAV structure, #940 stereo downmix,
#944 rate conversion, #773 VAD, sherpa/whisper, PRIVATE Noosphere,
physical devices and canonical Aquarium runtime. A real source requires
separate review of consent, privacy and cross-clock synchronization.
