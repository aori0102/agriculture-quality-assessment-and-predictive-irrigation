"""Offline policy tests: no models, broker, or physical pump are loaded."""
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from operation_log_manager import OperationLogManager


class AutoIrrigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = OperationLogManager()
        self.manager.log_file_path = Path(self.temporary.name) / 'operation.json'
        self.manager.log_node('one')
        self.manager.log_irrigation_status('one', False)  # Explicit fixture; independent of node defaults.

    def test_persisted_permission_preserves_profile(self):
        self.assertFalse(self.manager.get_node_profile('one').auto_irrigation_enabled)
        original = self.manager.get_node_profile('one').plant
        self.manager.log_irrigation_status('one', True)
        self.assertTrue(self.manager.get_node_profile('one').auto_irrigation_enabled)
        self.manager.log_irrigation_status('one', False)
        self.assertFalse(self.manager.get_node_profile('one').auto_irrigation_enabled)
        self.assertEqual(self.manager.get_node_profile('one').plant, original)
        self.assertNotIn('irrigation_status', self.manager.load_log()['node_profile']['one'])

    def test_legacy_permission_migrates_without_overriding_new_field(self):
        log = self.manager.load_log()
        profile = log['node_profile']['one']
        profile.pop('auto_irrigation_enabled')
        profile['irrigation_status'] = True
        self.manager.save_log(log)
        self.assertTrue(self.manager.get_node_profile('one').auto_irrigation_enabled)
        self.manager.log_irrigation_status('one', False)
        self.assertFalse(self.manager.get_node_profile('one').auto_irrigation_enabled)

    def test_dispatch_checks_latest_permission_and_propagates_publish_failure(self):
        path = Path(__file__).parent / 'IrrigationPrediction/irrigation_handler.py'
        tree = ast.parse(path.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == 'send_irrigation_command')
        publisher = Mock()
        namespace = {'logger': Mock(), 'operation_log_manager': self.manager, 'command_proxy': publisher,
                     'ActionIndex': SimpleNamespace(StartPump='start_pump')}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
        send = namespace['send_irrigation_command']
        handler = SimpleNamespace(max_duration_ms=60_000)
        self.assertFalse(send(handler, 'one', .5))
        publisher.send_command.assert_not_called()
        self.manager.log_irrigation_status('one', True)
        publisher.send_command.return_value = True
        self.assertTrue(send(handler, 'one', .5))
        self.assertEqual(publisher.send_command.call_args.kwargs['duration_ms'], 30_000)
        publisher.reset_mock()
        # OFF after inference must block the final send, without a stop command.
        self.manager.log_irrigation_status('one', False)
        self.assertFalse(send(handler, 'one', .5))
        publisher.send_command.assert_not_called()
        self.manager.log_irrigation_status('one', True)
        self.assertFalse(send(handler, 'one', 2))
        publisher.send_command.assert_not_called()
        publisher.send_command.return_value = False
        self.assertFalse(send(handler, 'one', .5))


if __name__ == '__main__':
    unittest.main()
