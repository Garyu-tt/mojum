import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mojum.trace_envelope import SCHEMA, TraceEnvelopeError, admit_trace


def sample():
    return dict(schema=SCHEMA, provenance="PUBLIC_SYNTHETIC", consent=True,
                trace_id="trace_12345678", phase="test", outcome="ok",
                duration_ms=20, attempt=1)


class HostileKey(str):
    def __new__(cls, value):
        obj = super().__new__(cls, value)
        obj.hash_calls = 0
        return obj

    def __hash__(self):
        self.hash_calls += 1
        return hash(str(self))

    def __eq__(self, other):
        raise RuntimeError("hostile equality executed")


class TraceEnvelopeTests(unittest.TestCase):
    def test_valid_and_deterministic(self):
        a = sample()
        x = admit_trace(a)
        self.assertEqual(x.sha256, admit_trace(dict(reversed(list(a.items())))).sha256)
        a["phase"] = "patch"
        self.assertEqual(x.fields["phase"], "test")
        with self.assertRaises(TypeError):
            x.fields["phase"] = "review"

    def test_unknown_and_missing_fields(self):
        for mutation in (lambda x: x.update(secret="token"),
                         lambda x: x.pop("attempt")):
            x = sample()
            mutation(x)
            with self.assertRaises(TraceEnvelopeError):
                admit_trace(x)

    def test_invalid_values(self):
        for field, values in {
            "consent": [False, 1, "true"],
            "provenance": ["PRIVATE", None],
            "schema": ["v2", None],
            "trace_id": ["x", "secret@example.com", "a" * 100, 5],
            "phase": ["network", [], None],
            "outcome": ["unknown", {}],
            "duration_ms": [True, -1, 86_400_001, 1.2],
            "attempt": [False, 0, 1001, 1.0],
        }.items():
            for bad in values:
                with self.subTest(field=field, bad=repr(bad)):
                    x = sample()
                    x[field] = bad
                    with self.assertRaises(TraceEnvelopeError):
                        admit_trace(x)

    def test_hostile_containers(self):
        class DictSubclass(dict):
            pass
        for value in [[], None, DictSubclass(sample())]:
            with self.assertRaises(TraceEnvelopeError):
                admit_trace(value)

    def test_hostile_string_key_rejected_before_hash_or_equality(self):
        key = HostileKey("schema")
        value = sample()
        del value["schema"]
        value[key] = SCHEMA
        calls_before = key.hash_calls
        with self.assertRaises(TraceEnvelopeError):
            admit_trace(value)
        self.assertEqual(key.hash_calls, calls_before)

    def test_error_never_echoes_input(self):
        x = sample()
        x["secret"] = "SUPER_SECRET_987"
        try:
            admit_trace(x)
        except TraceEnvelopeError as exc:
            self.assertNotIn("SUPER_SECRET_987", str(exc))
        else:
            self.fail("should reject secret")


if __name__ == "__main__":
    unittest.main()
