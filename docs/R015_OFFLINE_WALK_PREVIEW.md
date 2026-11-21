# R0.15 offline point-room walk preview

This local browser test path is part of the [visual editor](R015_VISUAL_ROOM_DESIGNER.md). It is a demonstration of geometric collision, not an organism runtime or a persistent simulation.

Open `tools/room_designer.html` with its three adjacent JS files, or package them into a single HTML. Choose **Тест прогулки**, then use the arrow buttons (25 cm per click). The blue dot and polyline show the temporary preview trail, blocked count and current position; **С начала** resets the walk and **Завершить тест** clears it.

In the default 4.5 × 4 metre room, three rightward steps from (1.25, 2.5) move to x=1.5, then x=1.75, then block on the obstacle beginning at x=2.0. Room edits and undo/redo reset an active preview. Invalid room geometry disables movement. A maximum of 512 attempts prevents unbounded preview history.

The JS implementation implements closed wall and rectangle boundaries, including swept Liang–Barsky segment intersection against thin obstacles. `node --test tests/room_walk_preview.test.cjs` checks deterministic outcomes. `PYTHONPATH=src python -m unittest discover -s tests -p test_room_walk_python_parity.py -v` compares movement results against current-main `mojum.world_room.advance`. Browser smoke covers visible controls, path rendering, and unchanged authoring JSON.

No Noosphere journals are read or written. The original #163 integration tests using typed-room JSON and SQLite are **not part of this main-only forward-port**: the typed-room CLI from #151 is not yet in main. No claim is made about MuJoCo, neural learning, Android or real-world contact.
