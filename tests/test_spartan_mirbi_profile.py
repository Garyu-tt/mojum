"""Offline regression tests for the opt-in Mirbi CPU-only Spartan profile."""
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import spartan_coordinator as coordinator
from tools import spartan_local_fleet as fleet


def mirbi_fake_http(port, path, body=None, timeout=5):
    capacities = {48081: 2, 48082: 6, 48083: 4, 48084: 4}
    if port not in capacities:
        raise AssertionError("unexpected remote backend")
    if path == "/health":
        return {"status": "ok"}
    if path == "/slots":
        return [{"id": i, "n_ctx": 2048, "is_processing": False}
                for i in range(capacities[port])]
    raise AssertionError("unneeded model request")


class MirbiProfileTests(unittest.TestCase):
    def test_profile_selects_four_explicit_local_cpu_processes(self):
        models = fleet.discover(request=mirbi_fake_http, profile="mirbi-linux")
        self.assertEqual([m.port for m in models], [48081, 48082, 48083, 48084])
        self.assertEqual([m.free for m in models], [2, 6, 4, 4])
        self.assertEqual(sum(m.free for m in models), 16)
        self.assertTrue(all(m.device.startswith("cpu") for m in models))
        self.assertEqual(len({m.model for m in models}), 2)

    def test_original_vm200_profile_is_unchanged_and_default(self):
        self.assertEqual(tuple(x[0] for x in fleet.BACKENDS),
                         (18182, 18183, 18180, 18181))
        self.assertEqual(tuple(x[0] for x in fleet.BACKEND_PROFILES["vm200"]),
                         tuple(x[0] for x in fleet.BACKENDS))

    def test_unknown_profile_fails_before_probing(self):
        with self.assertRaisesRegex(fleet.FleetError, "unknown explicit"):
            fleet.discover(request=mirbi_fake_http, profile="third-party")

    def test_local_http_allowlist_still_rejects_unapproved_port_and_path(self):
        with self.assertRaisesRegex(fleet.FleetError, "unapproved loopback port"):
            fleet._http(48085, "/health")
        with self.assertRaisesRegex(fleet.FleetError, "unapproved API path"):
            fleet._http(48082, "/anything")

    def test_existing_coordinator_runs_without_model_calls_when_queue_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "tasks.sqlite3"
            out = root / "private-proposals"
            out.mkdir(mode=0o700)
            with closing(coordinator.connect(str(db_path))) as conn:
                coordinator.create_schema(conn)
                coordinator.init(conn, 300, 100)

            with patch.object(fleet, "discover",
                              return_value=fleet.discover(
                                  request=mirbi_fake_http,
                                  profile="mirbi-linux")) as discovery:
                report = fleet.run(db_path, out, max_agents=300, max_jobs=2,
                                   backend_profile="mirbi-linux")
            discovery.assert_called_once_with(profile="mirbi-linux")
            self.assertEqual(report["backend_profile"], "mirbi-linux")
            self.assertEqual(report["addressable_agents"], 300)
            self.assertEqual(report["live_free_model_slots"], 16)
            self.assertEqual(report["configured_physical_call_limit"], 4)
            self.assertEqual(report["scheduled_primary_workers"], 2)
            self.assertEqual(report["no_task"], 2)
            self.assertEqual(report["model_invocation_attempts"], 0)
            self.assertEqual(list(out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
