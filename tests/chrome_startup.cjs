"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { performance } = require("node:perf_hooks");

// Chromium writes this file in the fresh profile used by the browser smoke.
// A complete first line avoids accepting a port while it is still being written.
function readPort(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  const match = /^([0-9]+)\r?\n/.exec(text);
  if (!match) return null;
  const port = Number(match[1]);
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : null;
}

async function waitForDevToolsPort(profile, child, {
  timeoutMs = 45000,
  pollIntervalMs = 100,
} = {}) {
  for (const [name, value] of Object.entries({ timeoutMs, pollIntervalMs })) {
    if (!Number.isFinite(value) || value <= 0 || value > 2147483647) {
      throw new RangeError(`${name} must be a positive finite timer duration`);
    }
  }
  const started = performance.now();
  const deadline = started + timeoutMs;
  const file = path.join(profile, "DevToolsActivePort");
  return new Promise((resolve, reject) => {
    let timer;
    let settled = false;
    const finish = (error, port) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.removeListener("error", onError);
      child.removeListener("exit", onExit);
      if (error) reject(error);
      else resolve(port);
    };
    const onError = error => finish(new Error(
      "Chrome could not start: " + error.message, { cause: error },
    ));
    const onExit = (code, signal) => finish(new Error(
      "Chrome exited before DevTools startup (" +
      (signal ? "signal " + signal : "code " + code) + ")",
    ));
    const poll = () => {
      if (child.exitCode !== null || child.signalCode !== null) {
        onExit(child.exitCode, child.signalCode);
        return;
      }
      let port;
      try {
        port = readPort(file);
      } catch (error) {
        finish(error);
        return;
      }
      // Observe readiness before the deadline check, including after the final
      // wait. A slow runner must not discard a browser that has just started.
      if (port !== null) {
        finish(null, port);
        return;
      }
      const remaining = deadline - performance.now();
      if (remaining <= 0) {
        finish(new Error(`Chrome startup timeout after ${timeoutMs} ms`));
        return;
      }
      timer = setTimeout(poll, Math.min(pollIntervalMs, remaining));
    };
    child.once("error", onError);
    child.once("exit", onExit);
    poll();
  });
}

module.exports = { waitForDevToolsPort };
