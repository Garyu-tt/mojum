"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const H = require("../tools/room_designer_history.js");
const C = require("../tools/room_designer_core.js");

const fresh = () => C.clone(C.DEFAULT);

test("new room history starts with no undo and no redo", () => {
  const h = H.create(fresh());
  assert.equal(H.canUndo(h), false);
  assert.equal(H.canRedo(h), false);
  assert.equal(H.undo(h).changed, false);
  assert.equal(H.redo(h).changed, false);
  assert.deepEqual(H.current(h), fresh());
  assert.throws(() => H.create(fresh(), 1), /бюджет/);
  assert.throws(() => H.create(fresh(), 257), /бюджет/);
});

test("undo/redo restores literal numeric tokens, not coerced JS Number values", () => {
  let history = H.create(fresh());
  let altered = fresh();
  altered.height = "4.0";
  altered.start.x = "1.250";
  history = H.record(history, altered);
  assert.equal(H.canUndo(history), true);
  assert.equal(H.current(history).height, "4.0");
  let result = H.undo(history);
  assert.equal(result.changed, true);
  assert.equal(result.state.height, "4");
  assert.equal(result.state.start.x, "1.25");
  result = H.redo(result.history);
  assert.equal(result.changed, true);
  assert.equal(result.state.height, "4.0");
  assert.equal(result.state.start.x, "1.250");
  assert.match(C.draftJSON(result.state), /"height": 4\.0/);
  assert.match(C.draftJSON(result.state), /"start": \[1\.250, 2\.5\]/);
});

test("history snapshots are independent copies, no caller mutation can rewrite past", () => {
  const editable = fresh();
  let history = H.create(editable);
  editable.width = "8.0";
  assert.equal(H.current(history).width, "4.5");
  history = H.record(history, editable);
  editable.width = "9.0";
  assert.equal(H.current(history).width, "8.0");
  const extracted = H.current(history);
  extracted.start.x = "999";
  extracted.obstacles[0].w = "42";
  assert.equal(H.current(history).start.x, "1.25");
  assert.equal(H.current(history).obstacles[0].w, "0.125");
});

test("zero-change gesture never creates duplicate history entries", () => {
  let history = H.create(fresh());
  const same = fresh();
  assert.equal(H.record(history, same), history);
  same.width = "5";
  history = H.record(history, same);
  assert.equal(H.record(history, C.clone(same)), history);
  assert.equal(history.entries.length, 2);
});

test("new edit after undo discards redo tail", () => {
  let h = H.create(fresh());
  let a = fresh(); a.width = "5";
  let b = fresh(); b.width = "6";
  h = H.record(h, a);
  h = H.record(h, b);
  assert.equal(h.entries.length, 3);
  let old = H.undo(h);
  assert.equal(old.state.width, "5");
  assert.equal(H.canRedo(old.history), true);
  let c = fresh(); c.width = "7";
  const branch = H.record(old.history, c);
  assert.equal(H.current(branch).width, "7");
  assert.equal(H.canRedo(branch), false);
  assert.equal(H.redo(branch).changed, false);
  assert.equal(H.undo(branch).state.width, "5");
});

test("bounded history uses 64 states and retains the most recent edits", () => {
  let h = H.create(fresh(), 4);
  for (const width of ["5", "6", "7", "8", "9"]) {
    const next = fresh(); next.width = width;
    h = H.record(h, next);
    assert.ok(h.entries.length <= 4);
  }
  assert.equal(h.entries.length, 4);
  assert.equal(H.current(h).width, "9");
  let steps = 0;
  while (H.canUndo(h)) {
    h = H.undo(h).history;
    steps += 1;
  }
  assert.equal(steps, 3);
  assert.equal(H.current(h).width, "6");
  assert.equal(H.canUndo(h), false);
});
