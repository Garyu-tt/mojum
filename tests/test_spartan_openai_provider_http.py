"""Real loopback HTTP transport smoke, fake model server, no external traffic."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import tempfile
import unittest

from tools import spartan_budget as budget
from tools.spartan_openai_provider import LocalOpenAIProvider, ProviderPolicy


class LoopbackHTTPTests(unittest.TestCase):
    def test_real_http_request_without_credentials(self):
        received = []
        class Server(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                received.append((self.path, self.headers.get("X-Request-ID"),
                                 self.headers.get("Authorization"),
                                 json.loads(self.rfile.read(size))))
                body = json.dumps({"model": "local-fixture", "usage": {
                    "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                    "choices": [{"finish_reason": "stop", "message": {
                        "role": "assistant", "content": "draft-only"}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                with closing(budget.connect(Path(directory) / "budget.sqlite")) as db:
                    budget.schema(db)
                    budget.configure(db, 50000, 0)
                    policy = ProviderPolicy("llama_cpp", "local-fixture", server.server_port,
                                            enabled=True, max_output_tokens=16)
                    result = LocalOpenAIProvider(policy).complete(
                        db=db, worker=1, request_id="http-smoke-1", prompt="hello")
                    self.assertEqual(result["text"], "draft-only")
                    self.assertEqual(budget.status(db)["reported_tokens"], 5)
                    self.assertEqual(len(received), 1)
                    self.assertEqual(received[0][:3],
                                     ("/v1/chat/completions", "http-smoke-1", None))
                    self.assertEqual(received[0][3]["messages"],
                                     [{"role": "user", "content": "hello"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()
