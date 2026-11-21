# R0.15 — offline test walk in the visual point-room editor

**Draft and experimental; no private journal, release, persistent agent or production environment touched.**

This small extension is stacked on [#169](https://github.com/Garyu-tt/mojum/pull/169) for the [#163](https://github.com/Garyu-tt/mojum/pull/163) offline SVG room designer, which itself depends on the exact room JSON CLI [#151](https://github.com/Garyu-tt/mojum/pull/151). It deliberately **does not create another tab** or a second simulation backend.

## How to use

Open [`tools/room_designer.html`](../tools/room_designer.html) locally with `room_designer_core.js`, `room_designer_history.js` and `room_walk_preview.js` in the same directory. No server, Node/Python dependencies, credentials or external network are required **for using the editor**.

Below the same 2D plan, select **▶ Тест прогулки**, then use the four on-screen arrow buttons (25 cm per click). A blue marker shows the *test point*; a blue line shows its recent trail. The interface displays the number of steps, blocked attempts and position in metres. **С начала** resets the test to the edited room's original start. **■ Завершить тест** removes the overlay. It uses no real wall-clock timer and does not move the actual persistent organism.

For example, in the default 4.5 × 4 metre room (start 1.25, 2.5; a narrow rectangle at x=2.0), pressing **right** three times yields:

- Step 1: x=1.5, free.
- Step 2: x=1.75, free.
- Step 3: attempts x=2.0, blocked by a closed obstacle boundary; the blue point stays at x=1.75 and the blocked count becomes 1.

The existing green start point and rectangular geometry remain editable independently. If the user changes the room geometry or starts Undo/Redo/import, the walk automatically **resets to the beginning** of the new valid room; it never contaminates room authoring history, exported JSON, SQLite, Noosphere or agent memory. When the room is invalid, movement buttons are disabled until the geometry is repaired. Preview history is capped at 512 attempts; reset to inspect more.

## Numerical contract and validation

The tiny [`tools/room_walk_preview.js`](../tools/room_walk_preview.js) mirrors only the Python `src/mojum/world_room.py` geometric point-motion fixture:

- `0 <= x <= width`, `0 <= y <= height`; touching a rectangular obstacle is invalid;
- swept segment versus *closed* axis-aligned rectangle uses the same Liang–Barsky intersection (no skipping a thin wall between start and end);
- invalid/nonfinite/overbudget displacement is rejected;
- each attempt advances one **preview-only** tick, even if blocked.

Neither browser nor JS implementation calculates a journal SHA-256, runs the developmental policy, acquires genes, learns, reads PRIVATE events, authenticates a network identity, invokes MuJoCo, or claims to represent simulated pain, sentience or actual contact. The provenance string is `deterministic-point-room-fixture/v1` and `physical_contact=false`.

## QA and limits

- `node --test tests/room_walk_preview.test.cjs`: starts, free/blocked movement, swept 1 mm wall, wall edges and tangencies, bad inputs, bounded 512-step memory.
- `PYTHONPATH=src python -m unittest discover -s tests -p test_room_walk_python_parity.py -v`: independent Node and Python processes compare a set of deterministic movement paths and **every** `position`, `blocked`, `tick`, `provenance`, `physical_contact` field from JS against Python `world_room.advance` exactly (no optimistic approximation or fake physics success).
- Existing real headless Chrome smoke verifies visible controls, three right-steps with a genuinely blocked third move, the SVG marker/path, reset/stop, and **byte-identical authoring JSON** before and after the test walk.
- The dedicated 3.11–3.13 CI also runs inherited room editor, Undo/Redo, browser screenshot and custom SQLite creation/replay tests. Browser automation uses Node 22 + installed headless Chrome on GitHub hosted runners, without npm/pip dependency setup.

The UI-only path is still a **synthetic 2D point-room preview** for checking collision rules. These tests cannot demonstrate a functioning brain, trained policies, a full autonomous child, physical dexterity, real sensors, 3D MuJoCo behaviour, distributed continuity or safe irreversible actions. Independent human review, owner approval and real-device acceptance remain necessary. P0 [#116](https://github.com/Garyu-tt/mojum/issues/116) remains unresolved; historical v2A/v2B on-disk ABI is not altered or silently converted.

No production/owner-private database should be supplied to this experimental editor. It never requests one.
