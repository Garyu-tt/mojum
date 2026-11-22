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
            "/api/v1/github?limit=999",
            "/api/v1/github?limit=0",
            "/api/v1/github?refresh=invalid",
            "/api/v1/github?limit=2&limit=3",
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

    def test_check_query_never_accepts_more_than_eight_items(self):
        self.assertIsNone(gateway.route("/api/v1/github/checks?prs="+",".join(str(x) for x in range(1,10))))
        self.assertEqual(gateway.route("/api/v1/github/checks?prs=1,2,3,4,5,6,7,8"),
                         "/api/v1/github/checks?prs=1,2,3,4,5,6,7,8")


if __name__=="__main__":
    unittest.main()
