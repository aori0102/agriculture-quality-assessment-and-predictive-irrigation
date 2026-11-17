from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cloud_edge.launcher import check_edge_config, main, run_with_clients


class LauncherTests(unittest.TestCase):
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
        with patch('cloud_edge.launcher.check_edge_config'), \
             patch('cloud_edge.launcher.load_device_bindings', return_value=()), \
             patch('cloud_edge.launcher.importlib.import_module') as imports, \
             patch('cloud_edge.launcher.run_with_clients') as run:
            imports.reset_mock()
            self.assertEqual(main(['--check']), 0)
            imports.assert_not_called()
            run.assert_not_called()
