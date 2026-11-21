"""Actual headless Chrome smoke: real R0.13 SQLite -> PRIVATE JSONL -> HTML.

This uses the unmodified viewer HTML, a temporary local-only test script
injected *after* the application inline script, and preinstalled Chromium.
No Playwright/Selenium, npm downloads, browser dev server, mock DOM, network
requests, remote secrets, production databases, or external service calls.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mojum.aquarium import Aquarium  # noqa: E402
from mojum.aquarium_trace_export import export_private_trace  # noqa: E402

SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
VIEWER = ROOT / "examples/aquarium_trace_viewer.html"


class Probe(HTMLParser):
    def __init__(self):
        super().__init__()
        self.smoke = None

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.smoke = dict(attrs).get("data-real-chrome-smoke")


def chrome_binary():
    for name in ("google-chrome-stable", "google-chrome", "chromium",
                 "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Google Chrome/Chromium not installed; cannot claim browser smoke")


def instrumented_html(original_html, source_text):
    # Only a *test-owned, synthetic* local SQLite history is placed here.
    # Escaping '<' prevents any accidental closing of the inline script.
    literal = json.dumps(source_text, ensure_ascii=True).replace("<", "\\u003c")
    boot = r"""
<script>
window.addEventListener("load", async () => {
  const el = id => document.getElementById(id);
  try {
    const file = new File([__TRACE__], "verified.synthetic.private.jsonl",
                          {type: "text/plain"});
    const picker = el("file");
    Object.defineProperty(picker, "files", {
      configurable: true, value: [file]
    });
    picker.dispatchEvent(new Event("change", {bubbles:true}));
    let loaded = false;
    for (let retry=0;retry<100;retry++) {
      if (el("tick").textContent === "0") {loaded=true;break;}
      await new Promise(resolve=>setTimeout(resolve,10));
    }
    if (!loaded) throw Error("local JSONL import did not finish");
    const first = {
      tick: el("tick").textContent,
      action: el("action").textContent,
      probe: el("probe").textContent,
      measured: el("deflection").textContent,
      prediction: el("prediction").textContent,
      brain: el("brain").textContent
    };
    const canvas=el("room").getBoundingClientRect();
    const viewport={
      width:window.innerWidth,
      scrollWidth:document.documentElement.scrollWidth,
      canvasWidth:canvas.width,
      canvasHeight:canvas.height,
      controlsWidth:el("play").getBoundingClientRect().width
    };
    el("seek").value="1";
    el("seek").dispatchEvent(new Event("input",{bubbles:true}));
    const second={
      tick:el("tick").textContent,
      prediction:el("prediction").textContent,
      counter:el("counter").textContent,
      source:el("status").textContent
    };
    document.documentElement.dataset.realChromeSmoke = JSON.stringify({
      first,second,viewport,agent:false
    });
  } catch (error) {
    document.documentElement.dataset.realChromeSmoke =
      JSON.stringify({error:String(error && error.message || error)});
  }
});
</script>
""".replace("__TRACE__", literal)
    if original_html.count("</body>") != 1:
        raise AssertionError("viewer page missing unique body end")
    return original_html.replace("</body>", boot + "\n</body>")


def inspect_actual_browser(chrome, html, *, width, height):
    with tempfile.TemporaryDirectory(prefix="mojum-real-chrome-") as scratch:
        page = Path(scratch) / "viewer.actual.chromium.html"
        page.write_text(html, encoding="utf-8")
        command = [
            chrome, "--headless=new", "--no-sandbox",
            "--disable-gpu", "--disable-dev-shm-usage",
            "--no-first-run", "--disable-background-networking",
            "--disable-extensions", "--disable-sync", "--disable-component-update",
            "--window-size=" + str(width) + "," + str(height),
            "--virtual-time-budget=3500",
            "--dump-dom", page.as_uri(),
        ]
        run = subprocess.run(command, text=True, capture_output=True,
                             timeout=40, check=False)
        if run.returncode:
            raise AssertionError(
                "real Chrome exited with status " + str(run.returncode)
                + ": " + run.stderr[-500:]
            )
        found = Probe()
        found.feed(run.stdout)
        if found.smoke is None:
            raise AssertionError(
                "actual Chromium DOM does not contain completed viewer smoke output"
            )
        result = json.loads(found.smoke)
        if "error" in result:
            raise AssertionError("actual browser fixture failed: " + result["error"])
        return result


def main():
    chrome = chrome_binary()  # No silent skip/fallback to JS VM.
    with tempfile.TemporaryDirectory(prefix="mojum-private-chrome-fixture-") as tmp:
        private = Path(tmp)
        db = private / "one-organism.sqlite3"
        trace = private / "recorded.private.jsonl"
        with Aquarium(db, SEED, GRAPH, exploration_mode="goal_free",
                      sensory_mode="toy_probe") as aquarium:
            frame0 = aquarium.step()
            frame1 = aquarium.step()
        export_private_trace(
            db, trace, SEED, GRAPH, allow_private_export=True,
            exploration_mode="goal_free", sensory_mode="toy_probe",
        )
        data = trace.read_text(encoding="utf-8")
        original = VIEWER.read_text(encoding="utf-8")
        injected = instrumented_html(original, data)
        reports = []
        for width,height,mode in ((1280, 800, "desktop"), (390, 844, "mobile")):
            report = inspect_actual_browser(
                chrome, injected, width=width, height=height
            )
            first, second, viewport = (
                report["first"], report["second"], report["viewport"]
            )
            assert first["tick"] == "0", (mode,"first tick")
            assert first["action"] == frame0["developmental"]["intent"]["action"]
            assert first["probe"] == "Запрошена", (mode,"probe")
            assert first["measured"] == "0.75", (mode,"measured touch")
            assert first["prediction"] == "—", (mode,"blank at birth")
            assert first["brain"] == "UNIMPLEMENTED", (mode,"not a fake brain")
            assert second["tick"] == "1", (mode,"next tick")
            assert second["prediction"] == "0.75", (mode,"learned prediction")
            assert second["counter"] == "2 / 2", (mode,"counter")
            assert "verified.synthetic.private.jsonl" in second["source"]
            assert viewport["width"] > 0
            assert viewport["canvasWidth"] >= 100
            assert viewport["canvasHeight"] >= 60
            assert viewport["controlsWidth"] > 0
            assert viewport["scrollWidth"] <= viewport["width"] + 2, (
                mode, "horizontal overflow", viewport
            )
            assert frame1["toy_contact"]["prediction_before"]["predicted_deflection"] == .75
            reports.append({
                "viewport":mode,
                "source":"real replay-verified local SQLite + PRIVATE JSONL",
                "actual_browser":True,
                "first_tick":first["tick"],
                "second_tick":second["tick"],
                "measured_contact":first["measured"],
                "next_prediction":second["prediction"],
                "canvas_width_px":round(viewport["canvasWidth"]),
                "horizontal_overflow":False,
            })
    print(json.dumps({
        "status":"PASS_ACTUAL_HEADLESS_CHROME",
        "browser_binary":Path(chrome).name,
        "reports":reports,
        "privacy":"synthetic local only; no screenshots or private frames uploaded",
    }, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
