import json
import tempfile
import unittest
from pathlib import Path

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

    def test_duplicate_json_keys_fail_closed(self):
        self.check_rejected(b'{"schema":1,"schema":2}')

    def test_oversized_registry_rejected(self):
        self.check_rejected(b"x" * (256 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
