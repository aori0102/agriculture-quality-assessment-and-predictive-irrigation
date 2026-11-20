"""Offline checks for Edge pump policy and device confirmation."""

import unittest
from unittest.mock import Mock, patch

import pump_control


class PumpControlTests(unittest.TestCase):
    def setUp(self):
        self.modes = {"node-a": True, "node-b": True}
        self.log = Mock()
        self.log.load_log.side_effect = lambda: {"node_profile": {
            node: {"node_id": node, "irrigation_status": enabled}
            for node, enabled in self.modes.items()
        }}
        self.log.log_irrigation_status.side_effect = lambda node, value: self.modes.__setitem__(node, value)
        self.proxy = Mock()
        self.proxy.send_command.return_value = True
        self.reporter = Mock()
        self.patches = [
            patch.object(pump_control, "operation_log_manager", self.log),
            patch.object(pump_control, "command_proxy", self.proxy),
            patch.object(pump_control, "cloud_reporter", self.reporter),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.control = pump_control.PumpCoordinator()

    def event(self, state, command_id="", sequence=1, node="node-a"):
        self.control.enqueue_event(node, {
            "event": "pump_started" if state else "pump_stopped",
            "boot_id": "boot-1", "sequence": sequence, "command_id": command_id,
        })
        self.control.tick()

    def test_manual_off_blocks_auto_and_sends_node_specific_stop(self):
        self.assertTrue(self.control.request_manual("node-a", False))
        self.assertFalse(self.modes["node-a"])
        self.assertTrue(self.modes["node-b"])
        self.assertFalse(self.control.request_automatic_start("node-a", 1000))
        args, kwargs = self.proxy.send_command.call_args
        self.assertEqual(args[0], "node-a")
        self.assertEqual(args[1], pump_control.ActionIndex.StopPump)
        self.assertEqual(len(kwargs["command_id"]), 32)
        self.reporter.submit.assert_any_call("report_control_mode", node_id="node-a", enabled=False)

    def test_manual_on_requires_fresh_off_and_uses_bounded_duration(self):
        self.assertFalse(self.control.request_manual("node-a", True))
        self.proxy.send_command.assert_not_called()
        self.event(False)
        self.assertTrue(self.control.request_manual("node-a", True))
        args, kwargs = self.proxy.send_command.call_args
        self.assertEqual(args[1], pump_control.ActionIndex.StartPump)
        self.assertEqual(kwargs["duration_ms"], 30000)
        self.assertEqual(self.control.confirmed["node-a"], False)
        self.assertFalse(self.control.request_manual("node-a", True))
        self.assertEqual(self.proxy.send_command.call_count, 1)
        self.event(True, kwargs["command_id"], 2)
        self.assertTrue(self.control.confirmed["node-a"])
        self.reporter.submit.assert_any_call("report_pump_status", node_id="node-a", is_on=True)

    def test_pending_start_does_not_confirm_from_wrong_command(self):
        self.event(False)
        self.assertTrue(self.control.request_automatic_start("node-a", 60000))
        self.event(True, "wrong", 2)
        self.assertFalse(self.control.confirmed["node-a"])
        self.assertFalse(self.control.request_automatic_start("node-a", 60000))
        self.event(True, self.proxy.send_command.call_args.kwargs["command_id"], 3)
        self.assertTrue(self.control.confirmed["node-a"])

    def test_timeout_marks_state_unknown_without_retry(self):
        self.event(False)
        self.assertTrue(self.control.request_automatic_start("node-a", 1000))
        with patch.object(pump_control, "monotonic", return_value=float("inf")):
            self.control.tick()
        self.assertNotIn("node-a", self.control.confirmed)
        self.assertEqual(self.proxy.send_command.call_count, 1)
        self.reporter.submit.assert_any_call(
            "report_pump_detail", node_id="node-a",
            detail="unknown: device acknowledgement timed out")

    def test_device_heartbeat_expiry_blocks_new_starts(self):
        self.event(False)
        self.assertFalse(self.control.confirmed["node-a"])
        with patch.object(pump_control, "monotonic", return_value=float("inf")):
            self.control.tick()
        self.assertNotIn("node-a", self.control.confirmed)
        self.assertFalse(self.control.request_automatic_start("node-a", 1000))
        self.proxy.send_command.assert_not_called()

    def test_auto_restore_waits_for_stop_confirmation(self):
        self.modes["node-a"] = False
        self.event(True)
        self.assertTrue(self.control.request_auto("node-a", True))
        self.assertFalse(self.modes["node-a"])
        command_id = self.proxy.send_command.call_args.kwargs["command_id"]
        self.event(False, command_id, 2)
        self.assertTrue(self.modes["node-a"])


if __name__ == "__main__":
    unittest.main()
