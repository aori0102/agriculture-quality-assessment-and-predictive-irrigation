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

    def rejection(self, **changes):
        data = dict(event="pump_start_rejected", boot_id="boot-1", sequence=2,
                    command_id=self.control.pending["node-a"][0], reason="cooldown",
                    detail="Wait 240000 ms after last stop", retry_after_ms=240000)
        data.update(changes)
        return data

    def test_rejection_reports_reason_without_false_off_or_later_timeout(self):
        self.event(False)
        self.assertTrue(self.control.request_automatic_start("node-a", 1000))
        data = self.rejection()
        self.reporter.reset_mock()
        with self.assertLogs('edge.pump', level='WARNING') as logs:
            self.control.enqueue_event('node-a', data)
            self.control.tick()
        self.assertIn('reason=cooldown', logs.output[0])
        self.assertIn('240000', logs.output[0])
        self.assertNotIn('node-a', self.control.pending)
        self.assertIs(self.control.confirmed['node-a'], False)
        self.reporter.submit.assert_called_once_with('report_pump_detail', node_id='node-a',
            detail='rejected: cooldown; Wait 240000 ms after last stop')
        self.reporter.reset_mock()
        with patch.object(pump_control, 'monotonic', return_value=float('inf')):
            self.control.tick()
        self.reporter.emit_event.assert_not_called()

    def test_wrong_command_or_malformed_rejection_cannot_clear_pending_start(self):
        self.event(False)
        self.assertTrue(self.control.request_automatic_start('node-a', 1000))
        for fields in (dict(command_id='old'), dict(reason={}), dict(reason='unknown'),
                       dict(retry_after_ms=True), dict(retry_after_ms=-1), dict(detail='bad\ntext')):
            self.control._accept_event('node-a', self.rejection(**fields))
            self.assertIn('node-a', self.control.pending)

    def test_connected_loop_applies_queued_off_before_due_debug_dispatch(self):
        import ast
        from pathlib import Path
        from types import SimpleNamespace
        from debug_console import DebugConsole
        self.event(True)
        self.control.enqueue_event('node-a', dict(event='pump_stopped', boot_id='boot-1', sequence=2, command_id=''))
        self.assertTrue(self.control.confirmed['node-a'])
        console = DebugConsole(Mock(), self.control, self.proxy,
            SimpleNamespace(read_all_recorded_nodes=lambda: ['node-a']))
        console.execute('simulate_pump node-a 1000 0')
        source = Path(__file__).with_name('app.py')
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and isinstance(n.test, ast.BoolOp)
                      and ast.unparse(n.test) == 'ready and client.is_connected()')
        loop = ast.While(test=ast.Constant(value=True), body=branch.body+[ast.Break()], orelse=[])
        module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        retrainer = Mock(); retrainer.tick.return_value=False
        namespace = dict(pump_coordinator=self.control, node_fallback=SimpleNamespace(active_fallback=Mock()),
            debug_console=console, retrainer=retrainer, image_future=None,
            irrigation_handler=Mock(), capture_handler=Mock())
        with patch.object(pump_control, 'control_ready', return_value=True):
            exec(compile(module, str(source), 'exec'), namespace)
        self.proxy.send_command.assert_called_once()
        self.assertEqual(self.proxy.send_command.call_args.args, ('node-a',pump_control.ActionIndex.StartPump))
        self.assertIn('node-a',self.control.pending)

    def test_debug_start_reports_exact_precondition_and_never_sends(self):
        self.event(False)
        self.control.pending['node-a']=('previous',pump_control.ActionIndex.StartPump.value,999999999999)
        with self.assertLogs('edge.pump',level='WARNING') as logs:
            self.assertFalse(self.control.request_debug_start('node-a',1000))
        self.assertIn('previous_command_pending',logs.output[0])
        self.control.pending.clear()
        with patch.object(pump_control,'control_ready',return_value=False), self.assertLogs('edge.pump',level='WARNING') as logs:
            self.assertFalse(self.control.request_debug_start('node-a',1000))
        self.assertIn('config_or_audit_sync_not_ready',logs.output[0])
        self.proxy.send_command.assert_not_called()

    def test_debug_start_rejects_old_off_even_before_tick_expires_it(self):
        self.event(False)
        now=self.control.last_event_at['node-a']
        with patch.object(pump_control,'monotonic',return_value=now+15), self.assertLogs('edge.pump',level='WARNING') as logs:
            self.assertFalse(self.control.request_debug_start('node-a',1000))
        self.assertIn('pump_OFF_state_stale',logs.output[0])
        self.proxy.send_command.assert_not_called()

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
