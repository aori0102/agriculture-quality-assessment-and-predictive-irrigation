import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock
from node_fallback import NodeFallback


class FallbackTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.policy = self.path / 'policies.json'
        self.policy.write_text(json.dumps({'nodes': {}}))
        self.clock = [0.0]
        self.profile = SimpleNamespace(pwp=10, madp=20, fc=36, auto_irrigation_enabled=True)
        self.profiles = SimpleNamespace(read_all_recorded_nodes=lambda: ['n'], get_node_profile=lambda n: self.profile)
        self.client = Mock()
        self.client.publish.return_value.rc = 0
        self.manager = NodeFallback(self.client, 'e', self.profiles, self.policy, self.path/'state.json', clock=lambda: self.clock[0])

    def receive(self, data, retain=False):
        return self.manager.receive(SimpleNamespace(retain=retain, payload=json.dumps(dict(edge_id='e', node_id='n', **data)).encode()), 'n')

    def ack(self, **extra):
        return dict(event='config_applied', config_version=self.manager.state['configs']['n']['version'],
                    session_id=self.manager.session, sync_nonce=self.manager.pending['n'],
                    journal_pending=0, pump_running=False, **extra)

    def test_all_discovered_nodes_receive_automatic_defaults_without_entries(self):
        self.profiles.read_all_recorded_nodes = lambda: ['n', 'new-node']
        self.manager.tick()
        for node in ('n', 'new-node'):
            body = self.manager.state['configs'][node]['body']
            self.assertTrue(body['fallback_enabled'])
            self.assertEqual(body['emergency_threshold_pct'], 15)
            self.assertEqual(body['rearm_threshold_pct'], 20)
            self.assertEqual(body['duration_ms'], 6000)
            self.assertEqual(body['cooldown_ms'], 30000)
        self.assertIn('iot/e/new-node/config', [c.args[0] for c in self.client.publish.call_args_list])

    def test_missing_file_uses_defaults_and_legacy_enabled_cannot_opt_out(self):
        self.policy.unlink()
        self.manager.tick()
        self.assertTrue(self.manager.state['configs']['n']['body']['fallback_enabled'])
        self.policy.write_text(json.dumps({'nodes': {'n': {'enabled': False, 'duration_ms': 7000}}}))
        self.clock[0] = 5
        self.manager.tick()
        body = self.manager.state['configs']['n']['body']
        self.assertTrue(body['fallback_enabled'])
        self.assertEqual(body['duration_ms'], 7000)

    def test_previously_disabled_snapshot_migrates_and_requires_new_ack(self):
        disabled = self.manager.snapshot('n', self.profile, {}, allow_fallback=False)
        self.manager.state['configs']['n'] = {'version': 8, 'body': disabled}
        self.manager.ready['n'] = True
        self.manager.tick()
        self.assertEqual(self.manager.state['configs']['n']['version'], 9)
        self.assertTrue(self.manager.state['configs']['n']['body']['fallback_enabled'])
        self.assertNotIn('n', self.manager.ready)
        self.assertFalse(self.manager.safe_for_prediction('n'))
        self.receive(self.ack())
        self.assertTrue(self.manager.safe_for_prediction('n'))

    def test_invalid_one_node_limits_do_not_disable_other_nodes(self):
        self.profiles.read_all_recorded_nodes = lambda: ['n', 'new-node']
        self.policy.write_text(json.dumps({'nodes': {'n': {'duration_ms': 60001}}}))
        self.manager.tick()
        self.assertFalse(self.manager.state['configs']['n']['body']['fallback_enabled'])
        self.assertTrue(self.manager.state['configs']['new-node']['body']['fallback_enabled'])

    def test_ack_identity_freshness_and_reconnect_gate(self):
        self.manager.tick()
        self.assertFalse(self.manager.safe_for_prediction('n'))
        ack = self.ack()
        self.assertFalse(self.receive(ack, retain=True))
        self.assertFalse(self.receive(dict(ack, session_id='old')))
        self.assertFalse(self.receive(dict(ack, sync_nonce='wrong')))
        self.assertTrue(self.receive(ack))
        self.assertTrue(self.manager.safe_for_prediction('n'))
        self.clock[0] = 16
        self.assertFalse(self.manager.safe_for_prediction('n'))
        self.manager.disconnected()
        self.assertFalse(self.receive(ack))

    def test_invalid_policy_revokes_old_enabled_config(self):
        self.manager.tick()
        self.assertTrue(self.manager.state['configs']['n']['body']['fallback_enabled'])
        self.policy.write_text('{broken')
        self.clock[0] = 5
        self.manager.tick()
        config = self.manager.state['configs']['n']
        self.assertEqual(config['version'], 2)
        self.assertFalse(config['body']['fallback_enabled'])

    def test_auto_off_disables_policy_and_retry_keeps_version(self):
        self.manager.tick()
        self.clock[0] = 5
        self.manager.tick()
        self.assertEqual(self.manager.state['configs']['n']['version'], 1)
        self.profile.auto_irrigation_enabled = False
        self.clock[0] = 10
        self.manager.tick()
        self.assertFalse(self.manager.state['configs']['n']['body']['fallback_enabled'])

    def test_audit_durable_dedup_quarantine_and_pending_gate(self):
        self.manager.tick()
        event = dict(event='fallback_audit', event_id='boot-1', source='local_fallback', phase='stopped', actual_duration_ms=6000)
        self.assertTrue(self.receive(event))
        disk = json.loads((self.path/'state.json').read_text())
        self.assertEqual(len(disk['events']), 1)
        cutoff = disk['quarantine_until']['n']
        self.assertTrue(self.receive(event))
        self.assertEqual(self.manager.state['quarantine_until']['n'], cutoff)
        self.clock[0] = 5
        self.manager.tick()
        self.receive(dict(self.ack(), journal_pending=1))
        self.assertFalse(self.manager.safe_for_prediction('n'))
        self.receive(self.ack())
        self.assertFalse(self.manager.safe_for_prediction('n'))

    def test_threshold_and_duration_rejection(self):
        for options in ({'duration_ms': 60001}, {'duration_ms': True}, {'emergency_threshold_pct': float('nan')},
                        {'rearm_threshold_pct': 14}, {'edge_timeout_ms': 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.manager.snapshot('n', self.profile, options)

    def test_new_pending_journal_revokes_previous_readiness(self):
        self.manager.tick()
        self.receive(self.ack())
        self.assertTrue(self.manager.safe_for_prediction('n'))
        self.clock[0] = 5
        self.manager.tick()
        self.receive(dict(self.ack(), journal_pending=1))
        self.assertFalse(self.manager.safe_for_prediction('n'))

    def test_malformed_audit_is_not_acknowledged(self):
        self.manager.tick()
        self.client.reset_mock()
        self.assertFalse(self.receive(dict(event='fallback_audit', event_id='one', source='local_fallback', phase='stopped', actual_duration_ms=True)))
        self.client.publish.assert_not_called()
        self.assertFalse(self.manager.state['events'])


if __name__ == '__main__':
    main()
