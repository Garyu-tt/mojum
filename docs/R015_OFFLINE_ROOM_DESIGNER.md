# R0.15 — offline point-room designer (experimental, no server)

This is an isolated **Draft** UI stacked only on [#151](https://github.com/Garyu-tt/mojum/pull/151). It does **not** deploy the aquarium, mutate persistent Noosphere state, integrate MuJoCo, or select a canonical v2A/v2B history ABI. Python Aquarium stays stdlib-only and CPU-first; Node is used **only in CI** to test pure browser logic.

## Open it

Download the checked-out source directory and open [`tools/room_designer.html`](../tools/room_designer.html) in a desktop or mobile browser. The editor uses only the adjacent `room_designer.js`; it needs **no build step, remote network, web server, dependency install, login or service worker**. No telemetry or analytics.

The responsive SVG canvas supports **three tools**: draw obstacles with mouse/touch drag, set the point-organism start by tap (or keyboard arrow keys), and erase a rectangle by tap. Labels are native, all buttons have at least 44px tap targets, and the JSON export is accessible to screen readers. Configure width, height, start coordinates, and 0.25m snap-to-grid in the right-side panel; the layout stacks vertically on smaller screens.

The initial room is explicitly a **synthetic sample**: 4.5×4m, a rectangular obstacle at (2,1) of width 0.125m and height 2m, and start at (1.25,2.5). The visualization is a **deterministic 2D point/AABB room**, not an anatomical avatar, 3D physics, GPU workload or live runtime.

## Data contract

Download the generated `mojum-room-draft.json`, or copy the visible text. It uses exactly the **input-only** `mojum.world.point-room-draft/1` accepted by [#151](https://github.com/Garyu-tt/mojum/pull/151). The point-room bootstrap written by the actual Python Aquarium is `mojum.aquarium.bootstrap/2` with `mojum.world.room-exact-scalars/1`; **the draft authoring format is never persisted as a journal policy**.

**Numeric type integrity:** JavaScript `JSON.stringify()` would turn `4.0` into `4` and silently change the original Python world-revision hash. The designer deliberately emits numeric JSON tokens itself: manual dimension input `4` is an integer, while `4.0` is a float; obstacle and start coordinates are always serialized with a float marker (e.g. `2.0` or `1e-8`). It never includes executable user text or HTML in JSON. The editor limits rooms to 0–1000m per dimension, 128 obstacles, bounded coordinates and excludes starting inside rectangles. The underlying Python decoder provides a second independent integrity/geometry validation and supports its separate 4096-obstacle budget.

**Trust boundary:** the editor only saves or copies a local JSON draft. It does not create SQLite files, acquire an agent genome, move a real device, or automatically issue commands. The browser has no direct connection to servers, Notion/Noosphere or any network account.

## How to run that room in Mojum

From a repository checkout:

```sh
# Check a downloaded JSON before creating persistent state (no SQLite write).
PYTHONPATH=src python -m mojum.room_input mojum-room-draft.json

# Explicit birth in a NEW SQLite, NEVER reuse an existing file.
PYTHONPATH=src python -m mojum.aquarium \
  --db ./my-new-room.sqlite3 \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v2 \
  --room-numeric-contract typed_v1 \
  --new-room-contract mojum-room-draft.json \
  --ticks 12

# Later inspect the *existing* journal (0 ticks, SQLite mode=ro).
PYTHONPATH=src python -m mojum.aquarium \
  --db ./my-new-room.sqlite3 \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v2 \
  --room-numeric-contract typed_v1 \
  --resume-typed-room \
  --expected-journal-head YOUR_INDEPENDENTLY_RECORDED_64_HEX_HEAD
```

Save the first run's `journal_head` **independently of the SQLite file** before restoring. A head copied only from a compromised database is not cryptographic authentication. To add steps during a later anchored restore, explicitly specify a **positive** `--ticks` value. Historical `bootstrap/1`, old v2A archive and v2B mismatch are *not* converted by this UI. Continue to keep any real user or PRIVATE journal data out of public issue comments and commits.

## Acceptance and limitations

The stdlib-only browser app has deterministic pure-testable geometry functions (decimal lexical types, JSON token export, 2D point collision guard, bounded pointer coordinates, mobile SVG aspect-ratio letterboxing and grid snapping). A Node stdlib test harness also instantiates a minimal DOM and dispatches the **actual** script's touch pointer and keyboard listeners: drag rectangle, move start, remove obstacle, enter invalid dimension and recover. A cross-language smoke test invokes the **real** Python `mojum.room_input` on a generated temporary file, checking exact float numeric types and that no SQLite file is created. Independent GitHub Actions matrix: Node 20/22 × Python 3.11/3.13.

Browser support is designed for ordinary modern desktop/mobile browsers, but a **headless/physical-browser acceptance run is still required** before claiming visual polish, reliable touchscreen gestures on Pixel devices or public release. The UI does not currently import previous JSON (a lossy JavaScript `JSON.parse()` would erase 4 vs 4.0 and violate ABI), animate physical agents, render bodies or track digital-organism learning. Source import should be added only alongside a lexeme-preserving JSON parser and regression tests.

**No automatic merge** to [`main`](https://github.com/Garyu-tt/mojum), no deployment, no signed Noosphere export and no release approval until CI, independent review and explicit owner acceptance. P0 [#116](https://github.com/Garyu-tt/mojum/issues/116) for old v2A/v2B wire conflict stays OPEN.
