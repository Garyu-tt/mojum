# R0.15 portable single-file Mojum room editor

Experimental offline-only HTML/SVG editor derived from the reviewed source of #163. Its canonical development files are `tools/room_designer.html`, `tools/room_designer_core.js`, `tools/room_designer_history.js` and `tools/room_walk_preview.js`.

## Build

```sh
python tools/package_room_designer.py --out ./room-designer-offline.html
```

Open the generated HTML file locally. Its three JavaScript modules are embedded, so no adjacent files, server, Node/npm, Python or network connection are needed for the **editor itself**. The build step requires Python 3.11+. The packager refuses pre-existing outputs, symlinks and unrecognized script dependencies, uses an exclusive hard link to publish the completed file and reports its SHA-256. On POSIX the output file is mode 0600. A hash of the output is not a code signature.

The UI edits 2D point-room dimensions, a start coordinate and rectangles. It preserves decimal tokens such as `4` and `4.0`; supports canonical local JSON save/reimport, bounded undo/redo and a 512-step collision-only walk preview. The browser never opens SQLite or Noosphere.

**Not an Aquarium runtime integration on current main.** The separate typed-room parser and CLI from the original stacked #151 lineage have not been accepted into main. Generated `mojum.world.point-room-draft/1` JSON is an offline authoring artifact for now, not a valid command-line journal birth request. See [visual editor limitations](R015_VISUAL_ROOM_DESIGNER.md).

Dedicated CI builds this HTML on Python 3.11–3.13, checks no-clobber and exact bytes, runs Node tests and exercises real headless Chromium DOM/pointer/phone-width interactions. It does not prove Android device support, secure provenance, real physical motion or autonomous cognition.
