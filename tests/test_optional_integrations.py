import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.optional_integrations import load_catalog


class OptionalIntegrationCatalogTests(unittest.TestCase):
    def test_catalog_is_complete_and_never_installed_by_default(self):
        data = load_catalog()
        self.assertFalse(data["default_enabled"])
        self.assertEqual(data["cpu_baseline"], "stdlib-unchanged")
        self.assertEqual(set(range(689, 703)), {
            item["issue"] for item in data["entries"]
        })
        self.assertTrue(all(x["optional"] and x["status"] == "planned"
                            and x["license_review"] == "required"
                            for x in data["entries"]))

    def check_rejected(self, blob):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "catalog.json"
            p.write_bytes(blob)
            with self.assertRaises(ValueError):
                load_catalog(p)

    def test_cannot_enable_default_dependencies(self):
        data = load_catalog()
        data["default_enabled"] = True
        self.check_rejected(json.dumps(data).encode())

    def test_cannot_disable_optional_gate(self):
        data = load_catalog()
        data["entries"][0]["optional"] = False
        self.check_rejected(json.dumps(data).encode())

    def test_status_advancement_requires_approved_license(self):
        for status in ("contract", "experiment", "accepted"):
            with self.subTest(status=status):
                data = load_catalog()
                data["entries"][0]["status"] = status
                self.check_rejected(json.dumps(data).encode())

        # An independently reviewed candidate may advance in a later PR.
        data = load_catalog()
        data["entries"][0]["status"] = "contract"
        data["entries"][0]["license_review"] = "approved"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_catalog(path)["entries"][0]["status"], "contract")

    def test_cannot_use_unsafe_reference(self):
        data = load_catalog()
        data["entries"][0]["packages"][0]["url"] = "http://internal.local/secret"
        self.check_rejected(json.dumps(data).encode())

    def test_rejects_duplicate_issue_and_package(self):
        data = load_catalog()
        data["entries"][1]["issue"] = data["entries"][0]["issue"]
        self.check_rejected(json.dumps(data).encode())
        data = load_catalog()
        data["entries"][0]["packages"][1]["name"] = data["entries"][0]["packages"][0]["name"]
        self.check_rejected(json.dumps(data).encode())

    def test_issue_map_rejects_unknown_or_missing_issue(self):
        data = load_catalog()
        data["entries"][0]["issue"] = 123456
        self.check_rejected(json.dumps(data).encode())
        data = load_catalog()
        data["entries"].pop()
        self.check_rejected(json.dumps(data).encode())

    def test_accepted_status_requires_approved_license(self):
        data = load_catalog()
        data["entries"][0]["status"] = "accepted"
        data["entries"][0]["license_review"] = "required"
        self.check_rejected(json.dumps(data).encode())

    def test_duplicate_json_keys_fail_closed(self):
        self.check_rejected(b'{"schema":1,"schema":2}')

    def test_oversized_registry_rejected(self):
        self.check_rejected(b"x" * (256 * 1024 + 1))

    def test_oversized_input_is_never_read_unbounded(self):
        # A full Path.read_bytes() allocates attacker-controlled file size.
        # Guard the actual read() length, including the one-byte excess probe.
        class BoundedSource(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > 256 * 1024 + 1:
                    raise AssertionError("catalog read exceeded byte cap")
                return super().read(size)

        source = BoundedSource(b"x" * (256 * 1024 + 2))
        with mock.patch.object(Path, "open", return_value=source):
            with self.assertRaisesRegex(ValueError, "oversized integration registry"):
                load_catalog(Path("synthetic-untrusted-catalog.json"))
        self.assertEqual(source.tell(), 256 * 1024 + 1)


if __name__ == "__main__":
    unittest.main()
