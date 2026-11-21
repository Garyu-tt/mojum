# R0.15 offline visual point-room designer

**Scope of this main-target forward-port:** standalone local HTML/SVG authoring, deterministic numeric JSON export/reimport, bounded undo/redo and an independent point-motion walk preview. No production organism, database, journal, server or network connection is used by the editor.

## Open and edit

Open `tools/room_designer.html` in a modern browser with the three adjacent JS files (`room_designer_core.js`, `room_designer_history.js`, `room_walk_preview.js`). Alternatively generate one portable HTML file:

```sh
python tools/package_room_designer.py --out ./room-designer-offline.html
```

This emits a NEW no-clobber local HTML file; the output is not signed or remotely verified. The editor works through `file://` without Node, Python or server. Drag the green start marker or rectangles, edit coordinate fields, add/remove up to 64 rectangles, undo/redo up to 64 history snapshots and save/copy JSON. Reopening only the editor's exact canonical JSON preserves literal numeric forms (`4` versus `4.0`). Invalid geometry, duplicate JSON keys, noncanonical formatting and malformed numeric tokens fail closed. The room draft is **input-only** `mojum.world.point-room-draft/1`, not a new SQLite format.

## Walk preview

Click **Тест прогулки** and move the blue dot with the directional buttons (0.25 metres per step). The bounded 512-step demonstration enforces the local point-room's closed obstacle edges, swept segment collisions and room walls. It never changes the draft JSON, commits a tick to Aquarium or learns.

## Important integration boundary

The original stacked #163 implementation assumes the typed-room CLI from #151. That code is **not on current main** even though #151 was merged into another feature branch. Consequently this forward-port deliberately **does not advertise or run** `python -m mojum.room_input`, `--new-room-contract`, `--room-numeric-contract` or `--resume-typed-room`. JSON produced here must not be treated as a supported current-main Aquarium CLI input until its separate runtime contract has been integrated and accepted.

The tests on this branch cover Node model/undo/walk, real offline Chrome interactions, packaging, and Python `world_room.advance` motion parity. Cross-language SQLite birth/replay tests stay with the original stacked lineage and must be rerun after a reviewed runtime bridge is forward-ported. This is not MuJoCo, cognition, real sensing or distributed Noosphere.
