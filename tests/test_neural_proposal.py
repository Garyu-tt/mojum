import unittest

from mojum.neural_proposal import validate_neural_proposal


def sample(mode="shadow"):
    return {
        "schema": "mojum.neural-proposal/1",
        "tick": 11,
        "observed_at_ns": 110_000_000,
        "backend": "annarchy",
        "mode": mode,
        "learning_revision": "a" * 64,
        "sensory": [{"port": "interoception.pulse", "value": 0.3}],
        "motor_proposal": None,
    }


class NeuralProposalWireTests(unittest.TestCase):
    def test_shadow_is_input_only(self):
        packet = sample()
        result = validate_neural_proposal(packet)
        self.assertIsNone(result["motor_proposal"])
        packet["sensory"][0]["value"] = 0.99
        self.assertEqual(result["sensory"][0]["value"], 0.3)

    def test_shadow_rejects_motor_proposal(self):
        packet = sample()
        packet["motor_proposal"] = {"dx": 0.1, "dy": 0}
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)

    def test_proposal_is_bounded_but_not_actuated(self):
        packet = sample(mode="proposal")
        packet["motor_proposal"] = {"dx": 0.1, "dy": 0}
        self.assertEqual(validate_neural_proposal(packet)["motor_proposal"]["dx"], 0.1)
        packet["motor_proposal"]["dx"] = 1
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)

    def test_rejects_nan_bool_and_huge_sensory(self):
        for v in (True, float("nan"), float("inf"), 10**1000):
            packet = sample()
            packet["sensory"][0]["value"] = v
            with self.subTest(value=str(v)[:15]):
                with self.assertRaises(ValueError):
                    validate_neural_proposal(packet)

    def test_rejects_motor_nan_and_bad_shape(self):
        for v in (True, float("nan"), float("inf"), 1e10):
            packet = sample(mode="proposal")
            packet["motor_proposal"] = {"dx": v, "dy": 0.0}
            with self.assertRaises(ValueError):
                validate_neural_proposal(packet)
        packet = sample(mode="proposal")
        packet["motor_proposal"] = {"dx": 0, "dy": 0, "actuate": True}
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)

    def test_rejects_unknown_fields_and_versions(self):
        packet = sample()
        packet["engine_command"] = "run"
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)
        packet = sample()
        packet["schema"] = "mojum.neural-proposal/2"
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)

    def test_rejects_duplicates_and_bad_ports(self):
        packet = sample()
        packet["sensory"].append(dict(packet["sensory"][0]))
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)
        packet = sample()
        packet["sensory"][0]["port"] = "../private"
        with self.assertRaises(ValueError):
            validate_neural_proposal(packet)

    def test_rejects_bad_revision_and_tick(self):
        for revision in ("abc", "A" * 64, None):
            packet = sample()
            packet["learning_revision"] = revision
            with self.assertRaises(ValueError):
                validate_neural_proposal(packet)
        for tick in (-1, True, "1"):
            packet = sample()
            packet["tick"] = tick
            with self.assertRaises(ValueError):
                validate_neural_proposal(packet)


if __name__ == "__main__":
    unittest.main()
