# Mojum R0.8 — opt-in local live spectator (file-following prototype)

**Status:** experimental feature built on top of PR #30 (R0.7 infant play).
No merge into `main` and no claim of production/public streaming.

## Why this matters

Mojum's primary user experience is watching a developing, imperfect
digital creature test motions, observe consequences and remember them.
This is a live **window onto measured events**, not a text generator
inventing a pretend inner monologue or a pretrained action script.

R0.8 adds a local real-time path from a committed step to an observer:

```
virtual tick -> synthetic sensory frame -> motor experiment -> world response
             -> learning update -> PRIVATE SQLite Noosphere CAS commit
             -> --watch JSONL (flushed after commit) -> local spectator file
             -> explicitly authorized browser File System Access polling
             -> room trajectory + timeline + action result + memory update
```

The virtual simulation clock remains strictly independent of wall time.
`--pace-ms` slows **only the CLI run**, never changes heartbeat, weights,
world position, journal sequence, model seed or recorded timestamps.

## Start in a local repository checkout

In the **first terminal**:

```bash
PYTHONPATH=src python -m mojum.infant_play \
  --db ./dante_infant.sqlite3 --ticks 500 \
  --watch --pace-ms 300 > ./dante_live.jsonl
```

In a compatible Chromium-based desktop browser (with File System Access
enabled), open `examples/infant_observer.html`, choose **Живой просмотр
файла**, and grant read access to `dante_live.jsonl`.

- The viewer polls this **one permission-granted local file** every 600 ms.
- It accepts only *complete* UTF-8 JSONL lines, rejects malformed events
  and discontinuous tick numbering, and shows the newest tick by default.
- Scroll backwards to inspect old attempts. **Последний такт** reattaches
  to the newest observation; pause/resume playback is separate.
- Stop watching to release the file handle without deleting saved events.
- To resume the same digital creature after 500 ticks, run the above
  command again **with `>>` append redirection**, not `>`.
  The chosen SQLite journal remains the source of truth.

```bash
PYTHONPATH=src python -m mojum.infant_play \
  --db ./dante_infant.sqlite3 --ticks 500 \
  --watch --pace-ms 300 >> ./dante_live.jsonl
```

**No web server is created.** The frontend contains no external scripts,
fonts, imports, requests, sockets or telemetry. The file chooser must be
explicitly invoked by the user. On browsers without
`window.showOpenFilePicker` (including many mobile and Firefox builds),
the earlier static **Выбрать JSONL** replay remains available, but
automatic file-following is not supported. A browser may require an
appropriate trusted context or file access permission even when the
API exists; do not promise support on all browsers or mobile devices.

## Privacy / integrity caveats

- `--watch` emits **a selected synthetic spectator projection**, not the
  full SQLite journal. However it still describes private experiences,
  so the JSONL file is also private and **must not be uploaded or shared
  by default**.
- No public audience, user accounts, cross-user read permissions,
  token-based access, encryption or censorship/redaction service is
  implemented in R0.8.
- The browser viewer does not cryptographically authenticate the JSONL
  file. Its schema/tick validation is **presentation hygiene**, not a
  substitute for Noosphere's source journal verification or a trusted
  remote signature. A forged local file can still display fabricated
  events; the operator must trust the local file origin.
- Browsers should not be granted write access; the viewer asks only for
  a file handle with read access. It does not use network APIs.
- The current viewer caps file size at 12 MB and the observation timeline
  at 10,000 events. It intentionally fails closed on truncation or a
  non-contiguous sequence rather than silently mixing histories.
- This remains synthetic motor-babbling with a heartbeat and 2D
  point-room. The spectator's obstacle view is **not** camera vision
  experienced by the agent.

## Checks and next useful increments

Run:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The new regression tests cover flush-before-process-completion, a paced
run's deterministic equality to an unpaced run, strict pacing bounds,
continuation of tick IDs across two sessions, and an offline-only browser
contract. The File System Access interaction still warrants manual
Chromium desktop testing with real file growth, truncation and a denied
permission grant before production use.

For future spectator rooms: add meaningful curiosity, sensory object
observations, safe input experimentation, explicit knowledge provenance
and individualized development. Keep cross-person PRIVATE memories
isolated; never show a human-designed behavior script as discovered
personality.
