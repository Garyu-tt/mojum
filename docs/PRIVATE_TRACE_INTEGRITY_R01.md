# R0.13 PRIVATE JSONL frame integrity (viewer)

This is an opt-in, offline **byte-integrity** check between the existing
replay-verified SQLite export and its local HTML viewer. It is stacked on
[PR #87](https://github.com/Garyu-tt/mojum/pull/87); it does not change
the canonical Aquarius motor policy, GENESIS, sensory learner, SQLite
journal, physical device access or network delivery.

## What is checked

The verified SQLite exporter keeps its existing `trace_sha256` over the
complete exported JSONL file in its CLI metadata. It additionally writes
`trace_integrity` into the final JSONL checkpoint footer:

- Schema: `mojum.aquarium.trace-frames-sha256/1`
- `sha256`: hash of the **exact UTF-8 bytes of all frame JSONL lines**,
  preserving one terminating LF per line, before the checkpoint footer
- `byte_length`: length of those frame bytes
- `frame_count`: number of exported frames

The browser's built-in **WebCrypto SHA-256** checks the original frame
bytes before enabling playback. It also checks that a final checkpoint
exists, the selected tick span is consecutive and any `trace_range`
matches the actual frames. Corrupt frame bytes, missing footer, wrong
hash/count or inconsistent range **fail closed**.

Files written with older `--watch` and pre-integrity exporters can still
be opened when they have a valid final checkpoint, but the UI explicitly
says **"Без SHA-256: старый формат, не проверен"**. New files fail rather
than silently downgrading if the browser has no local WebCrypto.

## Precise trust boundary

**This is not cryptographic authentication or a digital signature.**
If someone can rewrite a local file, they can rewrite the frames *and*
the SHA-256 stored alongside them. Its purpose is to detect accidental
file corruption or truncation and avoid silently presenting incomplete
recordings as trustworthy evidence. Independent verification needs a
separately trusted expected digest and source journal head.

Full history replay and the source-journal hash chain validation still
take place during SQLite backup/export. The browser doesn't know the
hidden toy material, does not access SQLite and never transmits the
PRIVATE recording. Only the original synthetic fixture should appear
in CI; do not upload user/private traces to pull-request attachments.

## Repeatable QA

```sh
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_private_trace_integrity.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
python tests/smoke_real_chromium_viewer.py
```

Regression tests derive their sample from a real synthetic R0.13
Aquarium journal, verify exact Python `hashlib.sha256` bytes for
complete and bounded segment exports, then run the **actual inline
viewer JavaScript** with Node WebCrypto against good and tampered local
JSONL. Chrome desktop/390-CSS-pixel mobile CI is an independent
real-browser check: it must not accept a valid-looking but altered file.
