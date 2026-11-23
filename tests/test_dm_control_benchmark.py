import hashlib
import importlib.util
import json
import os
import unittest
import xml.etree.ElementTree as ET

from mojum import dm_control_benchmark as benchmark


def make_report(**overrides):
    body = {
        "schema": benchmark.SCHEMA,
        "backend": "dm_control.mujoco",
        "dm_control_version": "1.0.52",
        "mujoco_version": "3.17.0",
        "scenario": "mojum.synthetic.point-room/1",
        "step_count": 10,
        "timestep_ns": 2_000_000,
        "elapsed_ns": 100_000,
        "p50_step_ns": 9_000,
        "p95_step_ns": 14_000,
        "steps_with_contact": 2,
        "max_contacts": 1,
        "peak_traced_bytes": 1024,
        "final_position_m": [1.0, 2.0],
        "trajectory_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "mjcf_sha256": "c" * 64,
    }
    body.update(overrides)
    report = dict(body)
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    report["report_sha256"] = hashlib.sha256(payload).hexdigest()
    return report


class ProbeConfigTests(unittest.TestCase):
    def test_default_fixture_is_bounded_and_builds_deterministically(self):
        config = benchmark.ProbeConfig()
        xml_a = benchmark.build_mjcf_xml(config)
        xml_b = benchmark.build_mjcf_xml(config)
        self.assertEqual(xml_a, xml_b)
        root = ET.fromstring(xml_a)
        self.assertEqual(root.tag, "mujoco")
        self.assertEqual(len(root.findall("./worldbody/body/joint")), 2)
        self.assertEqual(len(root.findall(".//actuator/motor")), 2)
        self.assertEqual(len(root.findall(".//geom[@type='box']")), 2)
        self.assertNotIn("file=", xml_a)
        self.assertNotIn("http://", xml_a)

    def test_config_is_slotted_and_room_dimensions_are_bounded(self):
        config = benchmark.ProbeConfig()
        with self.assertRaises(AttributeError):
            config.__dict__
        with self.assertRaises(AttributeError):
            config.steps = benchmark.MAX_STEPS + 1
        with self.assertRaisesRegex(ValueError, "bounded point-body profile"):
            benchmark.ProbeConfig(width_m=benchmark.MAX_ROOM_EDGE_M + 1)

    def test_config_digest_binds_workload_parameters(self):
        first = benchmark._config_payload(benchmark.ProbeConfig(steps=120))
        second = benchmark._config_payload(benchmark.ProbeConfig(steps=121))
        first_hash = hashlib.sha256(benchmark._canonical_bytes(first)).hexdigest()
        second_hash = hashlib.sha256(benchmark._canonical_bytes(second)).hexdigest()
        self.assertNotEqual(first_hash, second_hash)

    def test_rejects_boolean_dimensions(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(width_m=True)

    def test_rejects_boolean_step_count(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(steps=True)

    def test_rejects_excessive_steps(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(steps=benchmark.MAX_STEPS + 1)

    def test_rejects_nan_dimensions(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(width_m=float("nan"))

    def test_rejects_out_of_room_obstacle(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(obstacles_m=((2.9, 0.1, 0.2, 0.2),))

    def test_rejects_start_overlap(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(obstacles_m=((0.3, 0.3, 0.2, 0.2),))

    def test_rejects_mutable_nested_control_pattern(self):
        with self.assertRaises(ValueError):
            benchmark.ProbeConfig(control_pattern=((1.0, 0.0), [0.0, 1.0]))

    def test_rejects_unknown_config_subclass(self):
        class ExtendedConfig(benchmark.ProbeConfig):
            pass

        with self.assertRaises(ValueError):
            benchmark.build_mjcf_xml(ExtendedConfig())


class ReportTests(unittest.TestCase):
    def test_validates_detaches_and_is_idempotent(self):
        source = make_report()
        result = benchmark.validate_report(source)
        self.assertEqual(result, source)
        self.assertIsNot(result, source)
        self.assertIsNot(result["final_position_m"], source["final_position_m"])
        result["final_position_m"][0] = 99.0
        self.assertEqual(source["final_position_m"], [1.0, 2.0])
        self.assertEqual(benchmark.validate_report(source), source)

    def test_rejects_non_native_report_keys(self):
        report = make_report()
        report[object()] = "not-allowed"
        with self.assertRaisesRegex(ValueError, "native string keys"):
            benchmark.validate_report(report)

    def test_rejects_string_subclass_keys(self):
        class Key(str):
            pass

        report = make_report()
        value = report.pop("step_count")
        report[Key("step_count")] = value
        with self.assertRaisesRegex(ValueError, "native string keys"):
            benchmark.validate_report(report)

    def test_rejects_boolean_integer(self):
        with self.assertRaises(ValueError):
            benchmark.validate_report(make_report(step_count=True))

    def test_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            benchmark.validate_report(make_report(extra="not-allowed"))

    def test_rejects_nonfinite_position(self):
        with self.assertRaises(ValueError):
            benchmark.validate_report(make_report(final_position_m=[float("inf"), 1.0]))

    def test_rejects_bad_digest(self):
        report = make_report()
        report["elapsed_ns"] += 1
        with self.assertRaisesRegex(ValueError, "digest"):
            benchmark.validate_report(report)

    def test_rejects_invalid_workload_hashes(self):
        with self.assertRaisesRegex(ValueError, "config_sha256"):
            benchmark.validate_report(make_report(config_sha256="not-a-hash"))
        with self.assertRaisesRegex(ValueError, "mjcf_sha256"):
            benchmark.validate_report(make_report(mjcf_sha256="g" * 64))

    def test_round_trips_strict_json(self):
        report = make_report()
        payload = json.dumps(report, separators=(",", ":"))
        self.assertEqual(benchmark.decode_report(payload), report)

    def test_rejects_duplicate_json_keys(self):
        report = make_report()
        payload = json.dumps(report, separators=(",", ":"))
        payload = payload.replace('"step_count":10', '"step_count":10,"step_count":11')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            benchmark.decode_report(payload)

    def test_rejects_nonfinite_json_constant(self):
        payload = '{"schema":"x","value":NaN}'
        with self.assertRaisesRegex(ValueError, "non-finite"):
            benchmark.decode_report(payload)

    def test_rejects_oversized_json(self):
        with self.assertRaises(ValueError):
            benchmark.decode_report(" " * (benchmark.MAX_REPORT_BYTES + 1))

    def test_rejects_non_native_text_subclass(self):
        class Text(str):
            pass

        with self.assertRaises(ValueError):
            benchmark.decode_report(Text("{}"))


@unittest.skipUnless(
    os.environ.get("MOJUM_DM_CONTROL_NATIVE") == "1" and importlib.util.find_spec("dm_control"),
    "native dm_control probe runs only in the dedicated optional-dependency workflow",
)
class NativeProbeTests(unittest.TestCase):
    def test_native_engine_executes_fixed_bounded_workload(self):
        report = benchmark.run_benchmark(benchmark.ProbeConfig(steps=120, control_period=20))
        self.assertEqual(report["schema"], benchmark.SCHEMA)
        self.assertEqual(report["backend"], "dm_control.mujoco")
        self.assertEqual(report["step_count"], 120)
        self.assertEqual(len(report["trajectory_sha256"]), 64)
        self.assertEqual(len(report["config_sha256"]), 64)
        self.assertEqual(len(report["mjcf_sha256"]), 64)
        self.assertGreaterEqual(report["p95_step_ns"], report["p50_step_ns"])
        self.assertEqual(benchmark.validate_report(report), report)


if __name__ == "__main__":
    unittest.main()
