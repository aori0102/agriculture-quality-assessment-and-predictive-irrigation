from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
import os
from unittest.mock import Mock, patch

from cloud_edge.launcher import acquire_instance_lock, check_edge_config, main, run_with_clients


class LauncherTests(unittest.TestCase):
    def test_hybrid_dynamic_node_joins_shared_reporter_and_dispatcher(self):
        from cloud_reporter import CloudReporter
        with TemporaryDirectory() as directory:
            root = Path(directory)
            handler = SimpleNamespace(on_accepted=None)
            reporter = CloudReporter()
            static, dynamic = Mock(), Mock()
            factory = Mock(side_effect=[static, dynamic])
            provisioner = Mock()
            provisioner.find_device.return_value = None
            provisioner.create_device.return_value = {
                'id': 42, 'token': 'test-dynamic-token', 'templateId': 'TMPL6FGqaSpR3',
            }
            pump_backend = Mock()
            pump_backend.try_dispatch.return_value = None

            def edge_main():
                handler.on_accepted('static', {'soil_index_pct': 40})
                handler.on_accepted('esp_new_a', {'soil_index_pct': 42})
                deadline = monotonic() + 2
                while dynamic.send_telemetry.call_count < 1 and monotonic() < deadline:
                    sleep(.01)
                dynamic.send_telemetry.assert_called_once_with(node_id='esp_new_a', soil_index_pct=42)
                while dynamic.report_node_catalog.call_count < 1 and monotonic() < deadline:
                    sleep(.01)
                dynamic.report_node_catalog.assert_called()
                handler.on_accepted('esp_new_a', {'soil_index_pct': 43})
                reporter.submit('send_ai_results', node_id='esp_new_a', plant_status='Healthy')
                reporter.submit('report_pump_status', node_id='esp_new_a', is_on=False)
                while not any(call.args[1] == 'esp_new_a' for call in pump_backend.try_dispatch.call_args_list) and monotonic() < deadline:
                    sleep(.01)
                self.assertTrue(any(call.args[1] == 'esp_new_a' for call in pump_backend.try_dispatch.call_args_list))

            settings = {
                'edge_id': 'edge-1', 'server': 'sgp1.blynk.cloud',
                'device_store': root / 'private/devices.json',
                'oauth_store': root / 'private/oauth.json',
            }
            with patch('cloud_edge.launcher.BlynkProvisioner', return_value=provisioner):
                run_with_clients(edge_main, object(),
                    [SimpleNamespace(node_id='static', device_id=41,
                                     config=SimpleNamespace(auth_token='test-static-token', server='sgp1.blynk.cloud'))],
                    client_factory=factory, pump_backend=pump_backend,
                    telemetry_handler=handler, reporter=reporter, provisioning=settings)
            static.send_telemetry.assert_called_once_with(node_id='static', soil_index_pct=40)
            self.assertEqual(dynamic.send_telemetry.call_args_list, [
                unittest.mock.call(node_id='esp_new_a', soil_index_pct=42),
                unittest.mock.call(node_id='esp_new_a', soil_index_pct=43),
            ])
            dynamic.send_ai_results.assert_called_once_with(node_id='esp_new_a', plant_status='Healthy')
            dynamic.report_pump_status.assert_called_once_with(node_id='esp_new_a', is_on=False)
            import json
            catalog = json.loads(dynamic.report_node_catalog.call_args.kwargs['catalog'])
            self.assertEqual({entry['n']: entry['d'] for entry in catalog['nodes']},
                             {'static': 41, 'esp_new_a': 42})
            provisioner.create_device.assert_called_once()
            self.assertIsNone(handler.on_accepted)

    def test_dynamic_only_check_needs_no_node_list(self):
        values = {
            'BLYNK_PROVISIONING_ENABLED': '1',
            'BLYNK_PROVISION_SERVER': 'sgp1.blynk.cloud',
            'BLYNK_DEVICES_JSON': '[]',
            'BLYNK_APPROVED_NODES': 'ignored;invalid',
        }
        with patch('cloud_edge.launcher.ensure_env_file', return_value=False), \
             patch('cloud_edge.launcher.check_edge_config'), \
             patch('cloud_edge.launcher.dotenv_values', return_value=values), \
             patch.dict('cloud_edge.launcher.os.environ', {}, clear=True), \
             patch('cloud_edge.launcher.importlib.import_module') as imports:
            self.assertEqual(main(['--check']), 0)
            imports.assert_not_called()

    def test_static_mode_routes_accepted_telemetry_through_reporter(self):
        from cloud_reporter import CloudReporter
        handler = SimpleNamespace(on_accepted=None)
        reporter, client = CloudReporter(), Mock()
        binding = SimpleNamespace(node_id='esp01', config=object())

        def edge_main():
            handler.on_accepted('esp01', {'soil_index_pct': 42})
            return 'done'

        result = run_with_clients(edge_main, object(), [binding],
                                  client_factory=Mock(return_value=client),
                                  telemetry_handler=handler, reporter=reporter)
        self.assertEqual(result, 'done')
        client.send_telemetry.assert_called_once_with(node_id='esp01', soil_index_pct=42)
        self.assertIsNone(handler.on_accepted)

    def test_single_instance_lock_rejects_second_holder(self):
        import os
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'private' / 'edge.lock'
            first = acquire_instance_lock(path)
            try:
                with self.assertRaises(BlockingIOError):
                    acquire_instance_lock(path)
            finally:
                os.close(first)
            second = acquire_instance_lock(path)
            os.close(second)

    def test_status_cli_reads_only_nonsecret_binding_data(self):
        from cloud_edge.device_store import CreateJournal, DeviceStore, StoredDevice
        from contextlib import redirect_stdout
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'Cloud Edge/runtime'
            store = DeviceStore(runtime / 'blynk_devices.json')
            store.save({'one': StoredDevice('one', 'aiot-one', 17, 'sgp1.blynk.cloud', 'private-token')})
            CreateJournal(runtime / 'pending_creates.json').save({'two'})
            output = StringIO()
            with patch('cloud_edge.launcher.REPOSITORY', root), redirect_stdout(output):
                self.assertEqual(main(['--provisioning-status']), 0)
            self.assertIn('device_id=17', output.getvalue())
            self.assertIn('needs_reconciliation', output.getvalue())
            self.assertNotIn('private-token', output.getvalue())

    def test_partial_startup_closes_every_constructed_client(self):
        clients = [Mock(), Mock()]
        clients[1].start.side_effect = RuntimeError('failed')
        factory = Mock(side_effect=clients)
        bindings = [SimpleNamespace(node_id='one', config=object()), SimpleNamespace(node_id='two', config=object())]
        edge_main, backend = Mock(), object()
        with self.assertRaises(RuntimeError):
            run_with_clients(edge_main, backend, bindings, client_factory=factory)
        edge_main.assert_not_called()
        for client in clients:
            client.close.assert_called_once()
        self.assertTrue(all(call.kwargs['profile_backend'] is backend for call in factory.call_args_list))

    def test_edge_runtime_failure_also_closes_clients(self):
        client = Mock()
        with self.assertRaises(OSError):
            run_with_clients(Mock(side_effect=OSError()), object(),
                [SimpleNamespace(node_id='one', config=object())], client_factory=Mock(return_value=client))
        client.close.assert_called_once()

    def test_missing_default_profile_has_actionable_error(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'config.cfg').write_text('[nodes]\nacquired_nodes_path=node_history.json\n')
            with self.assertRaisesRegex(ValueError, 'default_node_profile'):
                check_edge_config(path)

    def test_check_does_not_import_ai_or_connect(self):
        with patch('cloud_edge.launcher.ensure_env_file', return_value=False), \
             patch('cloud_edge.launcher.check_edge_config'), \
             patch('cloud_edge.launcher.load_device_bindings', return_value=()), \
             patch('cloud_edge.launcher.importlib.import_module') as imports, \
             patch('cloud_edge.launcher.run_with_clients') as run:
            imports.reset_mock()
            self.assertEqual(main(['--check']), 0)
            imports.assert_not_called()
            run.assert_not_called()

    def test_dispatcher_stops_before_clients_even_when_start_fails(self):
        events = []
        client, dispatcher = Mock(), Mock()
        client.close.side_effect = lambda: events.append('client')
        dispatcher.close.side_effect = lambda: events.append('dispatcher')
        dispatcher.start.side_effect = RuntimeError('startup failed')
        with self.assertRaises(RuntimeError):
            run_with_clients(Mock(), object(), [SimpleNamespace(node_id='one', config=object())],
                client_factory=Mock(return_value=client), pump_backend=object(),
                dispatcher_factory=Mock(return_value=dispatcher))
        self.assertEqual(events, ['dispatcher', 'client'])


class EnvPlaceholderTests(unittest.TestCase):
    def test_missing_env_created_once_and_never_overwritten(self):
        from cloud_edge.config import ensure_env_file
        from dotenv import dotenv_values
        import json
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            with patch('cloud_edge.config.ENV_FILE', path):
                self.assertTrue(ensure_env_file())
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                values = dotenv_values(path)
                entries = json.loads(values['BLYNK_DEVICES_JSON'])
                self.assertTrue(entries)
                self.assertEqual(values[entries[0]['auth_token_env']], '')
                path.write_text('PRIVATE_TOKEN=keep-this-exactly\n', encoding='utf-8')
                before = path.read_bytes()
                self.assertFalse(ensure_env_file())
                self.assertEqual(path.read_bytes(), before)

    def test_first_start_logs_path_and_stops_before_clients_or_ai(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            output = StringIO()
            with patch('cloud_edge.config.ENV_FILE', path), \
                 patch('cloud_edge.launcher.ENV_FILE', path), \
                 patch('cloud_edge.launcher.load_device_bindings') as load, \
                 patch('cloud_edge.launcher.importlib.import_module') as imports, \
                 redirect_stderr(output):
                self.assertEqual(main([]), 2)
            self.assertTrue(path.exists())
            self.assertIn(str(path), output.getvalue())
            self.assertIn('Auth Token', output.getvalue())
            load.assert_not_called()
            imports.assert_not_called()

    def test_env_create_error_is_reported_without_startup(self):
        output = StringIO()
        with patch('cloud_edge.launcher.ensure_env_file', side_effect=PermissionError()), \
             patch('cloud_edge.launcher.load_device_bindings') as load, redirect_stderr(output):
            self.assertEqual(main([]), 2)
        load.assert_not_called()
        self.assertIn('Check configuration in:', output.getvalue())
