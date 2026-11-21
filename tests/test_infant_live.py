"""R0.8 opt-in, paced, private local live observer regression."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class LocalLiveObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "dante-infant.sqlite3"

    def command(self, *extra):
        return [
            sys.executable, "-m", "mojum.infant_play",
            "--db", str(self.db), *extra,
        ]

    def test_first_committed_frame_is_flushed_before_slow_run_finishes(self):
        # This checks real streamed output rather than only a final JSON dump.
        cmd = self.command("--ticks", "2", "--watch", "--pace-ms", "500")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
        try:
            first = json.loads(p.stdout.readline())
            self.assertEqual(first["tick"], 0)
            self.assertEqual(first["schema"], "mojum.observation.trace/1")
            self.assertIsNone(p.poll(), "frames must flush while the run is still active")
            remaining, err = p.communicate(timeout=15)
            self.assertEqual(p.returncode, 0, err)
            rest = [json.loads(row) for row in remaining.splitlines() if row.strip()]
            self.assertEqual(rest[0]["tick"], 1)
            self.assertEqual(rest[1]["checkpoint"]["tick"], 2)
            self.assertEqual(rest[1]["events"], 3)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()

    def test_slow_pacing_does_not_change_virtual_state_or_memory(self):
        other = Path(self.temp.name) / "no-pacing.sqlite3"
        args = ["--ticks", "3", "--watch"]
        a = self.command(*args, "--pace-ms", "1")
        b = [
            sys.executable, "-m", "mojum.infant_play",
            "--db", str(other), *args,
        ]
        out_a = subprocess.check_output(a, text=True)
        out_b = subprocess.check_output(b, text=True)
        self.assertEqual(
            [json.loads(line) for line in out_a.splitlines()],
            [json.loads(line) for line in out_b.splitlines()],
        )

    def test_pacing_out_of_range_and_hidden_trace_are_rejected_without_db(self):
        for bad in ("-1", "10001", "not-an-int"):
            result = subprocess.run(
                self.command("--pace-ms", bad, "--watch"),
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.db.exists())
        result = subprocess.run(
            self.command("--ticks", "1", "--pace-ms", "10"),
            text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --watch", result.stderr)
        self.assertFalse(self.db.exists())

    def test_browser_follow_is_local_opt_in_with_fallback_and_bounded_memory(self):
        page = (ROOT / "examples/infant_observer.html").read_text(encoding="utf-8")
        for required in (
            "showOpenFilePicker", "liveHandle", "getFile()",
            "file.slice(liveOffset)", "TextDecoder", "MAX_TRACE_BYTES",
            "MAX_TRACE_ROWS", "setInterval(pollLive,600)",
            "resumeLatest", "stopFollow()", "liveTail",
            "file.size<liveOffset", "followLatest=false",
            "validFrame(row)", "Выбрать JSONL",
        ):
            with self.subTest(required=required):
                self.assertIn(required, page)
        # No external dependencies or accidentally public trace endpoint.
        for forbidden in (
            "fetch(", "XMLHttpRequest", "WebSocket(", "EventSource(",
            "https://", "<script src=", "localStorage", "indexedDB",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)

    def test_console_jsonl_can_be_restarted_and_reopened_by_same_organism(self):
        first = subprocess.check_output(
            self.command("--ticks", "3", "--watch", "--pace-ms", "0"),
            text=True,
        )
        second = subprocess.check_output(
            self.command("--ticks", "2", "--watch", "--pace-ms", "0"),
            text=True,
        )
        frames = [
            json.loads(line)
            for content in (first, second)
            for line in content.splitlines()
            if line.strip() and "checkpoint" not in json.loads(line)
        ]
        self.assertEqual([f["tick"] for f in frames], [0, 1, 2, 3, 4])
        self.assertEqual(frames[-1]["memory"]["total_updates"], 5)
        self.assertTrue(all(f["source"] == "DETERMINISTIC_SYNTHETIC_TRACE_NOT_CONSCIOUS_EXPERIENCE" for f in frames))


if __name__ == "__main__":
    unittest.main()
