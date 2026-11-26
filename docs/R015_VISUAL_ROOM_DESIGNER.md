# R0.15 offline visual point-room designer

**Scope of this main-target forward-port:** standalone local HTML/SVG authoring, deterministic numeric JSON export/reimport, bounded undo/redo and an independent point-motion walk preview. No production organism, database, journal, server or network connection is used by the editor.

## Open and edit

With an installed wheel, obtain the complete standalone editor from any
working directory:

```sh
python -m mojum.room_designer --output ./room-designer.html
```

Open that HTML file in your browser. No sibling scripts or source checkout
are needed. Existing output paths are never overwritten.

Open `tools/room_designer.html` in a modern browser with the three adjacent JS files (`room_designer_core.js`, `room_designer_history.js`, `room_walk_preview.js`). Alternatively generate one portable HTML file:

```sh
python tools/package_room_designer.py --out ./room-designer-offline.html
```

This emits a NEW no-clobber local HTML file; the output is not signed or remotely verified. The editor works through `file://` without Node, Python or server. Drag the green start marker or rectangles, edit coordinate fields, add/remove up to 64 rectangles, undo/redo up to 64 history snapshots and save/copy JSON. Reopening only the editor's exact canonical JSON preserves literal numeric forms (`4` versus `4.0`). Invalid geometry, duplicate JSON keys, noncanonical formatting and malformed numeric tokens fail closed. The room draft is **input-only** `mojum.world.point-room-draft/1`, not a new SQLite format.

## Walk preview

Click **Тест прогулки** and move the blue dot with the directional buttons (0.25 metres per step). The bounded 512-step demonstration enforces the local point-room's closed obstacle edges, swept segment collisions and room walls. It never changes the draft JSON, commits a tick to Aquarium or learns.

## Run an authored room

Current Aquarium accepts the exported `mojum.world.point-room-draft/1`
document through `--room-file`. Validate the file before creating a journal:

```sh
mojum-room-check ./my-room.json --format json
mojum-aquarium --db ./custom-room.sqlite3 --room-file ./my-room.json --ticks 120
```

Pass the same room and runtime modes on every restart, trace export and
cold-copy operation. Aquarium pins the converted runtime geometry and start;
it does not pin the original numeric token spelling. The fixed historical
`local_cell_*` profiles do not accept custom geometry. The editor itself
remains input-only and cannot edit a running organism.

The older stacked #163/#151 commands (`mojum.room_input`, `--new-room-contract`,
`--room-numeric-contract`, `--resume-typed-room`) remain outside this supported
flow. This is a synthetic 2D point-room, not MuJoCo or physical contact.

## Packaged asset maintenance

The four `tools/room_designer*` / `tools/room_walk_preview.js` sources and
`tools/package_room_designer.py` remain canonical. Generate the single
packaged `src/mojum/data/room_designer.html` with that packer's
`standalone_html()` function after changing them. The package regression
regenerates the bundle and requires exact byte equality.

Tests cover the Node model/undo/walk, offline browser interactions, exclusive
packaging and Python motion parity. The package integration additionally runs
the bundled JavaScript model's JSON export through real Python room validation,
Aquarium creation and cold resume. Node model execution does not establish
browser rendering behavior; the separate real-Chrome workflow remains its gate.
