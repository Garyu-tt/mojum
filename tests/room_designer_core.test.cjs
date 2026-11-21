"use strict";
/* Native Node tests: no npm install, browser automation or external services. */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../tools/room_designer_core.js");

const fresh = () => C.clone(C.DEFAULT);

test("default visual room is valid and authoring JSON matches existing simple fixture", () => {
  const room = fresh(), view = C.validate(room);
  assert.equal(view.width, 4.5);
  assert.equal(view.height, 4);
  assert.deepEqual(view.start, {x: 1.25, y: 2.5});
  const parsed = JSON.parse(C.draftJSON(room));
  const example = JSON.parse(fs.readFileSync(
    path.join(__dirname, "../examples/room_simple_mixed.json"), "utf8"));
  assert.deepEqual(parsed, example);
  assert.equal(parsed.schema, C.SCHEMA);
});

test("preserve integer and decimal JSON tokens rather than JSON.stringify coercion", () => {
  const room = fresh();
  room.height = "4.0";
  room.obstacles[0].x = "2.0";
  room.obstacles[0].y = "1.0";
  room.start.x = "1.250";
  const json = C.draftJSON(room);
  assert.match(json, /"height": 4\.0,/);
  assert.match(json, /"start": \[1\.250, 2\.5\]/);
  assert.match(json, /\[2\.0, 1\.0, 0\.125, 2\.0\]/);
  assert.equal(JSON.parse(json).height, 4);
  // String lexical preservation is essential: Python json.loads(4.0)
  // creates float, while json.loads(4) creates int, affecting world revision.
  room.height = "4";
  assert.match(C.draftJSON(room), /"height": 4,/);
  assert.notEqual(C.draftJSON(room), json);
});

test("strictly reject invalid numeric text, nonfinite and code injection", () => {
  for (const text of [
    "", " 4", "4 ", "-0", "-1", "04", "0.", ".5", "4e0",
    "1e999", "Infinity", "NaN", "1<script>", "1, 2",
    "1;alert(1)", "1001", "10000000000000000000", "1.1234567890123456",
  ]) {
    const room = fresh();
    room.width = text;
    assert.throws(() => C.draftJSON(room), undefined, "accepted " + text);
  }
  assert.equal(C.token("4.0", "value"), 4);
  assert.equal(C.token("0", "value"), 0);
});

test("start must stay in bounds and outside every obstacle including edges", () => {
  const room = fresh();
  room.start.x = "5";
  assert.throws(() => C.validate(room), /вне комнаты/);
  room.start.x = "2.0";
  room.start.y = "2";
  assert.throws(() => C.validate(room), /пересекает препятствие/);
  room.start.x = "2.125"; // touching the rectangle counts as collision
  assert.throws(() => C.validate(room), /пересекает препятствие/);
  room.start.x = "4.5";
  room.start.y = "4";
  assert.doesNotThrow(() => C.validate(room));
});

test("obstacle scalar, position, size, quota and room dimensions guarded", () => {
  const room = fresh();
  room.obstacles[0].x = "4.4";
  assert.throws(() => C.draftJSON(room), /выходит за границу/);
  room.obstacles[0].x = "2.0";
  room.obstacles[0].w = "0";
  assert.throws(() => C.validate(room), /размеры/);
  room.obstacles[0].w = "0.125";
  room.obstacles[0].y = "1.25";
  room.obstacles[0].h = "2.0";
  assert.doesNotThrow(() => C.validate(room));
  room.height = "0";
  assert.throws(() => C.validate(room), /больше нуля/);
  room.height = "4";
  room.obstacles = Array.from({length:65}, () => ({x:"0",y:"0",w:"0.01",h:"0.01"}));
  assert.throws(() => C.validate(room), /больше 64/);
  assert.equal(C.MAX_EDITOR_OBSTACLES, 64);
});

test("drag clamps to room and preserves decimal scalar class without modifying prior input", () => {
  const before = fresh();
  const moved = C.dragObstacle(before, 0, 3, 2);
  assert.equal(moved.obstacles[0].x, "3.0");
  assert.equal(moved.obstacles[0].y, "2");
  assert.equal(before.obstacles[0].x, "2.0");
  assert.equal(before.obstacles[0].y, "1");
  const clamped = C.dragObstacle(before, 0, 100, 100);
  assert.equal(C.validate(clamped).obstacles[0].x, 4.375);
  assert.equal(C.validate(clamped).obstacles[0].y, 2);
  assert.throws(() => C.dragObstacle(before, 0, 1.25, 1.5), /пересекает препятствие/);
  const startMoved = C.dragStart(before, 4.9, 4.9);
  assert.deepEqual(C.validate(startMoved).start, {x:4.5,y:4});
  assert.throws(() => C.dragStart(before, 2.05, 1.5), /пересекает препятствие/);
  assert.throws(() => C.dragObstacle(before, 5, 2, 2), /Неизвестное/);
});

test("offline HTML has all real controls with no remote script or network fetch", () => {
  const html = fs.readFileSync(
    path.join(__dirname,"../tools/room_designer.html"),"utf8");
  const ids = [
    "scene","add-obstacle","delete-obstacle","reset-room","status",
    "width","height","start-x","start-y","obstacle-list",
    "ob-x","ob-y","ob-w","ob-h","output","copy","download",
  ];
  ids.forEach(id=> assert.ok(html.includes('id="' + id + '"'), id));
  assert.match(html, /<script src="\.\/room_designer_core\.js"><\/script>/);
  assert.doesNotMatch(html, /<script[^>]+https?:\/\//i);
  assert.doesNotMatch(html, /\b(fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(/);
  assert.match(html, /setPointerCapture/);
  assert.match(html, /pointermove/);
  assert.match(html, /navigator\.clipboard\.writeText/);
  assert.match(html, /URL\.createObjectURL/);
  assert.match(html, /aria-live="polite"/);
});
