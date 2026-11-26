"""Hermetic adversarial tests for the detached PUBLIC_SYNTHETIC object trace."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from mojum.world_room import Room
from mojum.world_object_trace import (
    ATTESTATION_SCHEMA, AUDIENCE, MAX_BYTES, MAX_STEPS,
    attest_trace, decode_trace_json, main, record_trace, _digest,
)


def command(dx=0, dy=0, action="wait", object_id=None, place=None):
    return {"dx": dx, "dy": dy, "action": action,
            "object_id": object_id, "place": place}


class ObjectTraceTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(3, 3, ((2, 1, .1, 1),))
        self.fixtures = [("cube", 1.1, 1.1)]
        self.commands = [
            command(action="pick", object_id="cube"),
            command(dx=.2),
            command(action="place", object_id="cube", place=[1.2, 1.2]),
            command(dx=2),
        ]

    def trace(self):
        return record_trace(self.room, 1, 1, self.fixtures, self.commands)

    def resigned(self, doc):
        doc["trace_sha256"] = _digest({
            k: v for k, v in doc.items() if k != "trace_sha256"
        })
        return doc

    def test_full_pick_move_place_blocked_replay(self):
        doc = self.trace()
        receipt = attest_trace(decode_trace_json(json.dumps(doc).encode()))
        self.assertEqual(receipt["schema"], ATTESTATION_SCHEMA)
        self.assertEqual(receipt["audience"], AUDIENCE)
        self.assertEqual(receipt["verified_steps"], 4)
        self.assertFalse(receipt["physical_contact"])
        self.assertEqual(receipt["authority"], "offline_synthetic_fixture_only")
        self.assertEqual(doc["steps"][0]["object_event"]["carried"], "cube")
        self.assertIsNone(doc["steps"][2]["object_event"]["carried"])
        self.assertTrue(doc["steps"][3]["room_event"]["blocked"])
        self.assertEqual(doc["steps"][3]["room_state"]["x"], 1.2)
        self.assertEqual(doc["steps"][3]["objects_state"]["tick"], 4)

    def test_deterministic_and_nonmutating(self):
        inputs = copy.deepcopy(self.commands)
        fixtures = copy.deepcopy(self.fixtures)
        first = self.trace()
        second = self.trace()
        self.assertEqual(first, second)
        self.assertEqual(self.commands, inputs)
        self.assertEqual(self.fixtures, fixtures)
        self.assertEqual(attest_trace(first)["trace_sha256"], first["trace_sha256"])

    def test_empty_trace(self):
        doc = record_trace(self.room, 1, 1, self.fixtures, [])
        self.assertEqual(attest_trace(doc)["verified_steps"], 0)
        self.assertEqual(doc["final_room_revision"],
                         doc["steps"][-1]["room_state"]["revision"] if doc["steps"] else
                         record_trace(self.room, 1, 1, self.fixtures, [])["final_room_revision"])

    def test_plain_checksum_tampering(self):
        doc = self.trace()
        doc["steps"][0]["object_event"]["action"] = "wait"
        with self.assertRaisesRegex(ValueError, "checksum"):
            attest_trace(doc)

    def test_resigned_object_event_tampering(self):
        doc = self.trace()
        doc["steps"][0]["object_event"]["carried"] = None
        with self.assertRaisesRegex(ValueError, "replay"):
            attest_trace(self.resigned(doc))

    def test_resigned_room_blocked_tampering(self):
        doc = self.trace()
        doc["steps"][-1]["room_event"]["blocked"] = False
        with self.assertRaisesRegex(ValueError, "replay"):
            attest_trace(self.resigned(doc))

    def test_resigned_room_revision_tampering(self):
        doc = self.trace()
        doc["steps"][1]["room_state"]["revision"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "replay"):
            attest_trace(self.resigned(doc))

    def test_resigned_object_snapshot_tampering(self):
        doc = self.trace()
        doc["steps"][1]["objects_state"]["carried"] = None
        with self.assertRaisesRegex(ValueError, "replay"):
            attest_trace(self.resigned(doc))

    def test_resigned_command_change_without_output_change(self):
        doc = self.trace()
        doc["steps"][1]["command"]["dx"] = .1
        with self.assertRaisesRegex(ValueError, "replay"):
            attest_trace(self.resigned(doc))

    def test_missing_or_extra_steps_rejected(self):
        for mutate in (
            lambda d: d["steps"].pop(1),
            lambda d: d["steps"].append(copy.deepcopy(d["steps"][-1])),
        ):
            doc = self.trace()
            mutate(doc)
            with self.assertRaises(ValueError):
                attest_trace(self.resigned(doc))

    def test_extra_field_rejected_even_when_resigned(self):
        doc = self.trace()
        doc["steps"][0]["secret"] = "untrusted"
        with self.assertRaisesRegex(ValueError, "step envelope"):
            attest_trace(self.resigned(doc))

    def test_resigned_bool_tick_and_float_tick_rejected(self):
        for tick in (True, 1.0):
            doc = self.trace()
            doc["steps"][0]["room_state"]["tick"] = tick
            with self.assertRaisesRegex(ValueError, "replay"):
                attest_trace(self.resigned(doc))

    def test_reject_foreign_or_private_audience(self):
        for audience in ("PRIVATE", "PUBLIC", None):
            doc = self.trace()
            doc["audience"] = audience
            with self.assertRaisesRegex(ValueError, "audience"):
                attest_trace(self.resigned(doc))

    def test_reject_duplicate_json_keys(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            decode_trace_json('{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            decode_trace_json('{"x": NaN}')

    def test_limits_and_nonfinite_numeric_values(self):
        with self.assertRaises(ValueError):
            record_trace(self.room, 1, 1, self.fixtures,
                         [command()] * (MAX_STEPS + 1))
        with self.assertRaises(ValueError):
            record_trace(self.room, 1, 1, [("x", 1, 1)] * 17, [])
        with self.assertRaises(ValueError):
            record_trace(self.room, 1, 1, self.fixtures, [command(dx=float("nan"))])
        with self.assertRaises(ValueError):
            record_trace(self.room, 1, 1, self.fixtures, [command(dx=True)])
        with self.assertRaises(ValueError):
            record_trace(Room(1e12, 1), 0, 0, [], [])
        with self.assertRaises(ValueError):
            decode_trace_json(b"0" * (MAX_BYTES + 1))

    def test_reject_invalid_action_without_mutating_inputs(self):
        cases = [
            command(action="pick"),
            command(action="place", object_id="cube"),
            command(action="wait", object_id="cube"),
            command(action="pick", object_id="cube", place=[1, 1]),
        ]
        for entry in cases:
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    record_trace(self.room, 1, 1, self.fixtures, [entry])

    def test_direct_api_rejects_forged_command_keys(self):
        class SpoofedKey:
            def __init__(self, literal):
                self.literal = literal

            def __hash__(self):
                return hash(self.literal)

            def __eq__(self, other):
                return self.literal == other

        class StrSubclass(str):
            pass

        for key in (SpoofedKey("dx"), StrSubclass("dx")):
            with self.subTest(kind=type(key).__name__):
                forged = command()
                forged[key] = forged.pop("dx")
                # Both cases formerly passed set(forged) == _COMMAND_KEYS.
                self.assertEqual(set(forged), {"dx", "dy", "action", "object_id", "place"})
                with self.assertRaisesRegex(ValueError, "invalid command keys"):
                    record_trace(self.room, 1, 1, self.fixtures, [forged])

    def test_malformed_or_forged_state_geometry(self):
        bad = self.trace()
        bad["room"]["obstacles"][0][0] = 1000
        with self.assertRaises(ValueError):
            attest_trace(self.resigned(bad))
        mutated = Room(3, 3)
        object.__setattr__(mutated, "width", True)
        with self.assertRaises(ValueError):
            record_trace(mutated, 1, 1, [], [])

    def test_cli_read_only_success_and_bad_data(self):
        good = self.trace()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.json"
            path.write_text(json.dumps(good), encoding="utf-8")
            captured = io.StringIO()
            with redirect_stdout(captured):
                self.assertEqual(main([str(path)]), 0)
            self.assertEqual(json.loads(captured.getvalue())["verified_steps"], 4)
            self.assertEqual(json.loads(path.read_text()), good)
            path.write_text('{"schema": 1, "schema": 2}', encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                main([str(path)])
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
