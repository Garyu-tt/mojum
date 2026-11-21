# Offline trace viewer: coverage map for superseded PR #74 (issues #316/#317/#319)

Absence of `tests/test_offline_trace_viewer.py` on `main` does not mean the
assertions are missing. Mapping of the #74 cases to current regression tests
(source of truth: `tests/test_aquarium_trace_export.py` for the exporter and
`tests/test_private_trace_integrity.py` for the real viewer JS run under Node):

| #74 case | Current coverage |
|---|---|
| tick gap rejected | `test_private_trace_integrity.py` (`gap` in the Node harness) |
| oversize file rejected without reading | same (`oversized`, 25 MiB) |
| stale async file-selection race | same (`race`) |
| HTML-looking payload stays literal text | same (`safe`) and `test_markup_remains_local_only_and_uses_safe_text_rendering` (no `innerHTML`) |
| genuine committed SQLite rows, same tick order | `test_aquarium_trace_export.py::test_replays_real_private_frames_in_same_tick_order` |
| WAL snapshot, source unchanged | `::test_source_open_with_uncheckpointed_wal_remains_unchanged` |
| opt-in private export | `::test_private_export_requires_explicit_opt_in` |
| no private data on stdout | `::test_cli_exports_to_file_without_leaking_private_payload_to_stdout` |
| no-clobber and 0600 | `::test_existing_destination_is_never_overwritten`, mode check at line 76 |
| metadata-only footer | `::test_guided_cli_exports_...` (footer excludes `temporal_range_state`) and footer checks in the integrity test |
| seek/prev/next, end/start, timed play/pause, speed, and stop on hidden tab | `test_private_trace_integrity.py` (deterministic Node VM on real viewer handlers, verified JSONL) |
| 5 cm rays with optional numeric probe fallback | same (synthetic explicit legacy fixture plus verified contact smoke) |
| bad subsequent import clears playback and disables controls | same (Node VM) |
| real Chrome | `tests/smoke_real_chromium_viewer.py`, `tests/smoke_r09_chromium_viewer.py` |

Limit-boundary regression cases (64k line, 5000 frames) were added in PR #387 (closed 2026-11-21). See the current `tests/test_private_trace_integrity.py` Node harness for the assertions. This map is an inventory of tests, not evidence of a successful run on the latest `main` SHA.
The user guide is `docs/REAL_AQUARIUM_TRACE_VIEWER_R01.md`.

The deterministic Node playback assertions are exercised through the existing
`test_private_trace_integrity.py` suite; they are not a substitute for physical
touchscreen or 24/7 playback testing. The 5 cm ray fixture uses the explicit
unverified legacy opt-in because the current producer does not emit that optional
historical field in its three-tick default toy-contact trace.
