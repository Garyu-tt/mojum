/* Mojum R0.15: isolated offline point-room walk preview.
 * Mirrors src/mojum/world_room.py for deterministic 2D swept point-vs-AABB
 * geometry only; no neural agent, persistence, telemetry or real physics.
 * Required input: C.validate(roomDraft) -> numeric dimensions and rectangles.
 */
(function (root) {
  "use strict";
  const SCHEMA = "mojum.world.point-room-walk-preview/1";
  const MAX_PREVIEW_STEPS = 512;

  function valid(room, x, y) {
    return Number.isFinite(x) && Number.isFinite(y) &&
      x >= 0 && y >= 0 && x <= room.width && y <= room.height &&
      !room.obstacles.some(r =>
        r.x <= x && x <= r.x + r.w &&
        r.y <= y && y <= r.y + r.h);
  }

  function segmentHitsRect(x0, y0, x1, y1, rect) {
    const dx = x1 - x0, dy = y1 - y0;
    let enter = 0, leave = 1;
    for (const [p, q] of [
      [-dx, x0 - rect.x],
      [ dx, rect.x + rect.w - x0],
      [-dy, y0 - rect.y],
      [ dy, rect.y + rect.h - y0],
    ]) {
      if (p === 0) {
        if (q < 0) return false;
      } else {
        const ratio = q / p;
        if (p < 0) {
          if (ratio > leave) return false;
          enter = Math.max(enter, ratio);
        } else {
          if (ratio < enter) return false;
          leave = Math.min(leave, ratio);
        }
      }
    }
    return enter <= leave;
  }

  function begin(room) {
    if (!room || !room.start || !valid(room, room.start.x, room.start.y)) {
      throw new Error("Тестовая прогулка: старт вне допустимой комнаты");
    }
    return {
      schema: SCHEMA, tick: 0,
      x: room.start.x, y: room.start.y,
      blocked: 0,
      trail: [{x: room.start.x, y: room.start.y}],
    };
  }

  function validTraceState(room, state) {
    if (!Number.isInteger(state.blocked) || state.blocked < 0 ||
        state.blocked > state.tick || !Array.isArray(state.trail) ||
        state.trail.length !== state.tick + 1) return false;
    // A for-loop deliberately detects holes in a forged sparse array; .some()
    // skips those slots. The fixed 512-step budget bounds validation work.
    for (let i = 0; i < state.trail.length; i++) {
      const point = state.trail[i];
      if (!point || typeof point !== "object" ||
          !valid(room, point.x, point.y)) return false;
    }
    const last = state.trail[state.tick];
    return last.x === state.x && last.y === state.y;
  }

  function step(room, state, dx, dy) {
    if (!state || state.schema !== SCHEMA ||
        !Number.isInteger(state.tick) || state.tick < 0 ||
        state.tick >= MAX_PREVIEW_STEPS) {
      throw new Error("Лимит тестовой прогулки — 512 шагов. Сбросьте прогулку");
    }
    // A trace is ephemeral, but it must never display forged collision
    // counts, oversized history or an invalid path as measured evidence.
    if (!validTraceState(room, state) ||
        !Number.isFinite(dx) || !Number.isFinite(dy) ||
        Math.hypot(dx, dy) > 1000 || !valid(room, state.x, state.y)) {
      throw new Error("Некорректный шаг или исходное состояние");
    }
    const tx = state.x + dx, ty = state.y + dy;
    const blocked = !valid(room, tx, ty) || room.obstacles.some(
      r => segmentHitsRect(state.x, state.y, tx, ty, r));
    const x = blocked ? state.x : tx;
    const y = blocked ? state.y : ty;
    return {
      schema: SCHEMA, tick: state.tick + 1,
      x, y,
      blocked: state.blocked + (blocked ? 1 : 0),
      trail: state.trail.concat([{x,y}]),
      last: {
        tick: state.tick + 1, position: [x,y], blocked,
        provenance: "deterministic-point-room-fixture/v1",
        physical_contact: false,
      },
    };
  }

  const api = Object.freeze({
    SCHEMA, MAX_PREVIEW_STEPS, valid, segmentHitsRect, begin, step,
  });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.MojumWalkPreview = api;
})(typeof window !== "undefined" ? window : globalThis);
