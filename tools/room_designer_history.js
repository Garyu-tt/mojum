/* Local bounded undo/redo for the browser room designer.
 * Every snapshot stores original numeric string tokens, not JS float values.
 * No browser storage, network, timers or dependencies.
 */
(function (root) {
  "use strict";

  function clone(state) {
    return {
      width: state.width, height: state.height,
      start: { x: state.start.x, y: state.start.y },
      obstacles: state.obstacles.map(r => ({
        x: r.x, y: r.y, w: r.w, h: r.h,
      })),
    };
  }
  function equals(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  function create(initial, limit = 64) {
    if (!Number.isInteger(limit) || limit < 2 || limit > 256) {
      throw new Error("История комнаты: требуется бюджет от 2 до 256 состояний");
    }
    return { entries: [clone(initial)], index: 0, limit };
  }
  function current(history) {
    return clone(history.entries[history.index]);
  }
  function record(history, state) {
    const snapshot = clone(state);
    if (equals(history.entries[history.index], snapshot)) return history;
    // If an edit follows Undo, never re-expose the discarded redo branch.
    const entries = history.entries.slice(0, history.index + 1);
    entries.push(snapshot);
    if (entries.length > history.limit) entries.splice(0, entries.length - history.limit);
    return { entries, index: entries.length - 1, limit: history.limit };
  }
  function canUndo(history) { return history.index > 0; }
  function canRedo(history) { return history.index < history.entries.length - 1; }
  function undo(history) {
    if (!canUndo(history)) return { history, state: current(history), changed: false };
    const next = { ...history, index: history.index - 1 };
    return { history: next, state: current(next), changed: true };
  }
  function redo(history) {
    if (!canRedo(history)) return { history, state: current(history), changed: false };
    const next = { ...history, index: history.index + 1 };
    return { history: next, state: current(next), changed: true };
  }
  const api = Object.freeze({ create, record, current, canUndo, canRedo, undo, redo });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.MojumRoomHistory = api;
})(typeof window !== "undefined" ? window : globalThis);
