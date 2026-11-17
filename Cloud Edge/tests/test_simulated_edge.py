"""Offline tests for the explicit live runner; never contact Blynk."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

from cloud_edge import CloudConfig, PumpCommand
from examples.profile_backend import MemoryBackend
from examples.simulated_edge import DEFAULT_DATASET, Evidence, SimulatedPump, Session, load_dataset, main


class DatasetTests(unittest.TestCase):
    def test_default_fixture_has_expected_rounding_and_missing_values(self):
        rows = load_dataset(DEFAULT_DATASET, "demo-node")
        self.assertEqual(rows[0]["expected"], {
            "v0": 43, "v1": 28.5, "v2": "SIMULATED: Healthy", "v5": 41, "v6": 66,
        })
        self.assertNotIn("v0", rows[-1]["expected"])
        self.assertNotIn("v6", rows[-1]["expected"])
        self.assertEqual(rows[0]["values"]["node_id"], "demo-node")

    def test_rejects_wrong_node_and_invalid_measurements_before_connecting(self):
        original = json.loads(DEFAULT_DATASET.read_text())
        for field, value in (("node_id", "other"), ("soil_index_pct", 101),
                             ("temperature_c", True), ("predicted_moisture_pct", float("nan"))):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                rows = [dict(original[0], **{field: value})]
                path = Path(directory) / "fixture.json"
                path.write_text(json.dumps(rows))
                with self.assertRaises(ValueError):
                    load_dataset(path, "demo-node")


class PumpTests(unittest.TestCase):
    def setUp(self):
        self.pump = SimulatedPump("demo-node")

    def test_request_does_not_change_state_until_acknowledged(self):
        self.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
        self.assertFalse(self.pump.is_on)
        self.assertTrue(self.pump.acknowledge(1, True))
        self.assertTrue(self.pump.is_on)
        with self.assertRaises(ValueError):
            self.pump.acknowledge(1, True)

    def test_expired_disconnected_and_superseded_requests(self):
        self.pump.receive(PumpCommand._received("demo-node", True, -1), 1)
        with self.assertRaises(ValueError):
            self.pump.acknowledge(1, True)
        for generation, ready in ((2, True), (1, False)):
            self.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
            with self.assertRaises(ValueError):
                self.pump.acknowledge(generation, ready)
        self.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
        self.pump.receive(PumpCommand._received("demo-node", False, 5), 1)
        self.assertFalse(self.pump.acknowledge(1, True))

    def test_withholding_and_wrong_identity_never_activate_pump(self):
        self.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
        self.pump.withhold()
        with self.assertRaises(ValueError):
            self.pump.acknowledge(1, True)
        with self.assertRaises(ValueError):
            self.pump.receive(PumpCommand._received("other", True, 5), 1)
        self.assertFalse(self.pump.is_on)


class EvidenceTests(unittest.TestCase):
    def test_successful_upload_is_not_dashboard_pass_and_token_is_redacted(self):
        stream = io.StringIO()
        evidence = Evidence(stream, "test-secret", output=Mock())
        evidence.emit("uploaded", values={"v2": "test-secret"})
        self.assertTrue(all(value == "NOT TESTED" for value in evidence.results.values()))
        evidence.mark("V0", "PASS", "Observed 43")
        self.assertEqual(evidence.results["V0"], "PASS")
        self.assertNotIn("test-secret", stream.getvalue())
        with self.assertRaises(ValueError):
            evidence.mark("V17", "PASS", "invalid")
        with self.assertRaises(ValueError):
            evidence.mark("V1", "PASS", "")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(commands_ready=True, profiles_ready=True, command_connection_id=1,
                           last_command_error=None, last_profile_error=None, last_profile_status="ACTIVE")
        self.client.poll_command.return_value = None
        self.evidence = Evidence(io.StringIO(), "secret", output=Mock())
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        self.session = Session(self.client, MemoryBackend(), "demo-node", [], 10, self.evidence, self.worker)

    def test_poll_stays_responsive_during_upload_and_ack_waits_for_free_worker(self):
        from concurrent.futures import Future
        self.session.future = Future()
        self.client.poll_command.return_value = PumpCommand._received("demo-node", True, 5)
        self.session.tick()
        self.assertIsNotNone(self.session.pump.pending)
        with self.assertRaises(ValueError):
            self.session.handle("ack")
        self.assertFalse(self.session.pump.is_on)

    def test_ack_publishes_simulated_state_and_finish_reports_off(self):
        self.session.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
        self.session.handle("ack")
        self.session.future.result(timeout=1)
        self.client.report_pump_status.assert_called_with(node_id="demo-node", is_on=True)
        self.session.finish()
        self.client.report_pump_status.assert_called_with(node_id="demo-node", is_on=False)

    def test_disconnect_invalidates_pending_request(self):
        self.session.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
        self.client.commands_ready = False
        self.session.tick()
        self.assertIsNone(self.session.pump.pending)

    def test_ack_consumes_newer_off_request_before_simulating_execution(self):
        self.session.pump.receive(PumpCommand._received("demo-node", True, 5), 1)
        self.client.poll_command.return_value = PumpCommand._received("demo-node", False, 5)
        self.session.handle("ack")
        self.session.future.result(timeout=1)
        self.assertFalse(self.session.pump.is_on)
        self.client.report_pump_status.assert_called_with(node_id="demo-node", is_on=False)

    def test_complete_replay_holds_last_sample(self):
        self.session.rows = load_dataset(DEFAULT_DATASET, "demo-node")[:1]
        self.session.tick()
        self.session.future.result(timeout=1)
        self.session.tick()
        self.session.tick()
        self.client.send_snapshot.assert_called_once()
        self.assertTrue(all(v == "NOT TESTED" for v in self.evidence.results.values()))

    def test_shutdown_failure_is_logged_without_raw_exception(self):
        from cloud_edge import CloudError
        self.client.report_pump_status.side_effect = CloudError("secret", category="network")
        self.session.finish()
        self.assertTrue(self.session.failed)
        self.assertIn("final_off_failed", self.evidence.stream.getvalue())
        self.assertNotIn("secret", self.evidence.stream.getvalue())

    def test_cli_invalid_interval_never_constructs_client(self):
        with patch("examples.simulated_edge.CloudClient") as factory:
            with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                main(["--node-id", "demo-node", "--interval", "nan"])
            factory.assert_not_called()

    def test_cli_eof_closes_and_reports_incomplete_without_marking_pins(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("examples.simulated_edge.CloudClient", return_value=self.client), \
                patch("examples.simulated_edge.CloudConfig.from_env",
                      return_value=CloudConfig("sgp1.blynk.cloud", "test-secret")), \
                patch("examples.simulated_edge.select.select", return_value=([True], [], [])), \
                patch("sys.stdin", io.StringIO()), patch("sys.stdout", io.StringIO()):
            path = Path(directory) / "result.log"
            result = main(["--node-id", "demo-node", "--log", str(path)])
            events = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(result, 2)
        self.assertEqual(events[-1]["event"], "summary")
        self.assertEqual(set(events[-1]["results"].values()), {"NOT TESTED"})
        self.client.close.assert_called()
        self.client.report_pump_status.assert_called_with(node_id="demo-node", is_on=False)

    def test_cli_readiness_timeout_cleans_up_and_logs_failure(self):
        self.client.commands_ready = False
        with tempfile.TemporaryDirectory() as directory, \
                patch("examples.simulated_edge.CloudClient", return_value=self.client), \
                patch("examples.simulated_edge.CloudConfig.from_env",
                      return_value=CloudConfig("sgp1.blynk.cloud", "test-secret")), \
                patch("examples.simulated_edge.monotonic", side_effect=[0, 61]), \
                patch("sys.stdout", io.StringIO()):
            path = Path(directory) / "result.log"
            result = main(["--node-id", "demo-node", "--log", str(path)])
            log = path.read_text()
        self.assertEqual(result, 1)
        self.assertIn('"category": "timeout"', log)
        self.assertNotIn("test-secret", log)
        self.client.close.assert_called()
