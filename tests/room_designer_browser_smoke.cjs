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
const { waitForDevToolsPort } = require("./chrome_startup.cjs");

const binary = process.env.CHROME_BINARY ||
  ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"]
    .find(f => fs.existsSync(f));
if (!binary) throw new Error("Chrome/Chromium required; browser acceptance cannot be faked");
const HTML = process.env.MOJUM_ROOM_HTML
  ? path.resolve(process.env.MOJUM_ROOM_HTML)
  : path.resolve(__dirname, "../tools/room_designer.html");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

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
    const port = await waitForDevToolsPort(profile, chrome);
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

    // Real browser interactions: offline movement preview changes neither
    // source draft JSON nor persistent Noosphere (there is no SQLite access).
    assert.equal(await evaluate(cdp,
      "document.querySelector('#walk-right').disabled"),true);
    await evaluate(cdp,"document.querySelector('#walk-toggle').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#walk-right').disabled"),false);
    for(let i=0;i<3;i++){
      await evaluate(cdp,"document.querySelector('#walk-right').click()");
    }
    const movement=JSON.parse(await evaluate(cdp,
      "JSON.stringify({output:document.querySelector('#output').value,"+
      "status:document.querySelector('#walk-status').textContent,"+
      "dot:document.querySelector('#walk-trail circle').getAttribute('cx'),"+
      "line:document.querySelector('#walk-trail polyline').getAttribute('points')})"));
    assert.equal(movement.output,first.text,
      "movement preview must never mutate authoring JSON");
    assert.match(movement.status,/Шаги: 3\/512/);
    assert.match(movement.status,/блокировок: 1/);
    assert.equal(Number(movement.dot),1.75);
    assert.ok(movement.line.length>5,"real SVG path is rendered");
    await evaluate(cdp,"document.querySelector('#walk-reset').click()");
    assert.match(await evaluate(cdp,
      "document.querySelector('#walk-status').textContent"),/Шаги: 0\/512/);
    // Valid geometry editing and Undo both invalidate and restart the walk,
    // while preserving the original numeric authoring JSON.
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#width');e.value='5.0';"+
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    assert.match(await evaluate(cdp,
      "document.querySelector('#walk-status').textContent"),/Шаги: 0\/512/);
    await evaluate(cdp,"document.querySelector('#undo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#output').value"),first.text);
    // An invalid draft disables walk arrows but cannot trap the operator
    // in an active preview: Stop remains usable, Undo restores the room.
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#width');e.value='invalid';"+
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#walk-right').disabled"),true);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#walk-toggle').disabled"),false);
    await evaluate(cdp,"document.querySelector('#walk-toggle').click()");
    await evaluate(cdp,"document.querySelector('#undo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#output').value"),first.text);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#walk-trail').children.length"),0);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#walk-right').disabled"),true);

    // Real click handlers, SVG obstacle creation.
    await evaluate(cdp,"document.querySelector('#add-obstacle').click()");
    const added = JSON.parse(await evaluate(cdp,
      "JSON.stringify({rects:document.querySelector('#obstacles').children.length, " +
      "json:JSON.parse(document.querySelector('#output').value).obstacles.length})"));
    assert.deepEqual(added,{rects:2,json:2});

    // Exercise real on-page undo/redo, including the SVG rectangles.
    await evaluate(cdp,"document.querySelector('#undo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),1);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#redo').disabled"),false);
    await evaluate(cdp,"document.querySelector('#redo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),2);

    // Real Chromium Ctrl+Z / Ctrl+Y keyboard delivery, outside text inputs.
    const pressCtrl = async (code, key, virtualKey) => {
      await cdp.call("Input.dispatchKeyEvent", {
        type:"keyDown",code,key,windowsVirtualKeyCode:virtualKey,
        nativeVirtualKeyCode:virtualKey,modifiers:2,
      });
      await cdp.call("Input.dispatchKeyEvent", {
        type:"keyUp",code,key,windowsVirtualKeyCode:virtualKey,
        nativeVirtualKeyCode:virtualKey,modifiers:2,
      });
    };
    await pressCtrl("KeyZ","z",90);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),1);
    await pressCtrl("KeyY","y",89);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),2);

    // Text inputs must preserve the original numeric token 4.0.
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#height');e.value='4.0';" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    const canonicalExport = await evaluate(cdp,
      "document.querySelector('#output').value");
    assert.match(canonicalExport, /"height": 4\.0,/);
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#width');e.value='invalid';" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),true);
    // Invalid intermediate input was never written to the history;
    // one Undo restores the last valid room, not an older shape.
    await evaluate(cdp,"document.querySelector('#undo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),false);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#output').value"),canonicalExport);

    await evaluate(cdp,"document.querySelector('#reset-room').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),false);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),1);

    // Actual file input/change event: reload locally exported bytes in the
    // editor, including the original 4.0 float token, without Noosphere.
    await evaluate(cdp,
      "(function(){const dt=new DataTransfer();" +
      "dt.items.add(new File([" + JSON.stringify(canonicalExport) +
      "],'room.json',{type:'application/json'}));" +
      "const input=document.querySelector('#import-file');" +
      "input.files=dt.files;input.dispatchEvent(new Event('change',{bubbles:true}));" +
      "return true})()");
    await until(cdp,
      "document.querySelector('#output').value.includes('height\": 4.0')");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#output').value"),canonicalExport);
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),2);
    // Local-file import is one recoverable edit; preserve 4.0 and both rects.
    await evaluate(cdp,"document.querySelector('#undo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#obstacles').children.length"),1);
    assert.match(await evaluate(cdp,
      "document.querySelector('#output').value"), /"height": 4,/);
    await evaluate(cdp,"document.querySelector('#redo').click()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#output').value"),canonicalExport);
    await evaluate(cdp,"document.querySelector('#reset-room').click()");

    // A delayed import must not overwrite a subsequent user's room edit.
    // Exercise the real DOM change listener with a deliberately held file.text().
    await evaluate(cdp,
      "(function(){const original=File.prototype.text;window.__roomOriginalText=original;" +
      "File.prototype.text=function(){if(this.name==='delayed-import.json')" +
      "return new Promise(resolve=>{window.__resolveRoomImport=resolve;});" +
      "return original.call(this)};" +
      "const input=document.querySelector('#import-file'),dt=new DataTransfer();" +
      "dt.items.add(new File(['placeholder'],'delayed-import.json'," +
      "{type:'application/json'}));input.files=dt.files;" +
      "input.dispatchEvent(new Event('change',{bubbles:true}));return true})()");
    await until(cdp, "typeof window.__resolveRoomImport==='function'");
    await evaluate(cdp,
      "(function(){const el=document.querySelector('#width');el.value='5.0';" +
      "el.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    const newerDraft=await evaluate(cdp,"document.querySelector('#output').value");
    assert.notEqual(newerDraft,canonicalExport);
    await evaluate(cdp,"window.__resolveRoomImport(" +
      JSON.stringify(canonicalExport) + ");true");
    await evaluate(cdp,"new Promise(resolve=>setTimeout(resolve,0))");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#output').value"),newerDraft,
      "Stale asynchronous JSON import clobbered a newer user edit");
    assert.doesNotMatch(await evaluate(cdp,
      "document.querySelector('#status').textContent"),/загружена локально/);
    await evaluate(cdp,
      "File.prototype.text=window.__roomOriginalText;" +
      "delete window.__roomOriginalText;delete window.__resolveRoomImport;true");
    await evaluate(cdp,"document.querySelector('#reset-room').click()");

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
    // An entire pointer drag should be ONE undo entry.
    await evaluate(cdp,"document.querySelector('#undo').click()");
    assert.equal(await evaluate(cdp,
      "JSON.parse(document.querySelector('#output').value).start[0]"),pos.start);
    await evaluate(cdp,"document.querySelector('#redo').click()");
    assert.equal(await evaluate(cdp,
      "JSON.parse(document.querySelector('#output').value).start[0]"),moved);

    // Recover from a malformed typed start using the REAL SVG pointer path,
    // without an Undo or direct model mutation in the browser.
    await evaluate(cdp,
      "(function(){const e=document.querySelector('#start-x');e.value='';" +
      "e.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),true);
    const grip = JSON.parse(await evaluate(cdp,
      "JSON.stringify((function(){const r=document.querySelector('#start circle').getBoundingClientRect();" +
      "return {x:r.x+r.width/2,y:r.y+r.height/2}})())"));
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mouseMoved", x: grip.x, y: grip.y,
    });
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mousePressed", x: grip.x, y: grip.y, button: "left", clickCount: 1,
    });
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mouseMoved", x: grip.x + 16, y: grip.y, buttons: 1,
    });
    await cdp.call("Input.dispatchMouseEvent", {
      type: "mouseReleased", x: grip.x + 16, y: grip.y, button: "left", clickCount: 1,
    });
    assert.equal(await evaluate(cdp,
      "document.querySelector('#download').disabled"),false,
      "SVG drag must repair a malformed previous start");
    assert.ok(await evaluate(cdp,
      "Number.isFinite(JSON.parse(document.querySelector('#output').value).start[0])"));

    // Capture a non-private visual acceptance artifact at a phone-like
    // viewport. Nothing in this synthetic demo originates from a user DB.
    await cdp.call("Emulation.setDeviceMetricsOverride", {
      width: 393, height: 852, deviceScaleFactor: 2, mobile: true,
    });
    await sleep(180);
    const capture = await cdp.call("Page.captureScreenshot",{
      format: "png", captureBeyondViewport: true,
    });
    const folder = path.resolve(__dirname, "../ui-qa-artifacts");
    fs.mkdirSync(folder, {recursive:true});
    const screenshot = path.join(folder, "room-designer-mobile.png");
    fs.writeFileSync(screenshot, Buffer.from(capture.data,"base64"));
    assert.ok(fs.statSync(screenshot).size>10000,
      "Actual browser screenshot must be nonempty");
    const responsive = await evaluate(cdp,
      "getComputedStyle(document.querySelector('.columns')).gridTemplateColumns");
    assert.equal(responsive.trim().split(" ").length,1,
      "Phone-width editor must stack its main columns");

    console.log("PASS: offline file:// Chrome editor loaded; add, undo/redo, decimal input, invalid " +
      "export block, reset, keyboard arrow, SVG pointer drag and its undo/redo work");
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
