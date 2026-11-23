"""Deterministic offline XR_EXT_hand_tracking boundary tests (no VR SDK)."""
import copy
import json
import unittest

from mojum.openxr_hand import (
    EXTENSION, INPUT_SCHEMA, JOINTS, ZERO_HEAD,
    HandStream, HandTrackingError, decode_frame_json, verify_observation_chain,
)


def fixture(*, active=True, hand="left", tick=1, space="local", calibration="a" * 64):
    joints = []
    for joint in JOINTS:
        joints.append({
            "joint": joint,
            "location_flags": 15 if active else 0,
            "position_m": [0.01, 0.02, -0.03] if active else None,
            "orientation_xyzw": [0, 0, 0, 1] if active else None,
            "radius_m": 0.012 if active else None,
        })
    return {
        "schema": INPUT_SCHEMA, "extension": EXTENSION,
        "hand": hand, "base_space": space,
        "calibration_sha256": calibration, "frame_index": tick,
        "time_ns": tick * 11_111_111, "is_active": active, "joints": joints,
    }


class OpenXRHandTests(unittest.TestCase):
    def setUp(self):
        self.stream = HandStream(hand="left", base_space="local", calibration_sha256="a" * 64)

    def accept(self, frame=None):
        return self.stream.ingest(frame or fixture(), expected_head=self.stream.head)

    def test_joint_order_exact_26(self):
        self.assertEqual(len(JOINTS), 26)
        self.assertEqual((JOINTS[0], JOINTS[1], JOINTS[25]), ("palm", "wrist", "little_tip"))

    def test_replay_deterministic(self):
        first = self.accept()
        second = self.accept(fixture(tick=2))
        self.assertEqual(verify_observation_chain([first, second]), second["sha256"])
        other = HandStream(hand="left", base_space="local", calibration_sha256="a" * 64)
        self.assertEqual(first, other.ingest(fixture(), expected_head=ZERO_HEAD))
        self.assertEqual(second, other.ingest(fixture(tick=2), expected_head=other.head))

    def test_inactive_valid_all_null(self):
        obs = self.accept(fixture(active=False))
        self.assertFalse(obs["is_active"])
        self.assertIsNone(obs["joints"][0]["position_m"])
        self.assertEqual(verify_observation_chain([obs]), obs["sha256"])

    def test_valid_but_untracked(self):
        frame = fixture()
        for joint in frame["joints"]:
            joint["location_flags"] = 3
        self.assertEqual(self.accept(frame)["joints"][0]["location_flags"], 3)

    def test_rejected_input_cannot_advance_chain(self):
        frame = fixture()
        frame["joints"][0]["position_m"][0] = float("nan")
        with self.assertRaises(HandTrackingError):
            self.accept(frame)
        self.assertEqual(self.stream.head, ZERO_HEAD)
        self.assertEqual(self.stream.last_frame_index, -1)
        self.accept()

    def test_detached_result_and_input(self):
        frame = fixture()
        obs = self.accept(frame)
        frame["joints"][0]["position_m"][0] = 500
        original_head = obs["sha256"]
        obs["joints"][0]["position_m"][0] = 500
        self.assertEqual(self.stream.last_time_ns, 11_111_111)
        next_obs = self.accept(fixture(tick=2))
        self.assertEqual(next_obs["previous_sha256"], original_head)
        self.assertNotEqual(next_obs["previous_sha256"], ZERO_HEAD)

    def test_malformed_and_reordered_joints(self):
        for edit in (lambda x: x["joints"].pop(),
                     lambda x: x["joints"].reverse(),
                     lambda x: x["joints"][5].update(joint="palm"),
                     lambda x: x["joints"][0].update(secret="not_allowed")):
            frame = fixture()
            edit(frame)
            with self.subTest(edit=edit), self.assertRaises(HandTrackingError):
                self.accept(frame)

    def test_invalid_quaternions(self):
        for value in ([0, 0, 0, 0], [0, 0, 0, 2], [0, 0, 0, float("inf")], [True, 0, 0, 1]):
            frame = fixture()
            frame["joints"][0]["orientation_xyzw"] = value
            with self.subTest(value=value), self.assertRaises(HandTrackingError):
                self.accept(frame)

    def test_negative_radius_nonfinite_or_unbounded_position(self):
        for part, value in (("radius_m", -1), ("radius_m", float("nan")),
                            ("radius_m", 0.3), ("position_m", [1001, 0, 0])):
            frame = fixture()
            frame["joints"][0][part] = value
            with self.subTest(part=part, value=value), self.assertRaises(HandTrackingError):
                self.accept(frame)

    def test_flag_invariants(self):
        for flags in (-1, 16, 0, 1, 2, 4, 8, True):
            frame = fixture()
            frame["joints"][0]["location_flags"] = flags
            with self.subTest(flags=flags), self.assertRaises(HandTrackingError):
                self.accept(frame)

    def test_inactive_rejects_pose_and_flags(self):
        for changed_key, changed_value in (("location_flags", 1), ("position_m", [0, 0, 0]),
                                           ("orientation_xyzw", [0, 0, 0, 1]), ("radius_m", 0)):
            frame = fixture(active=False)
            frame["joints"][0][changed_key] = changed_value
            with self.subTest(key=changed_key), self.assertRaises(HandTrackingError):
                self.accept(frame)

    def test_monotonic_and_optimistic_head(self):
        self.accept()
        for raw, head in ((fixture(), self.stream.head), (fixture(tick=2), ZERO_HEAD)):
            with self.assertRaises(HandTrackingError):
                self.stream.ingest(raw, expected_head=head)
        newer = fixture(tick=2)
        newer["time_ns"] = 1
        with self.assertRaises(HandTrackingError):
            self.accept(newer)
        self.assertEqual(self.stream.last_frame_index, 1)

    def test_cross_hand_and_calibration_denied(self):
        for kw in ({"hand": "right"}, {"space": "stage"}, {"calibration": "b" * 64}):
            with self.subTest(kw=kw), self.assertRaises(HandTrackingError):
                self.accept(fixture(**kw))

    def test_reject_unknown_properties(self):
        frame = fixture()
        frame["participant_id"] = "hidden"
        with self.assertRaises(HandTrackingError):
            self.accept(frame)

    def test_malformed_identifiers(self):
        for kwargs in ({"hand": "ambidextrous", "base_space": "local", "calibration_sha256": "a"*64},
                       {"hand": "left", "base_space": "view", "calibration_sha256": "a"*64},
                       {"hand": "left", "base_space": "local", "calibration_sha256": "XYZ"}):
            with self.assertRaises(HandTrackingError):
                HandStream(**kwargs)

    def test_reject_nonint_clock_bool_and_overflow(self):
        for key, value in (("time_ns", True), ("frame_index", 1.0),
                           ("time_ns", -1), ("time_ns", 2 ** 63), ("is_active", 1)):
            frame = fixture()
            frame[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(HandTrackingError):
                self.accept(frame)

    def test_json_decode_and_duplicate_keys(self):
        raw = json.dumps(fixture()).encode()
        self.assertEqual(decode_frame_json(raw), fixture())
        self.assertEqual(self.stream.ingest_json(raw, expected_head=ZERO_HEAD)["frame_index"], 1)
        for content in (b'{}', b'{"hand":"left","hand":"right"}',
                        raw.replace(b'"time_ns": 11111111', b'"time_ns": NaN'),
                        b'[' * 1500, b'A' * 65537, b'\xff'):
            with self.subTest(content=content[:32]), self.assertRaises(HandTrackingError):
                decode_frame_json(content)

    def test_chain_tamper_detection(self):
        obs = self.accept()
        altered = copy.deepcopy(obs)
        altered["joints"][0]["position_m"][0] = 99
        with self.assertRaises(HandTrackingError):
            verify_observation_chain([altered])

    def test_chain_clock_and_identity_detection(self):
        first = self.accept()
        second = self.accept(fixture(tick=2))
        for frames in ([second], [first, first], [first, first, second]):
            with self.subTest(n=len(frames)), self.assertRaises(HandTrackingError):
                verify_observation_chain(frames)

    def test_no_actuator_or_network_functions(self):
        import mojum.openxr_hand as api
        for forbidden in ("connect", "send", "actuate", "open_runtime", "write_journal"):
            self.assertFalse(hasattr(api, forbidden))


if __name__ == "__main__":
    unittest.main()
