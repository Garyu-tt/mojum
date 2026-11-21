"use strict";
/* Real offline Chromium UI smoke via native Node 22 CDP WebSocket.
 * No npm, Playwright download, browser extension or Internet services.
 */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");
const assert = require("node:assert/strict");

const binary = process.env.CHROME_BINARY ||
  ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
    .find(f => fs.existsSync(f));
if (!binary) throw new Error("Chrome/Chromium required; browser acceptance cannot be faked");
const HTML = path.resolve(__dirname, "../tools/room_designer.html");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function waitPort(dir, child) {
  for (let i = 0; i < 150; i++) {
    if (child.exitCode !== null) throw new Error("Chrome exited before DevTools startup");
    const file = path.join(dir, "DevToolsActivePort");
    if (fs.existsSync(file)) {
      const line = fs.readFileSync(file, "utf8").split("\n")[0];
      if (/^[0-9]+$/.test(line)) return Number(line);
    }
    await sleep(100);
  }
  throw new Error("Chrome startup timeout");
}

async function connect(url) {
  if (typeof WebSocket !== "function") throw new Error("Node 22 WebSocket required");
  const ws = new WebSocket(url);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  let seq = 0;
  const pending = new Map();
  ws.addEventListener("message", event => {
    const reply = JSON.parse(event.data);
    if (!reply.id) return;
    const listener = pending.get(reply.id);
    if (!listener) return;
    pending.delete(reply.id);
    reply.error ? listener.reject(new Error(reply.error.message)) : listener.resolve(reply.result);
  });
  return {
    ws,
    call(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++seq;
        pending.set(id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

async function evaluate(cdp, expression) {
  const data = await cdp.call("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (data.exceptionDetails) throw new Error(
    "Chrome JS exception: " + JSON.stringify(data.exceptionDetails)
  );
  return data.result?.value;
}

async function until(cdp, expression) {
  for(let i = 0; i < 140; i++){
    if(await evaluate(cdp, expression)) return;
    await sleep(75);
  }
  throw new Error("Offline visual editor did not initialize");
}

async function main() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mojum-visual-chrome-"));
  const chrome = spawn(binary, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--remote-debugging-port=0", "--remote-allow-origins=*",
    "--user-data-dir=" + profile, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let diagnostic = "";
  chrome.stderr.on("data",data => { diagnostic=(diagnostic+data).slice(-1200); });
  let cdp;
  try {
    const port = await waitPort(profile, chrome);
    const list = await (await fetch("http://127.0.0.1:" + port + "/json")).json();
    const page = list.find(item => item.type === "page");
    assert.ok(page,"Chrome must supply a page target");
    cdp = await connect(page.webSocketDebuggerUrl);
    await cdp.call("Page.enable");
    await cdp.call("Runtime.enable");
    await cdp.call("Page.navigate", { url: pathToFileURL(HTML).href });
    await until(cdp,
      "Boolean(window.MojumRoomDesignerCore && document.querySelector('#output')?.value?.includes('point-room-draft/1'))");

    const first = JSON.parse(await evaluate(cdp,
      "JSON.stringify({text:document.querySelector('#output').value, " +
      "valid:!document.querySelector('#download').disabled, " +
      "count:document.querySelector('#obstacles').children.length})"));
    assert.equal(first.valid,true);
    assert.equal(first.count,1);
    assert.equal(JSON.parse(first.text).schema,"mojum.world.point-room-draft/1");

    // Real click handlers, SVG obstacle creation.
    await evaluate(cdp,"document.querySelector('#add-obstacle').click()");
    const added = JSON.parse(await evaluate(cdp,
      "JSON.stringify({rects:document.querySelector('#obstacles').children.length, " +
      "json:JSON.parse(document.querySelector('#output').value).obstacles.length})"));
    assert.deepEqual(added,{rects:2,json:2});

    // Text inputs must preserve the original numeric token 4.0.
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#height');e.value='4.0';" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    assert.match(await evaluate(cdp,"document.querySelector('#output').value"),
                 /"height": 4\.0,/);
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#width');e.value='invalid';" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),true);

    await evaluate(cdp,"document.querySelector('#reset-room').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),false);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),1);

    // Keyboard arrow moves the accessible SVG start marker by 5cm.
    await evaluate(cdp,
      "(function(){const p=document.querySelector('#start > g');" +
      "p.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'," +
      "bubbles:true,cancelable:true}));return true})()");
    assert.equal(await evaluate(cdp,
      "JSON.parse(document.querySelector('#output').value).start[0]"),1.3);

    // Browser Input.dispatchMouseEvent, not synthetic JS click, exercises
    // a real pointerdown/pointercapture/pointermove from headless Chromium.
    const pos = JSON.parse(await evaluate(cdp,
      "JSON.stringify((function(){const r=document.querySelector('#start circle').getBoundingClientRect();" +
      "return {x:r.x+r.width/2,y:r.y+r.height/2," +
      "start:JSON.parse(document.querySelector('#output').value).start[0]}})())"));
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mouseMoved", x: pos.x, y: pos.y,
    });
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1,
    });
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mouseMoved", x: pos.x + 16, y: pos.y, buttons: 1,
    });
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mouseReleased", x: pos.x + 16, y: pos.y, button: "left", clickCount: 1,
    });
    const moved=await evaluate(cdp,
      "JSON.parse(document.querySelector('#output').value).start[0]");
    assert.ok(moved>pos.start,"Real SVG pointer drag must change start JSON");

    console.log("PASS: offline file:// Chrome editor loaded; add, decimal input, invalid " +
      "export block, reset, keyboard arrow and actual SVG pointer drag work");
  } catch (error) {
    if (diagnostic) console.error("Chrome stderr: "+diagnostic);
    throw error;
  } finally {
    if (cdp) cdp.ws.close();
    chrome.kill("SIGTERM");
    await sleep(250);
    fs.rmSync(profile,{recursive:true,force:true,maxRetries:4,retryDelay:100});
  }
}
main().catch(error => {console.error(error.stack||String(error));process.exitCode=1;});
