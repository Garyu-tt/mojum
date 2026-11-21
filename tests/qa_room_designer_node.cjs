"use strict";
/* Standalone Node stdlib QA for pure browser model + actual UI event handlers.
 * The runtime is still only a static HTML and local JS file, no Node required.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const cp = require("node:child_process");

const root = path.resolve(__dirname, "..");
const { dimension, startCoordinate, validate, buildDraft, floatToken, pointerToWorld,
        snap, withinRect } = require("../tools/room_designer.js");

function sample() {
  return {
    widthRaw: "4.5", heightRaw: "4.0",
    start: [1.25, 2.5], obstacles: [[2, 1, 0.125, 2]]
  };
}

test("exact JSON numeric type tokens are not collapsed by JS JSON.stringify", () => {
  const room = sample();
  const draft = buildDraft(room);
  assert.match(draft, /"width": 4\.5,/);
  assert.match(draft, /"height": 4\.0,/);
  assert.match(draft, /\[2\.0, 1\.0, 0\.125, 2\.0\]/);
  assert.match(draft, /"start": \[1\.25, 2\.5\]/);
  const parsed = JSON.parse(draft);
  assert.equal(parsed.schema, "mojum.world.point-room-draft/1");
  assert.deepEqual(parsed.start, room.start);
  assert.deepEqual(parsed.obstacles, room.obstacles);
  assert.equal(floatToken(4), "4.0");
  assert.equal(floatToken(-0), "-0.0");
  assert.equal(floatToken(0.25), "0.25");
  assert.equal(floatToken(1e-8), "1e-8");
  room.heightRaw = "4";
  assert.match(buildDraft(room), /"height": 4,/);
  assert.doesNotMatch(buildDraft(room), /"height": 4\.0,/);
  assert.equal(dimension("4.0").lexical, "4.0");
  assert.equal(dimension("4").lexical, "4");
  assert.equal(startCoordinate("0.0"), 0);
  assert.ok(Object.is(startCoordinate("-0.0"), -0), "signed zero must be retained");
  assert.match(buildDraft({...room, start: [-0, 1]}), /"start": \[-0\.0, 1\.0\]/);
  assert.throws(() => startCoordinate(""), /Координата/);
  assert.throws(() => startCoordinate("  "), /Координата/);
  assert.throws(() => startCoordinate("1e3"), /Координата/);
});

test("invalid dimensions, geometry, obstacle budget and start collisions fail closed", () => {
  for (const bad of ["", "0", "-2", "NaN", "Infinity", "1e3", "1001", "4;alert(1)",
                     "4,5", "  ", "true", "01.0"]) {
    assert.throws(() => dimension(bad), Error, bad);
  }
  for (const bad of [null, true, 4, {}, [], NaN]) {
    assert.throws(() => dimension(bad), Error);
  }
  const room = sample();
  assert.equal(validate(room).height.value, 4);
  room.start = [2, 2.5];
  assert.throws(() => buildDraft(room), /Стартовая точка/);
  room.start = [1.25, 2.5];
  room.obstacles = [[4.4, 0, 0.2, 1]];
  assert.throws(() => buildDraft(room), /Препятствие/);
  room.obstacles = [[2, 1, -0.2, 1]];
  assert.throws(() => buildDraft(room), /Препятствие/);
  room.obstacles = Array(129).fill([0, 0, .1, .1]);
  assert.throws(() => buildDraft(room), /128/);
  room.obstacles = [];
  room.start = [4.6, 2.5];
  assert.throws(() => buildDraft(room), /Стартовая/);
  assert.equal(withinRect(2, 2.5, [2, 1, .125, 2]), true);
  assert.equal(withinRect(1.5, 2.5, [2, 1, .125, 2]), false);
});

test("pointer math respects aspect-ratio letterbox and bottom-left coordinates", () => {
  const wide = { left: 10, top: 20, width: 900, height: 400 };
  // Room 4.5×4 renders at scale 100, horizontally centered with 225px margins.
  assert.deepEqual(pointerToWorld(10 + 225, 20, wide, 4.5, 4), [0, 4]);
  assert.deepEqual(pointerToWorld(10 + 675, 20 + 400, wide, 4.5, 4), [4.5, 0]);
  assert.deepEqual(pointerToWorld(-100, 1000, wide, 4.5, 4), [0, 0]);
  const narrow = { left: 0, top: 0, width: 300, height: 600 };
  assert.deepEqual(pointerToWorld(150, 300, narrow, 4.5, 4), [2.25, 2]);
  assert.equal(snap(1.14, .25), 1.25);
  assert.equal(snap(1.14, 0), 1.14);
  assert.throws(() => pointerToWorld(0, 0, {width:0,height:0}, 4.5, 4));
});

test("generated draft is accepted by real Python Mojum without SQLite writes", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mojum-designer-qa-"));
  try {
    const file = path.join(dir, "room-draft.json");
    const db = path.join(dir, "no-sqlite-must-exist.sqlite3");
    fs.writeFileSync(file, buildDraft(sample()), "utf8");
    const python = process.env.PYTHON_BIN || "python";
    const result = cp.spawnSync(
      python, ["-m", "mojum.room_input", file],
      {
        cwd: root, encoding: "utf8", timeout: 30000,
        env: {...process.env, PYTHONPATH: path.join(root, "src")}
      }
    );
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    const preview = JSON.parse(result.stdout);
    assert.equal(preview.status, "VALID_SYNTHETIC_POINT_ROOM_NO_SQLITE_CREATED");
    assert.equal(preview.width_m, 4.5);
    assert.equal(preview.height_m, 4);
    assert.equal(preview.height_type, "float"); // widthRaw "4.0" preserved
    assert.deepEqual(preview.start, [1.25, 2.5]);
    assert.equal(preview.obstacle_count, 1);
    assert.equal(fs.existsSync(db), false);
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
});

// A tiny in-memory DOM implementation exercises *the actual browser script*
// handlers, not a parallel mock of the geometry logic.
class Element {
  constructor(name) {
    this.name = name; this.children = []; this.handlers = {};
    this.dataset = {}; this.attributes = {}; this.textContent = "";
    this.value = ""; this.checked = false; this.pointerCaptures = new Set();
    this.classList = {toggle() {}};
  }
  addEventListener(name, callback) { (this.handlers[name] ||= []).push(callback); }
  dispatch(name, event = {}) {
    for (const cb of this.handlers[name] || []) cb(event);
  }
  setAttribute(key, val) { this.attributes[key] = String(val); }
  getAttribute(key) { return this.attributes[key]; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  getBoundingClientRect() { return {left: 0, top: 0, width: 450, height: 400}; }
  setPointerCapture(id) { this.pointerCaptures.add(id); }
  hasPointerCapture(id) { return this.pointerCaptures.has(id); }
  releasePointerCapture(id) { this.pointerCaptures.delete(id); }
  remove() {}
}
test("actual offline UI can draw, move start, erase and export valid JSON", () => {
  const ids = [
    "room-canvas", "room-width", "room-height", "start-x", "start-y",
    "snap-grid", "room-json", "obstacle-list", "status",
    "room-dimensions", "obstacle-count", "start-position",
    "clear-obstacles", "copy-json", "download-json"
  ];
  const nodes = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  nodes["room-width"].value = "4.5";
  nodes["room-height"].value = "4";
  nodes["start-x"].value = "1.25";
  nodes["start-y"].value = "2.5";
  nodes["snap-grid"].checked = true;
  const tools = ["draw", "start", "erase"].map(tool => {
    const el = new Element(tool); el.dataset.tool = tool; return el;
  });
  const document = {
    getElementById: id => nodes[id],
    querySelectorAll: selector => selector === "[data-tool]" ? tools : [],
    createElement: tag => new Element(tag),
    createElementNS: (_ns, tag) => new Element(tag),
    body: new Element("body")
  };
  const savedWindow = global.window;
  global.window = {document, navigator:{}, URL:{}, setTimeout};
  try {
    delete require.cache[require.resolve("../tools/room_designer.js")];
    require("../tools/room_designer.js");
    const svg = nodes["room-canvas"];
    assert.equal(JSON.parse(nodes["room-json"].value).obstacles.length, 1);
    assert.equal(nodes.status.dataset.status, "ok");

    // Drag another rectangle x:0.5→1.0, y:0.25→0.75 (initial start safe).
    svg.dispatch("pointerdown", {
      pointerType:"touch", clientX:50, clientY:375, pointerId:1,
      preventDefault() {}
    });
    svg.dispatch("pointermove", {clientX:100, clientY:325, pointerId:1});
    svg.dispatch("pointerup", {clientX:100, clientY:325, pointerId:1});
    assert.equal(JSON.parse(nodes["room-json"].value).obstacles.length, 2);

    // Switch to start-placement mode, tap (0.25, 1.0).
    tools[1].dispatch("click");
    svg.dispatch("pointerdown", {
      pointerType:"touch", clientX:25, clientY:300, pointerId:2
    });
    assert.deepEqual(JSON.parse(nodes["room-json"].value).start, [0.25, 1]);

    // Move the start with a real keyboard handler.
    svg.dispatch("keydown", {
      key:"ArrowUp", preventDefault() {}
    });
    assert.deepEqual(JSON.parse(nodes["room-json"].value).start, [0.25, 1.25]);

    // Remove a rectangle with its actual per-item button.
    assert.equal(nodes["obstacle-list"].children.length, 2);
    nodes["obstacle-list"].children[0].children[1].dispatch("click");
    assert.equal(JSON.parse(nodes["room-json"].value).obstacles.length, 1);

    // A rectangle covering the current start must not be adopted, and the
    // failure should be visible instead of overwritten by the next render.
    tools[0].dispatch("click");
    svg.dispatch("pointerdown", {
      pointerType:"touch", clientX:0, clientY:300, pointerId:3,
      preventDefault() {}
    });
    svg.dispatch("pointermove", {clientX:50, clientY:250, pointerId:3});
    svg.dispatch("pointerup", {clientX:50, clientY:250, pointerId:3});
    assert.equal(JSON.parse(nodes["room-json"].value).obstacles.length, 1);
    assert.equal(nodes.status.dataset.status, "error");

    // Type an invalid dimension; export is disabled fail-closed.
    nodes["room-width"].value = "99999";
    nodes["room-width"].dispatch("input");
    assert.equal(nodes["room-json"].value, "");
    assert.equal(nodes.status.dataset.status, "error");
    nodes["room-width"].value = "4.5";
    nodes["room-width"].dispatch("input");
    nodes["start-x"].value = "";
    nodes["start-x"].dispatch("input");
    assert.equal(nodes["room-json"].value, "");
    assert.equal(nodes.status.dataset.status, "error");

    // Restore a valid dimension/coordinate while retaining the model.
    nodes["start-x"].value = "0.25";
    nodes["room-width"].value = "4.5";
    nodes["room-width"].dispatch("input");
    assert.equal(nodes.status.dataset.status, "ok");
    assert.equal(JSON.parse(nodes["room-json"].value).obstacles.length, 1);
  } finally {
    if (savedWindow === undefined) delete global.window;
    else global.window = savedWindow;
    delete require.cache[require.resolve("../tools/room_designer.js")];
  }
});
test("standalone offline HTML contains all active accessible controls, no remote dependencies", () => {
  const html = fs.readFileSync(path.join(root, "tools/room_designer.html"), "utf8");
  for (const id of [
    "room-canvas","room-width","room-height","start-x","start-y",
    "snap-grid","room-json","obstacle-list","status","download-json",
    "copy-json","clear-obstacles"
  ]) assert.match(html, new RegExp('id="' + id + '"'));
  assert.match(html, /<script src="room_designer\.js" defer><\/script>/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /touch-action: none/);
  assert.doesNotMatch(html, /<script[^>]*src="https?:/i);
  assert.doesNotMatch(html, /<link[^>]*href="https?:/i);
});
