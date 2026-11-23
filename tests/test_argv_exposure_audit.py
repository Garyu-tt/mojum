import unittest
from mojum.argv_exposure_audit import audit_argv


class ArgvExposureTests(unittest.TestCase):
    def test_safe(self):
        result = audit_argv(["mojum", "--threads=4"])
        self.assertTrue(result.safe)
        self.assertEqual(result.findings, ())

    def test_inline_secret_is_redacted(self):
        result = audit_argv(["app", "--api-key=very-secret"])
        self.assertFalse(result.safe)
        self.assertEqual(result.findings[0].kind, "sensitive-option")
        self.assertNotIn("very-secret", repr(result))

    def test_split_secret(self):
        result = audit_argv(["app", "--password", "super-secret"])
        self.assertEqual([f.index for f in result.findings], [1, 2])
        self.assertNotIn("super-secret", repr(result))

    def test_missing_secret_value(self):
        kinds = [finding.kind for finding in audit_argv(["--token"]).findings]
        self.assertIn("missing-sensitive-option-value", kinds)

    def test_url_credentials(self):
        self.assertEqual(audit_argv(["https://alice:secret@example.test/"]).findings[0].kind,
                         "url-userinfo")

    def test_url_credentials_inside_option_values(self):
        for argument in (
            "--endpoint=https://alice:secret@example.test/",
            "-u=https://alice:secret@example.test/",
            "https://alice:secret@example.test/",
        ):
            with self.subTest(argument=argument.split("=")[0]):
                result = audit_argv([argument])
                self.assertIn("url-userinfo", [finding.kind for finding in result.findings])
                self.assertFalse(result.safe)

    def test_rejects_unpaired_surrogates(self):
        with self.assertRaises(ValueError):
            audit_argv(["bad-\ud800"])

    def test_digest_does_not_depend_on_secret(self):
        a = audit_argv(["--token=a"])
        b = audit_argv(["--token=b"])
        self.assertEqual(a.digest, b.digest)

    def test_rejects_hostile_inputs(self):
        for value in ((), ["ok", 1], ["ok", True], ["x" * 4097],
                      ["a\x00b"], [""], ["x"] * 257):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError):
                    audit_argv(value)

    def test_requires_provenance(self):
        with self.assertRaises(ValueError):
            audit_argv(["ok"], provenance="PRIVATE")

    def test_does_not_mutate(self):
        args = ["app", "--token=abc"]
        audit_argv(args)
        self.assertEqual(args, ["app", "--token=abc"])


if __name__ == "__main__":
    unittest.main()
