# R0.15 — portable single-file offline Mojum room editor

**Experimental Draft.** The canonical editable source is still [`tools/room_designer.html`](../tools/room_designer.html) plus the adjacent [`room_designer_core.js`](../tools/room_designer_core.js) from [#163](https://github.com/Garyu-tt/mojum/pull/163). This helper produces a portable **one-file HTML** copy so a phone or offline device needs just one document to edit synthetic room layouts.

## Generate the one-file editor

From the Mojum repository checkout, with Python 3.11 or newer:

```sh
python tools/package_room_designer.py --out ./room-designer-offline.html
```

Open the resulting `room-designer-offline.html` in a local browser (`file://`). The editor itself needs **no Python, Node, npm, package installation, hosting server or Internet** to open or operate. It draws the existing SVG canvas, edits room size/start/obstacles, validates geometry, exports/copies a plain local `mojum.world.point-room-draft/1` JSON file, and supports canonical local JSON reimport.

The Python packager is run **only at generation time**. It reads and validates the two checked-in local UTF-8 source files, replaces exactly one local classic JS script tag with the literal JS bytes, refuses unsafe `</script` delimiters, and produces an HTML file with no sibling script dependency. It publishes through a same-directory owner-only 0600 temporary file, flush/fsync, and atomic exclusive filesystem link. It **never overwrites** a file (including a symlink or a second concurrent creator) and never opens SQLite, Noosphere or an agent process. A crash after exclusive publish may leave a complete, already created output; rerunning deliberately refuses reuse, so choose a fresh filename if necessary.

The command prints a local file path, byte count and SHA-256 of the emitted HTML. Those metadata are for reproducibility only, **not code signing or remote attestation**. No user data, API keys or private journal files are included.

## Use with the real CPU Aquarium

1. In the locally opened editor, move the starting marker and obstacles; edit precise decimal coordinates in the form fields.
2. Save a new `room.json` file.
3. Validate with `PYTHONPATH=src python -m mojum.room_input room.json` — this does **not** create any journal.
4. Create a **new** synthetic Aquarium journal with:

   ```sh
   PYTHONPATH=src python -m mojum.aquarium \
     --db ./brand-new-room.sqlite3 \
     --exploration-mode goal_free \
     --developmental-policy local_cell_v2 \
     --room-numeric-contract typed_v1 \
     --new-room-contract room.json \
     --ticks 12
   ```

5. To restore the identical room later without creating frames, supply the saved independently anchored journal head using the [typed-room resume](R015_VERIFIED_TYPED_ROOM_RESUME.md) `--resume-typed-room` mode.

The editor/packager do not create or operate the organism themselves. Synthetic history remains in explicit, separate, PRIVATE SQLite files. The input-only JSON draft's original numeric tokens `4` and `4.0` are intentionally preserved: Python hashes their original int/float forms differently. The persistent bootstrap remains the existing `mojum.aquarium.bootstrap/2` with its exact numeric room schema, not a new wire version.

## Actual QA and limits

The standalone builder has independent Python stdlib unit tests for exact HTML composition, stable hash/bytes, source mismatch/unsafe script validation, no-clobber of preexisting/symlink paths, owner-only file permissions and two racing creators. Its dedicated workflow builds the portable HTML on Python 3.11, 3.12 and 3.13, then runs the **same existing real offline Chrome/Chromium file:// DOM/keyboard/pointer/phone-width acceptance** against the **bundled single HTML** by passing `MOJUM_ROOM_HTML=room-designer-offline.html` to the smoke test; it requires real Chrome and captures a 393px phone-width PNG. This is stronger than comparing source strings but is **not a physical Android/touch-device acceptance**.

This feature is a local 2D rectangle editor for an offline point-room fixture — **not** MuJoCo physics, a 3D character editor, real cognition, distributed messaging or a security boundary for untrusted web origins. Opening the file from a trusted local directory is the intended workflow. Passing an actual private `room.json` to a browser or public issue tracker requires separate user permission.

As with #163 and #151, this remains a stacked **Draft** pending exact-head CI, independent review and explicit owner release approval. It does not reconcile historical original v2A/v2B journal ABI [P0 #116](https://github.com/Garyu-tt/mojum/issues/116), and it does not change the existing `main`, Noosphere, or runtime default policies.
