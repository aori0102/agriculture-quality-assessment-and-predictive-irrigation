import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import run_cloud_demo as demo
from cloud_edge import CloudError


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class StandaloneCloudDemoTests(unittest.TestCase):
    def setUp(self):
        self.binding = SimpleNamespace(node_id="fake-node", config=SimpleNamespace(server="sgp1.blynk.cloud"))
        self.client = Mock()
        self.client.commands_ready = True
        self.client.last_command_error = None
        self.client.poll_command.return_value = None
        self.output = StringIO()
        self.clock = Clock()

    def run_fake(self, **kwargs):
        with redirect_stdout(self.output), redirect_stderr(self.output):
            return demo.run_demo(self.binding, duration=0.4, interval=0.2,
                client_factory=Mock(return_value=self.client), clock=self.clock,
                pause=self.clock.sleep, **kwargs)

    def test_uplink_and_simulated_pump_downlink(self):
        command = SimpleNamespace(requested_on=True, is_expired=lambda: False)
        pending = [command]
        self.client.poll_command.side_effect = lambda: pending.pop() if pending else None
        self.assertEqual(self.run_fake(require_command=True), 0)
        self.client.report_pump_status.assert_called_once_with(node_id="fake-node", is_on=True)
        self.assertTrue(self.client.send_snapshot.call_args.kwargs['pump_is_on'])
        self.client.close.assert_called_once()

    def test_missing_downlink_is_not_reported_as_verified(self):
        self.assertEqual(self.run_fake(require_command=True), 1)
        self.assertIn("Downlink not verified", self.output.getvalue())
        self.client.report_pump_status.assert_not_called()

    def test_expired_command_is_ignored(self):
        self.client.poll_command.return_value = SimpleNamespace(is_expired=lambda: True)
        self.assertEqual(self.run_fake(require_command=True), 1)
        self.client.report_pump_status.assert_not_called()

    def test_upload_error_is_sanitized_and_client_closed(self):
        self.client.send_snapshot.side_effect = CloudError("private-token", category="network")
        self.assertEqual(self.run_fake(), 1)
        self.assertNotIn("private-token", self.output.getvalue())
        self.client.close.assert_called_once()

    def test_dry_run_never_loads_credentials_or_connects(self):
        with patch.object(demo, 'load_device_bindings') as load, patch.object(demo, 'CloudClient') as client, redirect_stdout(self.output):
            self.assertEqual(demo.main(['--dry-run']), 0)
        load.assert_not_called()
        client.assert_not_called()

    def test_fake_measurements_in_range(self):
        for step in range(100):
            values = demo.fake_snapshot(step, bool(step % 2))
            for key in ['soil_index_pct', 'air_humidity_pct', 'predicted_moisture_pct']:
                self.assertTrue(0 <= values[key] <= 100)
            self.assertIn('SIMULATED', values['plant_status'])


if __name__ == '__main__':
    unittest.main()
