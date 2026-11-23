# Synthetic pulse interval diagnostic R0.1

`mojum.pulse_intervals.analyze_pulse_intervals` computes deterministic time
intervals between explicitly observed `False` → `True` transitions in a
bounded sequence of caller-provided synthetic pulse frames.

## Contract

- Input classification must be exactly `PUBLIC_SYNTHETIC`.
- Frames are an exact `list` or `tuple` of 1–10,000 exact dictionaries. Each
  dictionary uses native string keys and has exactly `tick` (an integer, not `bool`) and `pulse` (an exact
  `bool`).
- Ticks are non-negative, strictly increasing logical frame numbers. The
  caller supplies a fixed integer `dt_ns` in the range 1,000–1,000,000,000.
- Gaps are reported as missing frames and split continuity segments. The
  analyzer never interpolates or estimates pulses inside a gap, and never
  calculates an interval across a gap.
- A segment that begins with `pulse=True` is treated as an unanchored high
  sample, not an observed rising edge. An edge requires an explicit low-to-high
  transition in the same contiguous segment.
- The report contains exact integer nanosecond intervals, min/max, floor mean,
  floor median, completeness diagnostics, a stable schema/provenance label and
  a SHA-256 digest. The digest is an integrity aid, not authentication.

## Explicit non-goals

This is not heart-rate monitoring, medical analysis, physiology, prediction,
learning, an agent sensory interface, a writer to Aquarium/SQLite/Noosphere,
or a physical device integration. The `PUBLIC_SYNTHETIC` label is a caller
assertion, not proof of data origin.

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_pulse_intervals.py' -v
```
