"""No network, models or hardware; verify debug scheduling and node isolation."""
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import Mock, patch
import json
from datetime import datetime, timedelta, timezone

from debug_console import DebugConsole
from node_fallback import NodeFallback


class DebugTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = [0.0]
        self.profile = SimpleNamespace(pwp=10, madp=20, fc=36, auto_irrigation_enabled=True)
        self.profiles = SimpleNamespace(read_all_recorded_nodes=lambda: ['one', 'two'],
                                        get_node_profile=lambda _: self.profile)
        self.client = Mock(); self.client.publish.return_value.rc = 0
        self.manager = NodeFallback(self.client, 'edge', self.profiles,
            Path(self.tmp.name)/'policies.json', Path(self.tmp.name)/'state.json', clock=lambda: self.now[0])
        self.manager.state['configs'] = {n: {'version': 1, 'body': {'fallback_enabled': True}}
                                         for n in ('one', 'two')}
        self.manager.ready = {'one': True, 'two': True}
        self.manager.last_seen = {'one': 0, 'two': 0}
        self.pump = Mock(max_duration_ms=60000, confirmed={'one': False, 'two': False}, pending={})
        self.proxy = Mock()
        self.console = DebugConsole(self.manager, self.pump, self.proxy, self.profiles,
                                    clock=lambda: self.now[0])

    def event(self, event, node='one', **extra):
        simulation = self.console.simulations[node]
        data = dict(event=event, edge_id='edge', node_id=node, session_id=self.manager.session,
                    simulation_id=simulation.id, **extra)
        return SimpleNamespace(retain=False, payload=json.dumps(data).encode())

    def arm(self):
        self.console.execute('simulate_node_connection_lost one')
        self.console.receive(self.event('debug_simulation_armed'), 'one')

    def test_model_quarantine_does_not_block_connection_loss_simulation(self):
        self.manager.state['quarantine_until']['one'] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.assertFalse(self.manager.safe_for_prediction('one'))
        self.assertTrue(self.manager.control_ready('one'))
        self.console.execute('simulate_node_connection_lost one')
        self.assertIn('one', self.console.simulations)
        self.assertEqual(self.console.simulations['one'].phase, 'arming')

    def test_model_quarantine_does_not_bypass_missing_audit_or_stale_ack(self):
        self.manager.state['quarantine_until']['one'] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.manager.ready.pop('one')
        with self.assertRaisesRegex(ValueError, 'config/audit sync'):
            self.console.execute('simulate_node_connection_lost one')
        self.manager.ready['one'] = True
        self.now[0] = 16
        with self.assertRaisesRegex(ValueError, 'config/audit sync'):
            self.console.execute('simulate_node_connection_lost one')
        self.assertFalse(self.console.simulations)

    def test_pump_default_delay_and_explicit_duration(self):
        self.console.enqueue('simulate_pump one 1234'); self.console.tick()
        self.pump.request_debug_start.assert_not_called()
        self.now[0] = 4.999; self.console.tick()
        self.pump.request_debug_start.assert_not_called()
        self.now[0] = 5; self.console.tick()
        self.pump.request_debug_start.assert_called_once_with('one', 1234)
        self.console.tick()
        self.pump.request_debug_start.assert_called_once()

    def test_explicit_zero_delay_capture_and_bad_commands(self):
        self.console.enqueue('simulate_pump one 500 0'); self.console.tick()
        self.pump.request_debug_start.assert_called_once_with('one', 500)
        self.console.execute('simulate_capture two')
        self.assertEqual(self.proxy.send_command.call_args.args[0], 'two')
        for command in ('simulate_pump one -1', 'simulate_pump one 60001',
                        'simulate_pump one 1 -1', 'simulate_pump one 1 120001',
                        'simulate_capture unknown', 'simulate_capture', 'bad',
                        'simulate_node_connection_lost one -1', 'simulate_pump one abc'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.console.execute(command)

    def test_late_dispatch_is_not_replayed(self):
        self.console.execute('simulate_pump one 500')
        self.now[0] = 8; self.console.tick()
        self.pump.request_debug_start.assert_not_called()
        self.assertFalse(self.console.scheduled)

    def test_cancel_scheduled_pump(self):
        self.console.execute('simulate_pump one 500')
        self.console.execute('cancel one')
        self.now[0] = 5; self.console.tick()
        self.pump.request_debug_start.assert_not_called()

    def test_requires_existing_fallback_and_fresh_off_state(self):
        self.manager.state['configs']['one']['body']['fallback_enabled'] = False
        with self.assertRaises(ValueError): self.console.execute('simulate_node_connection_lost one')
        self.manager.state['configs']['one']['body']['fallback_enabled'] = True
        self.pump.confirmed['one'] = True
        with self.assertRaises(ValueError): self.console.execute('simulate_node_connection_lost one')
        self.assertFalse(self.manager.debug_suspended)
        self.assertFalse(self.console.simulations)

    def test_arming_does_not_suppress_heartbeat_until_node_ack(self):
        self.console.execute('simulate_node_connection_lost one 7000')
        self.assertIn('one', self.manager.debug_blocked)
        self.assertNotIn('one', self.manager.debug_suspended)
        body = json.loads(self.client.publish.call_args.args[1])
        self.assertEqual(body['after_pump_delay_ms'], 7000)
        self.assertEqual(body['action'], 'arm')
        self.assertFalse(self.client.publish.call_args.kwargs['retain'])

    def test_only_target_node_loses_heartbeat_and_commands(self):
        self.arm()
        self.manager.tick()
        topics = [call.args[0] for call in self.client.publish.call_args_list]
        self.assertNotIn('iot/edge/one/heartbeat', topics)
        self.assertNotIn('iot/edge/one/config', topics)
        self.assertIn('iot/edge/two/heartbeat', topics)
        self.assertIn('iot/edge/two/config', topics)
        self.assertFalse(self.manager.safe_for_prediction('one'))
        with self.assertRaises(ValueError): self.console.execute('simulate_capture one')

    def test_unmatched_retained_or_premature_ack_cannot_finish(self):
        self.arm()
        data = self.event('debug_simulation_completed', pump_running=False,
                          source='local_fallback', run_id='run', actual_duration_ms=6000)
        decoded = json.loads(data.payload); decoded['simulation_id'] = 'old'
        self.console.receive(SimpleNamespace(payload=json.dumps(decoded).encode(), retain=False), 'one')
        data.retain = True; self.console.receive(data, 'one')
        self.assertIn('one', self.manager.debug_suspended)
        data.retain = False
        decoded = json.loads(data.payload); decoded['pump_running'] = True
        self.console.receive(SimpleNamespace(payload=json.dumps(decoded).encode(), retain=False), 'one')
        self.assertIn('one', self.manager.debug_suspended)

    def test_completion_restores_heartbeat_and_requires_fresh_readiness(self):
        self.arm()
        self.console.receive(self.event('debug_simulation_completed', pump_running=False,
            source='local_fallback', run_id='run', actual_duration_ms=6000), 'one')
        self.assertNotIn('one', self.manager.debug_suspended)
        self.assertNotIn('one', self.manager.debug_blocked)
        self.assertNotIn('one', self.manager.ready)
        self.manager.tick()
        self.assertIn('iot/edge/one/heartbeat', [c.args[0] for c in self.client.publish.call_args_list])

    def test_timeout_cleans_up_at_two_minutes(self):
        self.arm()
        self.now[0] = 119.9; self.console.tick()
        self.assertIn('one', self.manager.debug_suspended)
        self.now[0] = 120; self.console.tick()
        self.assertFalse(self.manager.debug_suspended)
        self.assertFalse(self.manager.debug_blocked)
        self.assertFalse(self.console.simulations)

    def test_measured_stop_duration_can_include_loop_latency(self):
        self.arm()
        self.console.receive(self.event('debug_simulation_completed', pump_running=False,
            source='local_fallback', run_id='run', actual_duration_ms=60001), 'one')
        self.assertFalse(self.console.simulations)

    def test_old_firmware_arm_timeout_does_not_simulate_network_loss(self):
        self.console.execute('simulate_node_connection_lost one')
        self.now[0] = 10; self.console.tick()
        self.assertFalse(self.console.simulations)
        self.assertFalse(self.manager.debug_suspended)
        self.assertFalse(self.manager.debug_blocked)

    def test_eof_cancels_and_closes_debug_without_killing_edge(self):
        self.arm(); self.console.enqueue('__eof'); self.console.tick()
        self.assertTrue(self.console.closed)
        self.assertFalse(self.manager.debug_suspended)
        self.console.tick()

    def test_failed_cancel_transport_still_restores_heartbeat(self):
        self.arm()
        self.manager.publish = Mock(side_effect=RuntimeError('transport failed'))
        self.console.close()
        self.assertFalse(self.manager.debug_suspended)
        self.assertFalse(self.manager.debug_blocked)

    def test_failed_scheduled_command_does_not_stop_tick(self):
        self.console.execute('simulate_pump one 500 0')
        self.pump.request_debug_start.side_effect = RuntimeError('failed')
        self.console.tick()
        self.assertFalse(self.console.scheduled)

    def test_failed_capture_does_not_stop_console(self):
        self.proxy.send_command.side_effect = RuntimeError('failed')
        self.console.enqueue('simulate_capture one')
        self.console.enqueue('simulate_pump one 500 0')
        self.console.tick()
        self.pump.request_debug_start.assert_called_once_with('one', 500)

    def test_disabled_console_not_instantiated_on_normal_app_path(self):
        import ast
        source=Path(__file__).with_name('app.py')
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='main')
        self.assertIs(fn.args.defaults[0].value, False)


class LauncherDebugTests(TestCase):
    def test_debug_argument_reaches_app_only_when_requested(self):
        import argparse, ast
        from threading import RLock
        import importlib
        source = Path(importlib.import_module('cloud_edge.launcher').__file__)
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        for arguments, expected in (([], {}), (['--debug'], {'debug': True})):
            with self.subTest(arguments=arguments):
                app, proxy = Mock(), Mock()
                modules = {'app': app, 'feranmut_proxy': proxy, 'cloud_reporter': Mock(),
                           'IrrigationPrediction.telemetry_handler': Mock()}
                importer=Mock(); importer.import_module.side_effect=lambda name: modules[name]
                runner=Mock(side_effect=lambda entry, *args, **kw: entry())
                namespace=dict(argparse=argparse, REPOSITORY=source.parents[2], ENV_FILE=Path('unused'),
                    ensure_env_file=lambda: False, check_edge_config=Mock(),
                    dotenv_values=lambda *a,**kw: {}, os=SimpleNamespace(environ={}),
                    load_device_bindings=lambda *a,**kw: [], RLock=RLock,
                    importlib=importer, sys=SimpleNamespace(path=[],stderr=Mock()),
                    EdgeProfileBackend=Mock(), EdgePumpBackend=Mock(), run_with_clients=runner)
                exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
                self.assertEqual(namespace['main'](arguments), 0)
                app.main.assert_called_once_with(**expected)


if __name__ == '__main__': main()
