"""Read-only bridge allowlist and anti-redirect tests; zero network calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
SOURCE = HERE / "gateway.py" if (HERE / "gateway.py").exists() else Path("/opt/cezar-telegram-gateway/readonly.py")
spec=importlib.util.spec_from_file_location("cezar_telegram_gateway",SOURCE)
gateway=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def test_owner_bot_read_paths_only(self):
        expected=(
            "/api/v1/health",
            "/api/v1/runs",
            "/api/v1/fleet",
            "/api/v1/github?limit=50&refresh=1",
            "/api/v1/github/checks?prs=612,684",
        )
        for uri in expected:
            with self.subTest(uri=uri):
                self.assertEqual(gateway.route(uri),uri)

    def test_denies_control_and_sensitive_paths(self):
        forbidden=(
            "/api/v1/runs/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/cancel",
            "/api/v1/automations",
            "/api/v1/config",
            "/api/v1/github/asset",
            "/api/v1/github/search?kind=pr&q=abc",
            "/api/v1/runs?all=true",
            "/api/v1/fleet?refresh=1",
            "/api/v1/github?limit=999",
            "/api/v1/github?limit=0",
            "/api/v1/github?refresh=invalid",
            "/api/v1/github?limit=2&limit=3",
            "/api/v1/github?limit",
            "/api/v1/github?limit=1&",
            "/api/v1/github/checks?prs",
            "/api/v1/runs?bad",
            "/api/v1/github/checks?prs=0",
            "/api/v1/github/checks?prs=612,",
            "/api/v1/github/checks?prs=612,99999999",
            "/api/v1/github/checks?prs=612&issues=1",
            "https://evil.example/api/v1/health",
            "//evil.example/api/v1/health",
            "/api/v1/health#fragment",
        )
        for uri in forbidden:
            with self.subTest(uri=uri):
                self.assertIsNone(gateway.route(uri))

    def test_redirect_never_followed(self):
        instance=gateway.NoRedirect()
        with self.assertRaises(ValueError):
            instance.redirect_request(None,None,302,"redirect",{},
                                      "https://evil.example/collect")

    def test_private_fleet_snapshot_is_pinned_and_bounded(self):
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/"current.json"
            wire=b'{"schema":"cezar.fleet.visibility.v1"}'
            source.write_bytes(wire)
            source.chmod(0o600)
            with patch.object(gateway,"FLEET_FILE",source):
                # The former Path.read_bytes() was an unbounded second open.
                with patch.object(Path,"read_bytes",side_effect=AssertionError("unsafe re-open")):
                    self.assertEqual(gateway.read_private_fleet(),wire)
                source.chmod(0o644)
                with self.assertRaises(ValueError):
                    gateway.read_private_fleet()
                source.chmod(0o600)
                source.write_bytes(b"x"*(gateway.MAX_BYTES+1))
                with self.assertRaises(ValueError):
                    gateway.read_private_fleet()

    def test_private_fleet_symlink_is_refused(self):
        import os
        import tempfile
        from unittest.mock import patch

        if not hasattr(os,"O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW required on deployment target")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            original=root/"real.json"
            original.write_bytes(b'{"schema":"cezar.fleet.visibility.v1"}')
            original.chmod(0o600)
            alias=root/"alias.json"
            alias.symlink_to(original)
            with patch.object(gateway,"FLEET_FILE",alias):
                with self.assertRaises(OSError):
                    gateway.read_private_fleet()

    def test_cezar_runs_redact_all_unapproved_fields(self):
        import json

        private = {
            "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "status": "running",
            "prNumber": 688,
            "prompt": "DO_NOT_DISCLOSE_PRIVATE_PROMPT",
            "credentials": {"token": "DO_NOT_DISCLOSE_API_KEY"},
            "worker": {"private_path": "/root/secret/session"},
        }
        redacted = gateway.project_runs(json.dumps([private]).encode("utf-8"))
        self.assertEqual(json.loads(redacted), [{
            "id": private["id"],
            "status": private["status"],
            "prNumber": 688,
        }])
        for secret in ("DO_NOT_DISCLOSE_PRIVATE_PROMPT", "DO_NOT_DISCLOSE_API_KEY",
                       "/root/secret/session", "prompt", "credentials", "worker"):
            self.assertNotIn(secret.encode(), redacted)

    def test_cezar_runs_reject_untrusted_shapes(self):
        import json

        base = {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "status": "running", "prNumber": 688}
        invalid = [
            {}, {"runs": [base]}, [7], [{"id": 5, "status": "running"}],
            [{**base, "status": "running\\nSECRET"}],
            [{**base, "prNumber": True}],
            [{**base, "prNumber": -1}],
            [base] * 1001,
        ]
        for payload in invalid:
            with self.subTest(kind=type(payload).__name__, size=len(payload)):
                with self.assertRaises(ValueError):
                    gateway.project_runs(json.dumps(payload).encode("utf-8"))


    def test_check_query_never_accepts_more_than_eight_items(self):
        self.assertIsNone(gateway.route("/api/v1/github/checks?prs="+",".join(str(x) for x in range(1,10))))
        self.assertEqual(gateway.route("/api/v1/github/checks?prs=1,2,3,4,5,6,7,8"),
                         "/api/v1/github/checks?prs=1,2,3,4,5,6,7,8")


if __name__=="__main__":
    unittest.main()
