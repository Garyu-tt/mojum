# R0.15 — offline visual point-room designer (Draft)

**Status: isolated experimental branch, not merged or deployed.** This is an intentionally small, dependency-free visual authoring tool for **new synthetic** CPU-first room journals. It is **not** a MuJoCo scene editor, 3D authoring environment, agent consciousness experiment, production Noosphere viewer or live simulator.

## Start locally, without a server

Open [`tools/room_designer.html`](../tools/room_designer.html) in a modern browser from a local checkout, or use the browser's *Open File* command. Keep [`tools/room_designer_core.js`](../tools/room_designer_core.js) beside the HTML file. No web server, Node runtime, Python, network, account or external library is required **for editing**.

If a browser restricts loading neighbouring local `.js` files, serve the checkout locally with Python's stdlib only:

```sh
PYTHONPATH=src python -m http.server 8765 --bind 127.0.0.1
# Open http://127.0.0.1:8765/tools/room_designer.html
```

This starts a local development server solely for the UI. It is **not** an Internet-facing API or authenticated production service. Never bind it to `0.0.0.0` for a PRIVATE draft. Only the separately issued Aquarium CLI opens SQLite.

## Use the designer

- Edit room width and height as **literal decimal strings**, e.g. `4` versus `4.0`. This matters: Python `json.loads` uses `int` for the former and `float` for the latter, while the synthetic `world_revision` depends on exact scalar types.
- Drag the green start marker to reposition the synthetic creature's initial point. Use the four obstacle fields to set exact coordinates and rectangle size; select, add (up to 64 UI rectangles), drag, or delete obstacles. Keyboard and screen-reader users can edit all coordinates through labelled text inputs; arrow keys adjust the start marker when it is focused.
- The UI validates dimensions and obstacles against the same restricted point-room conventions before enabling copy/download: 0 < width/height <= 1000m, no out-of-room rectangles, no start inside/touching a closed obstacle. Invalid numeric fragments, booleans, negative values, exponent strings and code snippets are never exported as numbers.
- Click **Save JSON** or **Copy JSON**. The output schema is *always* `mojum.world.point-room-draft/1`, an **input-only** format, never a stored organism state. Visual changes do not write to, read from or create Noosphere databases.
- No remote network calls, third-party JS libraries, account sign-in, persistent browser storage, tracking or dynamic code evaluation exist. A downloaded JSON file remains local until the operator explicitly uses the CLI.

The UI uses a downward Y axis for convenience; the point-room coordinates are ordinary positive 2D fixture coordinates. Grid and rectangles are a **schematic preview**. The renderer may round a dragged coordinate to the nearest millimetre. Direct text entry retains decimal tokens up to 15 fractional digits; it is preferred for precise fixtures and for preserving `4.0` as a float rather than `4` as an integer.

## Verify JSON, create room, inspect and continue

After saving the file as `room.json` in the checkout:

```sh
# 1. Preview/validate, no SQLite birth
PYTHONPATH=src python -m mojum.room_input room.json

# 2. Explicitly create a NEW, private synthetic journal
PYTHONPATH=src python -m mojum.aquarium \
  --db ./visual-new-room.sqlite3 \
  --room-numeric-contract typed_v1 \
  --new-room-contract room.json \
  --developmental-policy local_cell_v2 \
  --exploration-mode goal_free \
  --ticks 12
```

The command prints a `journal_head` hash. Record it **independently from the SQLite DB**; a matching local hash does not imply authenticated provenance.

To cold-reopen the same geometry **without the original JSON**, without creating any frames, and in a SQLite-enforced read-only mode:

```sh
PYTHONPATH=src python -m mojum.aquarium \
  --db ./visual-new-room.sqlite3 \
  --room-numeric-contract typed_v1 \
  --resume-typed-room \
  --expected-journal-head ORIGINAL_INDEPENDENT_64_HEX_HEAD \
  --developmental-policy local_cell_v2 \
  --exploration-mode goal_free
```

For explicitly authorized additional synthetic frames, pass a positive `--ticks N` with the **current** independently recorded head.

## Preserved ABI and tests

The UI never invents a new persistent on-disk format: it emits the *same* simple point-room draft schema already accepted in [#151](https://github.com/Garyu-tt/mojum/pull/151). Its in-memory numeric tokens are strings intentionally: `JSON.stringify` of JS `4.0` would emit `4` and potentially change the `world_revision` in the following Python reader.

A dedicated GitHub Actions matrix uses Node 22 built-in `node:test` and Python 3.11/3.12/3.13, **no npm or pip installs**. It tests valid/invalid geometry, collision-inclusive boundaries, drag clamping, start/obstacle editing, literal decimal token preservation, browser UI control inventory, absence of network requests, exact comparison to the already accepted simple JSON example, and **cross-language end-to-end equivalence**: JS default visual export → strict Python parser → real disposable SQLite journal, with all persisted event JSON, head hashes and final Aquarium checkpoints matching the expert typed fixture. Original #151 CLI safety regression still runs.

The static HTML and JS control inventory tests **do not prove pixel rendering, touch/drag ergonomics, accessibility audits or browser compatibility across every phone**. Those need a real visual/browser acceptance pass after CI. The tool has no direct connection to a real phone body or virtual 3D mesh.

The direct same-name v2A vs v2B wire-format incompatibility remains a separate open [P0 #116](https://github.com/Garyu-tt/mojum/issues/116). This Draft must not be merged to main, installed on the user's private infrastructure or promoted to a production editor without independent reviewer and explicit owner acceptance.
