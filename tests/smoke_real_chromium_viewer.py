"""Actual headless Chrome smoke: real R0.13 SQLite -> PRIVATE JSONL -> HTML.

This uses the unmodified viewer HTML, a temporary local-only test script
injected *after* the application inline script, and preinstalled Chromium.
No Playwright/Selenium, npm downloads, browser dev server, mock DOM, network
requests, remote secrets, production databases, or external service calls.
"""

from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

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


def instrumented_html(original_html, source_text, *, expected_rejection=None):
    # Only a *test-owned, synthetic* local SQLite history is placed here.
    # Escaping '<' prevents any accidental closing of the inline script.
    literal = json.dumps(source_text, ensure_ascii=True).replace("<", "\\u003c")
    rejection = json.dumps(expected_rejection, ensure_ascii=True).replace("<", "\\u003c")
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
    const rejection = __REJECTION__;
    if (rejection !== null) {
      for (let retry=0;retry<100;retry++) {
        if (el("status").textContent.includes(rejection)) {
          document.documentElement.dataset.realChromeSmoke = JSON.stringify({
            rejected: true,
            reason: el("status").textContent,
            controls_disabled: ["prev","play","next"].every(id => el(id).disabled),
            displayed_policy: el("policy").textContent
          });
          return;
        }
        await new Promise(resolve=>setTimeout(resolve,10));
      }
      throw Error("mixed-policy trace was not explicitly rejected");
    }
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
      brain: el("brain").textContent,
      policy: el("policy").textContent
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
""".replace("__TRACE__", literal).replace("__REJECTION__", rejection)
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


def inspect_actual_mobile_chromedriver(chrome, html, *, width, height):
    """Emulate *exact CSS pixels* via Chrome DevTools, not window-size hint.

    GitHub ubuntu-24.04 ships Chrome and ChromeDriver. Use only Python's
    urllib over loopback WebDriver; no Selenium/Playwright/npm dependency.
    """
    binary = shutil.which("chromedriver")
    if not binary:
        hint = os.environ.get("CHROMEWEBDRIVER", "")
        candidates = [Path(hint)] if hint else []
        if hint:
            candidates.append(Path(hint) / "chromedriver")
        binary = next((str(p) for p in candidates if p.is_file()), None)
    if not binary:
        raise RuntimeError("ChromeDriver unavailable: cannot claim 390px mobile viewport")
    with tempfile.TemporaryDirectory(prefix="mojum-actual-mobile-") as scratch:
        file = Path(scratch) / "viewer.actual.mobile.html"
        file.write_text(html, encoding="utf-8")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        driver = subprocess.Popen(
            [binary, "--port=" + str(port),
             "--allowed-ips=127.0.0.1", "--log-level=SEVERE"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        prefix = "http://127.0.0.1:" + str(port)
        session_id = None

        def web_driver(method, suffix, body=None):
            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = Request(prefix + suffix, data=data, method=method,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            value = result.get("value")
            if isinstance(value, dict) and value.get("error"):
                raise RuntimeError("ChromeDriver API error: " + str(value["error"]))
            return value

        try:
            for _ in range(70):
                if driver.poll() is not None:
                    raise RuntimeError("ChromeDriver failed to start")
                try:
                    web_driver("GET", "/status")
                    break
                except (URLError, ConnectionError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("ChromeDriver did not become ready")
            response = web_driver("POST", "/session", {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "chrome",
                        "goog:chromeOptions": {
                            "binary": chrome,
                            "args": [
                                "--headless=new", "--no-sandbox",
                                "--disable-gpu", "--disable-dev-shm-usage",
                                "--no-first-run", "--disable-background-networking",
                                "--disable-extensions", "--disable-sync",
                                "--disable-component-update",
                            ],
                        },
                    },
                },
            })
            session_id = response["sessionId"]
            route = "/session/" + session_id
            web_driver("POST", route + "/goog/cdp/execute", {
                "cmd": "Emulation.setDeviceMetricsOverride",
                "params": {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 1,
                    "mobile": True,
                    "screenWidth": width,
                    "screenHeight": height,
                },
            })
            web_driver("POST", route + "/url", {"url": file.as_uri()})
            for _ in range(70):
                raw = web_driver("POST", route + "/execute/sync", {
                    "script": (
                        "return document.documentElement"
                        ".getAttribute('data-real-chrome-smoke')"
                    ),
                    "args": [],
                })
                if raw:
                    result = json.loads(raw)
                    if "error" in result:
                        raise AssertionError(result["error"])
                    return result
                time.sleep(0.1)
            raise AssertionError("actual mobile browser never loaded JSONL telemetry")
        finally:
            if session_id:
                try:
                    web_driver("DELETE", "/session/" + session_id)
                except Exception:
                    pass
            driver.terminate()
            try:
                driver.wait(timeout=5)
            except subprocess.TimeoutExpired:
                driver.kill()
                driver.wait(timeout=5)


def main():
    chrome = chrome_binary()  # No silent skip/fallback to JS VM.
    reports = []
    source_by_policy = {}
    with tempfile.TemporaryDirectory(prefix="mojum-private-chrome-fixture-") as tmp:
        private = Path(tmp)
        original = VIEWER.read_text(encoding="utf-8")
        # One source organism for each persisted motor policy, not a JS toy.
        # Old v1 traces and v2 traces must both survive cold verified export
        # and real desktop/mobile browser playback.
        for policy in ("coarse_v1", "local_cell_v2"):
            db = private / (policy + ".sqlite3")
            trace = private / (policy + ".private.jsonl")
            with Aquarium(db, SEED, GRAPH, exploration_mode="goal_free",
                          sensory_mode="toy_probe", motor_policy=policy) as aquarium:
                frame0 = aquarium.step()
                frame1 = aquarium.step()
            export_private_trace(
                db, trace, SEED, GRAPH, allow_private_export=True,
                exploration_mode="goal_free", sensory_mode="toy_probe",
                motor_policy=policy,
            )
            data = trace.read_text(encoding="utf-8")
            source_by_policy[policy] = data
            footer = json.loads(data.splitlines()[-1])
            assert footer["policy_schema"] == (
                "mojum.developmental.explorer/1" if policy == "coarse_v1"
                else "mojum.developmental.explorer.local-cell/2"
            ), "export must bind explicit policy wire identity"
            assert "developmental_state" not in footer, (
                "private learned state must not leak in JSONL metadata"
            )
            injected = instrumented_html(original, data)
            for width, height, mode in ((1280, 800, "desktop"), (390, 844, "mobile")):
                report = (inspect_actual_mobile_chromedriver(
                    chrome, injected, width=width, height=height
                ) if mode == "mobile" else inspect_actual_browser(
                    chrome, injected, width=width, height=height
                ))
                first, second, viewport = (
                    report["first"], report["second"], report["viewport"]
                )
                assert first["tick"] == "0", (policy, mode, "first tick")
                assert first["action"] == frame0["developmental"]["intent"]["action"]
                assert first["probe"] == "Запрошена", (policy, mode, "probe")
                assert first["measured"] == "0.75", (policy, mode, "measured touch")
                assert first["prediction"] == "—", (policy, mode, "blank at birth")
                assert first["brain"] == "UNIMPLEMENTED", (policy, mode, "not a brain")
                expected_policy = (
                    "coarse_v1 · зоны 0,5 м" if policy == "coarse_v1"
                    else "local_cell_v2A · ячейки 4 см"
                )
                assert first["policy"] == expected_policy, (
                    policy, mode, "actual displayed motor policy", first["policy"]
                )
                assert second["tick"] == "1", (policy, mode, "next tick")
                assert second["prediction"] == "0.75", (policy, mode, "learned prediction")
                assert second["counter"] == "2 / 2", (policy, mode, "counter")
                assert "verified.synthetic.private.jsonl" in second["source"]
                assert "SHA-256 кадров совпадает" in second["source"], (
                    policy, mode, "WebCrypto digest was not visibly verified",
                    second["source"]
                )
                assert viewport["width"] > 0
                if mode == "mobile":
                    assert viewport["width"] == 390, (
                        "Chrome must render exact 390 CSS-px viewport", viewport
                    )
                assert viewport["canvasWidth"] >= 100
                assert viewport["canvasHeight"] >= 60
                assert viewport["controlsWidth"] > 0
                assert viewport["scrollWidth"] <= viewport["width"] + 2, (
                    policy, mode, "horizontal overflow", viewport
                )
                assert frame1["toy_contact"]["prediction_before"]["predicted_deflection"] == .75
                reports.append({
                    "motor_policy": policy,
                    "displayed_policy": first["policy"],
                    "viewport": mode,
                    "source": "real replay-verified local SQLite + PRIVATE JSONL",
                    "actual_browser": True,
                    "first_tick": first["tick"],
                    "second_tick": second["tick"],
                    "measured_contact": first["measured"],
                    "next_prediction": second["prediction"],
                    "css_viewport_width_px": viewport["width"],
                    "canvas_width_px": round(viewport["canvasWidth"]),
                    "horizontal_overflow": False,
                })
        # Adversarial file only: splice real committed first v1 frame and real
        # committed second v2 frame, then recompute exact byte SHA-256.
        # Viewer must fail closed because provenance is mixed even though
        # the replacement footer has an internally consistent frame digest.
        old_lines = source_by_policy["coarse_v1"].splitlines()
        new_lines = source_by_policy["local_cell_v2"].splitlines()
        wire = (old_lines[0] + "\n" + new_lines[1] + "\n").encode("utf-8")
        footer = json.loads(old_lines[-1])
        footer["trace_integrity"]["sha256"] = hashlib.sha256(wire).hexdigest()
        footer["trace_integrity"]["byte_length"] = len(wire)
        mixed = wire.decode("utf-8") + json.dumps(
            footer, sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")
        ) + "\n"
        result = inspect_actual_browser(
            chrome, instrumented_html(
                original, mixed, expected_rejection="Смешаны разные версии моторной политики"
            ), width=1280, height=800
        )
        assert result["rejected"] is True
        assert result["controls_disabled"] is True
        assert result["displayed_policy"] == "—"

        # Negative control 1: a footer may be rewritten without affecting
        # the SHA-256 of the exact unchanged frame bytes. Reject mismatch.
        clean = source_by_policy["local_cell_v2"].splitlines()
        bogus = json.loads(clean[-1])
        bogus["policy_schema"] = "mojum.developmental.local-cell/2"
        altered_footer_only = "\n".join(
            clean[:-1] + [json.dumps(bogus, sort_keys=True,
                                    ensure_ascii=False, separators=(",", ":"))]
        ) + "\n"
        result = inspect_actual_browser(
            chrome, instrumented_html(
                original, altered_footer_only,
                expected_rejection="Политика checkpoint не совпадает с кадрами"
            ), width=1280, height=800
        )
        assert result["rejected"] and result["controls_disabled"]
        assert result["displayed_policy"] == "—"

        # Negative control 2: an alternative peer wire schema with the
        # same public name local_cell_v2 is NOT the known v2A format.
        # Recompute SHA of *all* changed frame lines to ensure rejection
        # is based on ABI identity, not a stale file checksum.
        altered_frames = [json.loads(line) for line in clean[:-1]]
        for frame in altered_frames:
            frame["developmental"]["schema"] = "mojum.developmental.local-cell/2"
            frame["developmental"]["learning"]["schema"] = (
                "mojum.developmental.local-cell/2"
            )
        tampered_wire = "".join(
            json.dumps(frame, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n"
            for frame in altered_frames
        ).encode("utf-8")
        rewritten_footer = json.loads(clean[-1])
        rewritten_footer["policy_schema"] = "mojum.developmental.local-cell/2"
        rewritten_footer["trace_integrity"]["byte_length"] = len(tampered_wire)
        rewritten_footer["trace_integrity"]["sha256"] = (
            hashlib.sha256(tampered_wire).hexdigest()
        )
        tampered_file = tampered_wire.decode("utf-8") + json.dumps(
            rewritten_footer, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False
        ) + "\n"
        result = inspect_actual_browser(
            chrome, instrumented_html(
                original, tampered_file,
                expected_rejection="Схема обучения не совпадает с моторной политикой"
            ), width=1280, height=800
        )
        assert result["rejected"] and result["controls_disabled"]
        assert result["displayed_policy"] == "—"

        # Negative control 2b: unsupported and unversioned future schema
        # must fail even when its accompanying learning schema and SHA match.
        for frame in altered_frames:
            frame["developmental"]["schema"] = "mojum.developmental.local-cell/999"
            frame["developmental"]["learning"]["schema"] = (
                "mojum.developmental.local-cell.outcome/999"
            )
        unsupported_bytes = "".join(
            json.dumps(frame, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n"
            for frame in altered_frames
        ).encode("utf-8")
        footer = json.loads(clean[-1])
        footer["policy_schema"] = "mojum.developmental.local-cell/999"
        footer["trace_integrity"]["byte_length"] = len(unsupported_bytes)
        footer["trace_integrity"]["sha256"] = hashlib.sha256(unsupported_bytes).hexdigest()
        result = inspect_actual_browser(
            chrome, instrumented_html(
                original, unsupported_bytes.decode("utf-8") + json.dumps(
                    footer, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False
                ) + "\n",
                expected_rejection="Неподдерживаемая версия моторной политики"
            ), width=1280, height=800
        )
        assert result["rejected"] and result["controls_disabled"]
        assert result["displayed_policy"] == "—"

        # Negative control 3: missing learning.schema must not be
        # displayed as a valid v2 motor model, even with a recomputed hash.
        for frame in altered_frames:
            frame["developmental"]["schema"] = (
                "mojum.developmental.explorer.local-cell/2"
            )
            frame["developmental"]["learning"].pop("schema", None)
        no_learning = "".join(
            json.dumps(frame, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n"
            for frame in altered_frames
        ).encode("utf-8")
        footer = json.loads(clean[-1])
        footer["trace_integrity"]["byte_length"] = len(no_learning)
        footer["trace_integrity"]["sha256"] = (
            hashlib.sha256(no_learning).hexdigest()
        )
        result = inspect_actual_browser(
            chrome, instrumented_html(
                original, no_learning.decode("utf-8") + json.dumps(
                    footer, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False
                ) + "\n",
                expected_rejection="Схема обучения не совпадает с моторной политикой"
            ), width=1280, height=800
        )
        assert result["rejected"] and result["controls_disabled"]
        assert result["displayed_policy"] == "—"
    assert len(reports) == 4, "both persisted policy versions need both browsers"
    print(json.dumps({
        "status": "PASS_ACTUAL_HEADLESS_CHROME",
        "browser_binary": Path(chrome).name,
        "reports": reports,
        "privacy": "synthetic local only; no screenshots or private frames uploaded",
    }, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
