/* Mojum local point-room draft/1 model. No network, storage or dependencies.
 * Intentionally retain the ORIGINAL JSON numeric token strings: 4 and 4.0
 * must stay distinct because world_room._revision() hashes those types.
 * This file is both a classic file:// browser script and a Node CJS module.
 */
(function (root) {
  "use strict";
  const SCHEMA = "mojum.world.point-room-draft/1";
  const MAX_DIMENSION_M = 1000;
  const MAX_EDITOR_OBSTACLES = 64;
  const NUMERIC_TOKEN = /^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,15})?$/;

  const DEFAULT = Object.freeze({
    width: "4.5",
    height: "4",
    start: { x: "1.25", y: "2.5" },
    obstacles: [{ x: "2.0", y: "1", w: "0.125", h: "2.0" }],
  });

  function clone(state) {
    return {
      width: state.width,
      height: state.height,
      start: { x: state.start.x, y: state.start.y },
      obstacles: state.obstacles.map(r => ({ x: r.x, y: r.y, w: r.w, h: r.h })),
    };
  }

  function token(text, label) {
    // Not parseFloat: "1e999", "2junk", NaN, -0, booleans and executable
    // fragments must never be transformed into JSON from user input.
    if (typeof text !== "string" || !NUMERIC_TOKEN.test(text)) {
      throw new Error(label + ": требуется число 0, 1, 1.25 или 4.0");
    }
    const number = Number(text);
    if (!Number.isFinite(number) || number > MAX_DIMENSION_M) {
      throw new Error(label + ": значение должно быть не больше 1000 м");
    }
    return number;
  }

  function validate(state) {
    if (!state || typeof state !== "object" ||
        !state.start || !Array.isArray(state.obstacles) ||
        state.obstacles.length > MAX_EDITOR_OBSTACLES) {
      throw new Error("Некорректная комната или больше 64 препятствий");
    }
    const width = token(state.width, "Ширина");
    const height = token(state.height, "Высота");
    if (width <= 0 || height <= 0) {
      throw new Error("Ширина и высота должны быть больше нуля");
    }
    const sx = token(state.start.x, "Старт X");
    const sy = token(state.start.y, "Старт Y");
    if (sx > width || sy > height) {
      throw new Error("Начальная точка вне комнаты");
    }
    const rects = state.obstacles.map((rect, i) => {
      const x = token(rect.x, "Препятствие " + (i + 1) + ": X");
      const y = token(rect.y, "Препятствие " + (i + 1) + ": Y");
      const w = token(rect.w, "Препятствие " + (i + 1) + ": ширина");
      const h = token(rect.h, "Препятствие " + (i + 1) + ": высота");
      if (w <= 0 || h <= 0) {
        throw new Error("Препятствие " + (i + 1) + ": размеры должны быть > 0");
      }
      // Tolerance is NOT used: Python Room checks exact numeric boundaries.
      if (x + w > width || y + h > height) {
        throw new Error("Препятствие " + (i + 1) + " выходит за границу");
      }
      if (x <= sx && sx <= x + w && y <= sy && sy <= y + h) {
        throw new Error("Начальная точка пересекает препятствие " + (i + 1));
      }
      return { x, y, w, h };
    });
    return { width, height, start: { x: sx, y: sy }, obstacles: rects };
  }

  function draftJSON(state) {
    validate(state);
    const lines = state.obstacles.map(
      r => "    [" + [r.x, r.y, r.w, r.h].join(", ") + "]"
    );
    // Explicit lexical number composition is intentional. JSON.stringify
    // would change 4.0 to 4, silently changing future world revision hashes.
    return [
      "{",
      '  "schema": "' + SCHEMA + '",',
      '  "width": ' + state.width + ",",
      '  "height": ' + state.height + ",",
      '  "obstacles": [',
      lines.join(",\n"),
      "  ],",
      '  "start": [' + state.start.x + ", " + state.start.y + "]",
      "}",
      "",
    ].join("\n");
  }

  function fromDraftJSON(raw) {
    // Only import canonical drafts previously produced by this editor.
    // JSON.parse alone discards duplicate keys and turns 4.0 into 4; a
    // byte-normalized canonical check detects ambiguity rather than
    // accidentally altering the organism's future world revision.
    if (typeof raw !== "string" || new TextEncoder().encode(raw).length > 1_048_576) {
      throw new Error("Файл JSON слишком большой или повреждён");
    }
    let parsed;
    try {
      parsed = JSON.parse(raw, (key, value, context) => {
        if (typeof value !== "number") return value;
        if (!context || typeof context.source !== "string") {
          throw new Error("Этот браузер не умеет сохранять точные числовые токены при импорте");
        }
        return { __mojum_numeric_token: context.source };
      });
    } catch (error) {
      throw new Error("Не удалось прочитать JSON: " + error.message);
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)
        || Object.keys(parsed).sort().join(",") !== "height,obstacles,schema,start,width"
        || parsed.schema !== SCHEMA
        || !Array.isArray(parsed.start) || parsed.start.length !== 2
        || !Array.isArray(parsed.obstacles)) {
      throw new Error("Поддерживается только собственный формат проекта комнаты");
    }
    const unpack = item => {
      if (!item || typeof item !== "object" || Array.isArray(item)
          || Object.keys(item).join(",") !== "__mojum_numeric_token") {
        throw new Error("Некорректное число в файле комнаты");
      }
      return item.__mojum_numeric_token;
    };
    const recovered = {
      width: unpack(parsed.width),
      height: unpack(parsed.height),
      start: { x: unpack(parsed.start[0]), y: unpack(parsed.start[1]) },
      obstacles: parsed.obstacles.map(rect => {
        if (!Array.isArray(rect) || rect.length !== 4) {
          throw new Error("Препятствие должно содержать четыре числа");
        }
        return {
          x: unpack(rect[0]), y: unpack(rect[1]),
          w: unpack(rect[2]), h: unpack(rect[3]),
        };
      }),
    };
    validate(recovered);
    if (raw.trim() !== draftJSON(recovered).trim()) {
      throw new Error("Изменённый или неоднозначный JSON: импортируйте файл, экспортированный редактором");
    }
    return recovered;
  }

  function fmt(number, previous = "") {
    if (!Number.isFinite(number) || number < 0 || number > 1000) {
      throw new Error("Координата вне области редактора");
    }
    // 1 mm grid for interactive dragging, no drift accumulating from fractions.
    const rounded = Number(number.toFixed(3));
    let value = String(rounded);
    if (previous.includes(".") && !value.includes(".")) value += ".0";
    return value;
  }

  function dragObstacle(state, index, x, y) {
    const checked = validate(state);
    if (!Number.isInteger(index) || index < 0 || index >= state.obstacles.length) {
      throw new Error("Неизвестное препятствие");
    }
    const chosen = checked.obstacles[index], old = state.obstacles[index];
    const updated = clone(state);
    updated.obstacles[index].x = fmt(Math.min(Math.max(0, x), checked.width - chosen.w), old.x);
    updated.obstacles[index].y = fmt(Math.min(Math.max(0, y), checked.height - chosen.h), old.y);
    // Invalid start intersections are not committed by dragging.
    validate(updated);
    return updated;
  }

  function dragStart(state, x, y) {
    const checked = validate(state);
    const updated = clone(state);
    updated.start.x = fmt(Math.min(Math.max(0, x), checked.width), state.start.x);
    updated.start.y = fmt(Math.min(Math.max(0, y), checked.height), state.start.y);
    validate(updated);
    return updated;
  }

  const api = Object.freeze({
    SCHEMA, MAX_EDITOR_OBSTACLES, DEFAULT, clone, token,
    validate, draftJSON, fromDraftJSON, fmt, dragObstacle, dragStart,
  });
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.MojumRoomDesignerCore = api;
})(typeof window !== "undefined" ? window : globalThis);
