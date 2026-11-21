/* Mojum offline Room Designer R0.15: no network, dependencies or SQLite writes.
 * Authoring only; exported draft is validated by mojum.room_input CLI.
 * Keep the exact lexical distinction between JSON 4 and 4.0: never stringify
 * the generated numbers through JSON.stringify(), which erases float tags.
 */
(function (root) {
  "use strict";
  const SCHEMA = "mojum.world.point-room-draft/1";
  const MAX_DIM = 1000;
  const MAX_OBSTACLES = 128; // UI budget; Python core supports 4096
  const DIM_RE = /^(?:0|[1-9]\d{0,3})(?:\.\d{1,12})?$/;
  const SVG_NS = "http://www.w3.org/2000/svg";

  function dimension(raw) {
    if (typeof raw !== "string" || !DIM_RE.test(raw.trim())) {
      throw new Error("Размер вводится числом без экспоненты (например 4 или 4.5).");
    }
    const lexical = raw.trim();
    const value = Number(lexical);
    if (!Number.isFinite(value) || value <= 0 || value > MAX_DIM) {
      throw new Error("Размер комнаты должен быть больше нуля и не превышать 1000 м.");
    }
    return { value, lexical };
  }

  function startCoordinate(raw) {
    if (typeof raw !== "string" ||
        !/^-?(?:0|[1-9]\d{0,3})(?:\.\d{1,12})?$/.test(raw.trim())) {
      throw new Error("Координата старта должна быть десятичным числом.");
    }
    const result = Number(raw.trim());
    if (!Number.isFinite(result)) throw new Error("Некорректная координата старта.");
    return result;
  }

  function numeric(x, label) {
    if (typeof x !== "number" || !Number.isFinite(x)) {
      throw new Error(label + ": требуется конечное число.");
    }
    return x;
  }

  function withinRect(x, y, rect) {
    return x >= rect[0] && x <= rect[0] + rect[2] &&
           y >= rect[1] && y <= rect[1] + rect[3];
  }

  function validate(state) {
    if (!state || typeof state !== "object") throw new Error("Нет модели комнаты.");
    const width = dimension(state.widthRaw);
    const height = dimension(state.heightRaw);
    if (!Array.isArray(state.obstacles) || state.obstacles.length > MAX_OBSTACLES) {
      throw new Error("Максимум 128 препятствий в редакторе.");
    }
    if (!Array.isArray(state.start) || state.start.length !== 2) {
      throw new Error("Не задана стартовая точка.");
    }
    const [sx, sy] = state.start.map((v) => numeric(v, "Старт"));
    if (sx < 0 || sy < 0 || sx > width.value || sy > height.value) {
      throw new Error("Стартовая точка находится за пределами комнаты.");
    }
    for (const rect of state.obstacles) {
      if (!Array.isArray(rect) || rect.length !== 4) {
        throw new Error("Препятствие должно иметь x, y, ширину и высоту.");
      }
      const [x, y, w, h] = rect.map((v) => numeric(v, "Препятствие"));
      if (x < 0 || y < 0 || w <= 0 || h <= 0 ||
          x + w > width.value || y + h > height.value) {
        throw new Error("Препятствие выходит за границы комнаты или имеет нулевой размер.");
      }
      if (withinRect(sx, sy, rect)) {
        throw new Error("Стартовая точка не может находиться на препятствии.");
      }
    }
    return { width, height, start: [sx, sy], obstacles: state.obstacles };
  }

  // floatToken always emits a decimal marker, including for integer-valued
  // coordinates. Python JSON loader will therefore keep them as float.
  function floatToken(value) {
    numeric(value, "Координата");
    if (Object.is(value, -0)) return "-0.0";
    const str = String(value);
    if (/[eE]/.test(str)) {
      // JSON permits exponents and Python reads such literals as floats.
      return str;
    }
    return str.includes(".") ? str : str + ".0";
  }

  function buildDraft(state) {
    const room = validate(state);
    const obstacleLines = room.obstacles.map((rect) =>
      "    [" + rect.map(floatToken).join(", ") + "]"
    );
    return [
      "{",
      '  "schema": "' + SCHEMA + '",',
      '  "width": ' + room.width.lexical + ",",
      '  "height": ' + room.height.lexical + ",",
      '  "obstacles": [' + (obstacleLines.length ? "\n" + obstacleLines.join(",\n") + "\n  " : "") + "],",
      '  "start": [' + room.start.map(floatToken).join(", ") + "]",
      "}"
    ].join("\n") + "\n";
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function snap(v, size) {
    if (!size) return Math.round(v * 1000) / 1000;
    return Math.round(Math.round(v / size) * size * 1000) / 1000;
  }
  // Grid rounding can overshoot a non-grid-aligned wall (e.g. 4.9 -> 5.0).
  // Both touch and keyboard placement must retain the exact room boundary.
  function snapWithin(v, extent, size) {
    return clamp(snap(clamp(v, 0, extent), size), 0, extent);
  }

  // SVG preserveAspectRatio="xMidYMid meet". Letterbox offsets matter,
  // particularly on portrait Android screens. Cartesian origin = bottom-left.
  function pointerToWorld(clientX, clientY, bounds, width, height) {
    if (!bounds || bounds.width <= 0 || bounds.height <= 0 ||
        width <= 0 || height <= 0) throw new Error("Неверный размер холста.");
    const scale = Math.min(bounds.width / width, bounds.height / height);
    const left = bounds.left + (bounds.width - width * scale) / 2;
    const top = bounds.top + (bounds.height - height * scale) / 2;
    return [
      clamp((clientX - left) / scale, 0, width),
      clamp(height - (clientY - top) / scale, 0, height)
    ];
  }

  const API = { SCHEMA, MAX_OBSTACLES, dimension, startCoordinate, validate, buildDraft,
                floatToken, pointerToWorld, snap, snapWithin, withinRect };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  if (!root || !root.document) return;
  const doc = root.document;
  const svg = doc.getElementById("room-canvas");
  if (!svg) return; // safely loadable in an unrelated browser page

  const state = {
    widthRaw: "4.5", heightRaw: "4", start: [1.25, 2.5],
    obstacles: [[2, 1, 0.125, 2]], tool: "draw", grid: true,
    pending: null, pendingPointerId: null, latest: "", lastValidSize: [4.5, 4]
  };
  const refs = {
    width: doc.getElementById("room-width"),
    height: doc.getElementById("room-height"),
    startX: doc.getElementById("start-x"),
    startY: doc.getElementById("start-y"),
    grid: doc.getElementById("snap-grid"),
    preview: doc.getElementById("room-json"),
    obstacles: doc.getElementById("obstacle-list"),
    message: doc.getElementById("status"),
    dimensions: doc.getElementById("room-dimensions"),
    count: doc.getElementById("obstacle-count"),
    position: doc.getElementById("start-position")
  };

  function elt(tag, attributes) {
    const node = doc.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attributes || {})) {
      node.setAttribute(key, String(value));
    }
    return node;
  }
  function labelMessage(message, ok) {
    refs.message.textContent = message;
    refs.message.dataset.status = ok ? "ok" : "error";
  }
  function setTool(tool) {
    state.tool = tool;
    state.pending = null;
    state.pendingPointerId = null;
    doc.querySelectorAll("[data-tool]").forEach((button) => {
      const active = button.dataset.tool === tool;
      button.setAttribute("aria-pressed", String(active));
      button.classList.toggle("is-selected", active);
    });
    render();
  }
  function currentRoom() {
    state.widthRaw = refs.width.value;
    state.heightRaw = refs.height.value;
    state.start[0] = startCoordinate(refs.startX.value);
    state.start[1] = startCoordinate(refs.startY.value);
    return validate(state);
  }
  function gridStep() { return state.grid ? 0.25 : 0; }
  function pointer(event, size) {
    const world = pointerToWorld(
      event.clientX, event.clientY, svg.getBoundingClientRect(),
      size[0], size[1]);
    return [snapWithin(world[0], size[0], gridStep()),
            snapWithin(world[1], size[1], gridStep())];
  }
  function drawScene(width, height) {
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.replaceChildren();
    svg.appendChild(elt("rect", {
      x: 0, y: 0, width, height, fill: "var(--canvas)"
    }));
    const line = Math.max(0.015, Math.min(width, height) / 250);
    // Bound grid lines to avoid O(1000m / 0.25m) DOM work.
    const spacing = Math.max(0.25, Math.ceil(Math.max(width, height) / 64 * 4) / 4);
    for (let x = spacing; x < width; x += spacing) {
      svg.appendChild(elt("line", {
        x1: x, y1: 0, x2: x, y2: height,
        stroke: "var(--grid)", "stroke-width": line / 2
      }));
    }
    for (let y = spacing; y < height; y += spacing) {
      svg.appendChild(elt("line", {
        x1: 0, y1: y, x2: width, y2: y,
        stroke: "var(--grid)", "stroke-width": line / 2
      }));
    }
    for (const rect of state.obstacles) {
      svg.appendChild(elt("rect", {
        x: rect[0], y: height - rect[1] - rect[3],
        width: rect[2], height: rect[3],
        rx: 0.025, fill: "var(--wall)",
        stroke: "var(--wall-stroke)", "stroke-width": line,
        "pointer-events": "none"
      }));
    }
    if (state.pending) {
      const [a, b] = state.pending;
      svg.appendChild(elt("rect", {
        x: Math.min(a[0], b[0]), y: height - Math.max(a[1], b[1]),
        width: Math.abs(a[0] - b[0]), height: Math.abs(a[1] - b[1]),
        fill: "var(--ghost)", stroke: "var(--accent)",
        "stroke-width": line, "stroke-dasharray": Math.max(line * 4, 0.05),
        "pointer-events": "none"
      }));
    }
    svg.appendChild(elt("circle", {
      cx: state.start[0], cy: height - state.start[1],
      r: Math.max(line * 3, Math.min(width, height) / 35),
      fill: "var(--accent)", stroke: "var(--canvas)",
      "stroke-width": line * 2, "pointer-events": "none"
    }));
  }
  function renderList() {
    refs.obstacles.replaceChildren();
    state.obstacles.forEach((rect, index) => {
      const item = doc.createElement("li");
      const title = doc.createElement("span");
      title.textContent = "#" + (index + 1) + " · (" +
        rect[0] + ", " + rect[1] + ") · " + rect[2] + "×" + rect[3] + " м";
      const button = doc.createElement("button");
      button.type = "button";
      button.textContent = "Удалить";
      button.setAttribute("aria-label", "Удалить препятствие " + (index + 1));
      button.addEventListener("click", () => {
        state.obstacles.splice(index, 1);
        state.pending = null;
        render();
      });
      item.append(title, button);
      refs.obstacles.appendChild(item);
    });
  }
  function render() {
    let room;
    try {
      room = currentRoom();
      state.lastValidSize = [room.width.value, room.height.value];
      state.latest = buildDraft(state);
      refs.preview.value = state.latest;
      refs.dimensions.textContent = room.width.lexical + " × " + room.height.lexical + " м";
      refs.count.textContent = String(room.obstacles.length);
      refs.position.textContent = room.start.map((v) => v.toFixed(2)).join(", ");
      labelMessage("Модель корректна · готова к экспорту", true);
    } catch (error) {
      state.latest = "";
      refs.preview.value = "";
      labelMessage(error.message, false);
    }
    drawScene(...state.lastValidSize);
    renderList();
  }
  function setStart(x, y) {
    const old = state.start.slice();
    state.start = [x, y];
    refs.startX.value = String(x);
    refs.startY.value = String(y);
    try { currentRoom(); } catch (error) {
      state.start = old;
      refs.startX.value = String(old[0]);
      refs.startY.value = String(old[1]);
      labelMessage(error.message, false);
      return;
    }
    render();
  }
  svg.addEventListener("pointerdown", (event) => {
    if (state.pending) return; // Ignore additional fingers during an active drag.
    if (event.button !== 0 && event.pointerType === "mouse") return;
    try {
      const room = currentRoom();
      const p = pointer(event, [room.width.value, room.height.value]);
      if (state.tool === "start") {
        setStart(p[0], p[1]);
      } else if (state.tool === "erase") {
        for (let i = state.obstacles.length - 1; i >= 0; --i) {
          if (withinRect(p[0], p[1], state.obstacles[i])) {
            state.obstacles.splice(i, 1);
            render();
            break;
          }
        }
      } else if (state.tool === "draw") {
        event.preventDefault();
        state.pending = [p, p];
        state.pendingPointerId = event.pointerId;
        svg.setPointerCapture(event.pointerId);
      }
    } catch (error) { labelMessage(error.message, false); }
  });
  svg.addEventListener("pointermove", (event) => {
    if (!state.pending || event.pointerId !== state.pendingPointerId) return;
    const [width, height] = state.lastValidSize;
    state.pending[1] = pointer(event, [width, height]);
    drawScene(width, height);
  });
  function finishDraw(event) {
    if (!state.pending || event.pointerId !== state.pendingPointerId) return;
    let rejected = null;
    try {
      const [width, height] = state.lastValidSize;
      state.pending[1] = pointer(event, [width, height]);
      const [a, b] = state.pending;
      state.pending = null;
      const rect = [
        Math.min(a[0], b[0]), Math.min(a[1], b[1]),
        Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])
      ];
      if (rect[2] < 0.01 || rect[3] < 0.01) {
        rejected = "Протяни мышью или пальцем, чтобы нарисовать препятствие.";
      } else {
        state.obstacles.push(rect);
        try { validate(state); } catch (error) {
          state.obstacles.pop();
          rejected = error.message;
        }
      }
    } finally {
      state.pending = null;
      state.pendingPointerId = null;
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
      render();
      if (rejected) labelMessage(rejected, false);
    }
  }
  svg.addEventListener("pointerup", finishDraw);
  svg.addEventListener("pointercancel", (event) => {
    if (!state.pending || event.pointerId !== state.pendingPointerId) return;
    state.pending = null;
    state.pendingPointerId = null;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    render();
  });
  svg.addEventListener("keydown", (event) => {
    if (state.tool !== "start") return;
    const delta = gridStep() || 0.05;
    const dx = { ArrowLeft: -delta, ArrowRight: delta }[event.key] || 0;
    const dy = { ArrowDown: -delta, ArrowUp: delta }[event.key] || 0;
    if (!dx && !dy) return;
    event.preventDefault();
    const [width, height] = state.lastValidSize;
    setStart(
      snapWithin(state.start[0] + dx, width, gridStep()),
      snapWithin(state.start[1] + dy, height, gridStep())
    );
  });
  doc.querySelectorAll("[data-tool]").forEach((button) => {
    button.addEventListener("click", () => setTool(button.dataset.tool));
  });
  for (const el of [refs.width, refs.height, refs.startX, refs.startY]) {
    el.addEventListener("input", render);
  }
  refs.grid.addEventListener("change", () => { state.grid = refs.grid.checked; render(); });
  doc.getElementById("clear-obstacles").addEventListener("click", () => {
    state.obstacles = [];
    state.pending = null;
    state.pendingPointerId = null;
    render();
  });
  doc.getElementById("copy-json").addEventListener("click", async () => {
    if (!state.latest) return labelMessage("Сначала исправь геометрию.", false);
    try {
      if (!root.navigator.clipboard || !root.navigator.clipboard.writeText) {
        throw new Error("Clipboard API недоступен");
      }
      await root.navigator.clipboard.writeText(state.latest);
      labelMessage("JSON скопирован в буфер обмена.", true);
    } catch (_error) {
      refs.preview.focus();
      refs.preview.select();
      labelMessage("Выделил JSON. Скопируй его вручную.", false);
    }
  });
  doc.getElementById("download-json").addEventListener("click", () => {
    if (!state.latest) return labelMessage("Сначала исправь геометрию.", false);
    const blob = new Blob([state.latest], { type: "application/json;charset=utf-8" });
    const url = root.URL.createObjectURL(blob);
    const a = doc.createElement("a");
    a.href = url;
    a.download = "mojum-room-draft.json";
    doc.body.appendChild(a);
    a.click();
    a.remove();
    root.setTimeout(() => root.URL.revokeObjectURL(url), 1000);
    labelMessage("Подготовлен локальный JSON-файл.", true);
  });
  render();
})(typeof window !== "undefined" ? window : globalThis);
