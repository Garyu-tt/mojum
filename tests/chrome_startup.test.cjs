"use strict";

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { performance } = require("node:perf_hooks");
const { setTimeout: sleep } = require("node:timers/promises");
const test = require("node:test");
const { waitForDevToolsPort } = require("./chrome_startup.cjs");

// Real child processes exercise launch errors, exit/signal events and a file
// written independently of the parent's event loop. They do not stand in for
// the actual Chrome UI acceptance, which both workflows still run separately.
async function fixture(t) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mojum-startup-test-"));
  const child = spawn(process.execPath, ["-e", `
    const fs = require("node:fs");
    const path = require("node:path");
    process.on("message", message => {
      if (message.exit !== undefined) process.exit(message.exit);
      else if (message.signal) process.kill(process.pid, message.signal);
      else setTimeout(() => {
        fs.writeFileSync(path.join(process.argv[1], "DevToolsActivePort"), message.text);
        process.send({ written: message.id });
      }, message.delayMs || 0);
    });
    process.send({ ready: true });
  `, profile], { stdio: ["ignore", "ignore", "inherit", "ipc"] });
  const closed = new Promise(resolve => child.once("close", resolve));
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    await closed;
    fs.rmSync(profile, { recursive: true, force: true });
  });
  assert.deepEqual((await once(child, "message"))[0], { ready: true });
  let seq = 0;
  const write = (text, delayMs = 0) => new Promise((resolve, reject) => {
    const id = ++seq;
    const onMessage = message => {
      if (message.written !== id) return;
      child.removeListener("message", onMessage);
      resolve();
    };
    child.on("message", onMessage);
    child.send({ id, text, delayMs }, error => {
      if (!error) return;
      child.removeListener("message", onMessage);
      reject(error);
    });
  });
  return { child, profile, write, closed };
}

function noStartupListeners(child) {
  assert.equal(child.listenerCount("error"), 0);
  assert.equal(child.listenerCount("exit"), 0);
}

test("accepts complete boundary ports and CRLF from a live process", async t => {
  const { child, profile, write } = await fixture(t);
  for (const text of ["1\n/devtools/browser/test\n", "65535\r\n/devtools/browser/test\r\n"]) {
    await write(text);
    assert.equal(await waitForDevToolsPort(profile, child), Number(text.split(/\r?\n/)[0]));
    noStartupListeners(child);
  }
});

test("does not accept the partial decimal port before its newline", async t => {
  const { child, profile, write } = await fixture(t);
  await write("12");
  let finished = false;
  const waiting = waitForDevToolsPort(profile, child, { timeoutMs: 5000, pollIntervalMs: 10 })
    .then(port => { finished = true; return port; });
  await sleep(80);
  assert.equal(finished, false, "partial port 12 must not be accepted");
  await write("12345\n/devtools/browser/test\n");
  assert.equal(await waiting, 12345);
  noStartupListeners(child);
});

test("observes readiness after the final wait reaches the deadline", async t => {
  const { child, profile, write } = await fixture(t);
  const started = performance.now();
  const waiting = waitForDevToolsPort(profile, child, { timeoutMs: 600, pollIntervalMs: 2000 });
  // The first poll already saw an absent file. The only scheduled observation
  // is at the deadline; checking elapsed time before that read would lose it.
  await write("31415\n/devtools/browser/test\n");
  assert.equal(await waiting, 31415);
  assert.ok(performance.now() - started >= 500);
  noStartupListeners(child);
});

test("default budget admits a process starting beyond the former 15 seconds", {
  timeout: 30000,
}, async t => {
  const { child, profile, write } = await fixture(t);
  const started = performance.now();
  const waiting = waitForDevToolsPort(profile, child);
  await write("31416\n/devtools/browser/test\n", 15500);
  assert.equal(await waiting, 31416);
  assert.ok(performance.now() - started >= 15000);
  noStartupListeners(child);
});

for (const text of ["0\n", "65536\n", "-1\n", " 80\n", "1e3\n", "80junk\n"]) {
  test(`rejects invalid port ${JSON.stringify(text)} with a bounded timeout`, async t => {
    const { child, profile, write } = await fixture(t);
    await write(text);
    await assert.rejects(
      waitForDevToolsPort(profile, child, { timeoutMs: 40, pollIntervalMs: 10 }),
      /Chrome startup timeout after 40 ms/,
    );
    noStartupListeners(child);
  });
}

test("an absent port file times out and preserves caller-owned listeners", async t => {
  const { child, profile } = await fixture(t);
  const onError = () => {};
  const onExit = () => {};
  child.on("error", onError);
  child.on("exit", onExit);
  await assert.rejects(
    waitForDevToolsPort(profile, child, { timeoutMs: 40, pollIntervalMs: 10 }),
    /Chrome startup timeout after 40 ms/,
  );
  assert.deepEqual(child.listeners("error"), [onError]);
  assert.deepEqual(child.listeners("exit"), [onExit]);
  child.removeListener("error", onError);
  child.removeListener("exit", onExit);
});

for (const action of [{ exit: 0 }, { exit: 23 }, { signal: "SIGTERM" }]) {
  test(`process ${JSON.stringify(action)} rejects before a long poll`, { timeout: 5000 }, async t => {
    const { child, profile } = await fixture(t);
    const waiting = waitForDevToolsPort(profile, child, { timeoutMs: 30000, pollIntervalMs: 10000 });
    const rejected = assert.rejects(waiting, new RegExp(
      "Chrome exited before DevTools startup.*" +
      (action.signal ? "signal SIGTERM" : "code " + action.exit),
    ));
    child.send(action);
    await rejected;
    noStartupListeners(child);
  });
}

for (const action of [{ exit: 0 }, { signal: "SIGTERM" }]) {
  test(`an already stopped process ${JSON.stringify(action)} cannot admit a stale port`, async t => {
    const { child, profile, write, closed } = await fixture(t);
    await write("31417\n/devtools/browser/stale\n");
    child.send(action);
    await closed;
    await assert.rejects(waitForDevToolsPort(profile, child), /Chrome exited before DevTools startup/);
    noStartupListeners(child);
  });
}

test("a real missing executable rejects with its launch error", { timeout: 5000 }, async t => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mojum-startup-missing-"));
  t.after(() => fs.rmSync(profile, { recursive: true, force: true }));
  const child = spawn(path.join(profile, "missing-browser"), [], { stdio: "ignore" });
  const closed = new Promise(resolve => child.once("close", resolve));
  await assert.rejects(waitForDevToolsPort(profile, child), error => {
    assert.match(error.message, /Chrome could not start/);
    assert.equal(error.cause.code, "ENOENT");
    return true;
  });
  await closed;
  noStartupListeners(child);
});

test("a port-file read failure is not hidden as a startup timeout", async t => {
  const { child, profile } = await fixture(t);
  fs.mkdirSync(path.join(profile, "DevToolsActivePort"));
  await assert.rejects(waitForDevToolsPort(profile, child), { code: "EISDIR" });
  noStartupListeners(child);
});

test("rejects unbounded or overflowing timer options before installing listeners", async t => {
  const { child, profile } = await fixture(t);
  for (const name of ["timeoutMs", "pollIntervalMs"]) {
    for (const value of [0, -1, NaN, Infinity, 2147483648]) {
      await assert.rejects(waitForDevToolsPort(profile, child, { [name]: value }), RangeError);
      noStartupListeners(child);
    }
  }
});
