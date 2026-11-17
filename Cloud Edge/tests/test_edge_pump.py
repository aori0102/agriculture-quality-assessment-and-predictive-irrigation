import copy
from threading import RLock, Thread
import unittest
from unittest.mock import Mock

from cloud_edge.commands import PumpCommand
from cloud_edge.edge_pump import EdgePumpBackend, PumpDispatcher


class Client:
    commands_ready = True
    def __init__(self):
        self.pending = None
    def poll_command(self):
        command, self.pending = self.pending, None
        return command


class PumpTests(unittest.TestCase):
    def setUp(self):
        self.log = {'node_profile': {'one': {'node_id': 'one'}, 'two': {'node_id': 'two'}}}
        self.enable = Mock(side_effect=lambda node_id: self.set_flag(node_id, True))
        self.disable = Mock(side_effect=lambda node_id: self.set_flag(node_id, False))
        self.lock = RLock()
        self.backend = EdgePumpBackend(self.enable, self.disable, lambda: copy.deepcopy(self.log), operation_lock=self.lock)
        self.client = Client()

    def set_flag(self, node_id, value):
        self.log['node_profile'][node_id]['irrigation_status'] = value

    def request(self, value=True, node='one', lifetime=5):
        self.client.pending = PumpCommand._received(node, value, lifetime)
        return self.backend.try_dispatch(self.client, node)

    def test_on_off_and_duplicate_skip_with_node_isolation(self):
        self.assertEqual(self.request().outcome, 'saved')
        self.assertEqual(self.request().outcome, 'unchanged')
        self.enable.assert_called_once_with(node_id='one')
        self.assertEqual(self.request(False).outcome, 'saved')
        self.disable.assert_called_once_with(node_id='one')
        self.assertNotIn('irrigation_status', self.log['node_profile']['two'])

    def test_unknown_invalid_flag_and_wrong_binding(self):
        self.assertEqual(self.request(node='unknown').outcome, 'rejected')
        self.set_flag('one', 1)
        self.assertEqual(self.request().outcome, 'sync_failed')
        self.client.pending = PumpCommand._received('two', True, 5)
        self.assertEqual(self.backend.try_dispatch(self.client, 'one').outcome, 'rejected')
        self.enable.assert_not_called()

    def test_exceptions_and_readback_mismatch_never_claim_success(self):
        self.enable.side_effect = OSError('private-secret')
        result = self.request()
        self.assertEqual(result.outcome, 'sync_failed')
        self.assertNotIn('private-secret', result.message)
        self.enable.side_effect = lambda **kw: None
        self.assertEqual(self.request().outcome, 'sync_failed')

    def test_expired_disconnected_and_shutdown_requests_are_discarded(self):
        self.assertEqual(self.request(lifetime=-1).outcome, 'discarded')
        self.client.commands_ready = False
        self.assertEqual(self.request().outcome, 'discarded')
        self.client.commands_ready = True
        self.client.pending = PumpCommand._received('one', True, 5)
        self.assertEqual(self.backend.try_dispatch(self.client, 'one', should_stop=lambda: True).outcome, 'discarded')
        self.enable.assert_not_called()

    def test_off_replaces_on_while_shared_lock_is_busy(self):
        self.client.pending = PumpCommand._received('one', True, 5)
        results = []
        with self.lock:
            worker = Thread(target=lambda: results.append(self.backend.try_dispatch(self.client, 'one')))
            worker.start(); worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results, [None])
            self.assertTrue(self.client.pending.requested_on)
            self.client.pending = PumpCommand._received('one', False, 5)
        self.assertEqual(self.backend.try_dispatch(self.client, 'one').outcome, 'saved')
        self.enable.assert_not_called()
        self.disable.assert_called_once()

    def test_dispatcher_consumes_once_and_exposes_result_without_dashboard_calls(self):
        from tests.test_profile_sync import wait_until
        dispatcher = PumpDispatcher({'one': self.client}, self.backend, interval=.01)
        self.addCleanup(dispatcher.close)
        dispatcher.start()
        self.assertIsNone(dispatcher.last_result('one'))
        self.client.pending = PumpCommand._received('one', True, 5)
        wait_until(lambda: dispatcher.last_result('one') is not None)
        self.assertEqual(dispatcher.last_result('one').outcome, 'saved')
        self.assertIsNone(dispatcher.last_error('one'))
        dispatcher.close()
        self.client.pending = PumpCommand._received('one', False, 5)
        self.enable.assert_called_once()
        self.disable.assert_not_called()

    def test_disconnect_reconnect_during_read_discards_old_request(self):
        self.client.command_connection_id = 1
        def read():
            self.client.command_connection_id = 2
            return self.log
        backend = EdgePumpBackend(self.enable, self.disable, read)
        self.client.pending = PumpCommand._received('one', True, 5)
        self.assertEqual(backend.try_dispatch(self.client, 'one').outcome, 'discarded')
        self.enable.assert_not_called()

    def test_shared_profile_lock_defers_pump_consumption(self):
        from cloud_edge.edge_profiles import EdgeProfileBackend
        profile_backend = EdgeProfileBackend(Mock(), lambda: self.log, operation_lock=self.lock)
        # Both public constructors use the exact shared lock supplied by launcher.
        self.assertIs(profile_backend._lock, self.backend._lock)

