import copy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from cloud_edge.edge_profiles import EdgeProfileBackend
from cloud_edge.profiles import illustrative_preset


class EdgeProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'metadata.json'
        self.profile = illustrative_preset('one', 'Window')
        self.log = {'node_profile': {'one': {'node_id': 'one'}, 'two': {'node_id': 'two'}}}
        self.writer = Mock(side_effect=self.write)
        self.backend = EdgeProfileBackend(self.writer, lambda: copy.deepcopy(self.log), self.path)

    def write(self, **kw):
        self.log['node_profile'][kw['node_id']] = {
            'node_id': kw['node_id'], 'plant': kw['plant'],
            'pwp': kw['permanent_wilting_point'], 'fc': kw['field_capacity'],
            'sp': kw['saturation_point'],
            'madp': kw['field_capacity'] - kw['management_allowable_depletion'] * (
                kw['field_capacity'] - kw['permanent_wilting_point']),
        }

    def test_mapping_readback_and_restart_restore(self):
        result = self.backend.apply_profile(self.profile)
        self.assertEqual(result.outcome, 'saved')
        self.assertIsNone(result.active.revision)
        self.writer.assert_called_once_with(node_id='one', plant='Illustrative Lettuce',
            permanent_wilting_point=.1, management_allowable_depletion=.3,
            field_capacity=.22, saturation_point=.41)
        restored = EdgeProfileBackend(self.writer, lambda: self.log, self.path).get_active('one')
        self.assertAlmostEqual(restored.profile.management_allowed_depletion, .3)
        self.assertEqual(restored.profile.node_description, 'Window')
        self.assertEqual(restored.profile.mode, 'preset')
        self.assertTrue(restored.metadata_complete)
        self.assertTrue(restored.saved_only)
        self.assertNotIn('management_allowed_depletion', self.path.read_text())

    def test_identical_update_skips_edge_write_and_nodes_are_isolated(self):
        self.backend.apply_profile(self.profile)
        self.backend.apply_profile(self.profile)
        self.assertEqual(self.writer.call_count, 1)
        second = replace(self.profile, node_id='two', node_description='Outside')
        self.assertEqual(self.backend.apply_profile(second).outcome, 'saved')
        self.assertEqual(self.backend.get_active('one').profile.node_description, 'Window')
        self.assertEqual(self.backend.get_active('two').profile.node_description, 'Outside')

    def test_unknown_node_rejected_without_write(self):
        result = self.backend.apply_profile(replace(self.profile, node_id='unknown'))
        self.assertEqual(result.outcome, 'rejected')
        self.writer.assert_not_called()

    def test_metadata_failure_does_not_claim_rollback(self):
        with patch('cloud_edge.edge_profiles.os.replace', side_effect=OSError('private-secret')):
            result = self.backend.apply_profile(self.profile)
        self.assertEqual(result.outcome, 'sync_failed')
        self.assertIn('Edge values saved', result.message)
        self.assertEqual(self.log['node_profile']['one']['pwp'], .1)
        self.assertFalse(result.active.metadata_complete)
        self.assertNotIn('private-secret', result.message)

    def test_missing_or_stale_metadata_returns_editing_defaults(self):
        self.backend.apply_profile(self.profile)
        self.log['node_profile']['one']['plant'] = 'Changed at Edge'
        restored = self.backend.get_active('one')
        self.assertFalse(restored.metadata_complete)
        self.assertEqual(restored.profile.plant_name, 'Changed at Edge')
        self.assertEqual(restored.profile.mode, 'custom')
        self.assertEqual(restored.profile.node_description, '')
        self.path.unlink()
        self.assertFalse(self.backend.get_active('one').metadata_complete)

    def test_invalid_saved_thresholds_are_not_guessed_or_converted(self):
        self.backend.apply_profile(self.profile)
        for changes in ({'fc': .1}, {'madp': .5}, {'pwp': 10}, {'madp': True}, {'sp': float('nan')}):
            original = copy.deepcopy(self.log)
            self.log['node_profile']['one'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.backend.get_active('one')
            self.log = original

    def test_write_exception_and_mismatched_readback_are_sync_failures(self):
        self.writer.side_effect = OSError('private-secret')
        result = self.backend.apply_profile(self.profile)
        self.assertEqual(result.outcome, 'sync_failed')
        self.assertNotIn('private-secret', result.message)
        self.writer.side_effect = lambda **kw: None
        self.assertEqual(self.backend.apply_profile(self.profile).outcome, 'sync_failed')
        self.assertFalse(self.path.exists())


    def test_updates_for_different_nodes_are_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Lock
        from time import sleep
        counts = {'active': 0, 'maximum': 0}
        lock = Lock()
        def write(**kwargs):
            with lock:
                counts['active'] += 1
                counts['maximum'] = max(counts['maximum'], counts['active'])
            sleep(.01)
            self.write(**kwargs)
            with lock:
                counts['active'] -= 1
        self.writer.side_effect = write
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.backend.apply_profile,
                [self.profile, replace(self.profile, node_id='two')]))
        self.assertTrue(all(result.outcome == 'saved' for result in results))
        self.assertEqual(counts['maximum'], 1)

    def test_worker_restores_incomplete_metadata_then_saves_without_replay(self):
        from cloud_edge.profile_sync import ProfileSync
        from cloud_edge.datastreams import BlynkTopics
        from cloud_edge.errors import CloudError
        from tests.test_profile_sync import wait_until
        self.write(node_id='one', plant='Initial', permanent_wilting_point=.1,
                   management_allowable_depletion=.3, field_capacity=.22, saturation_point=.41)
        http = Mock()
        http.read_profile_snapshot.return_value = self.profile.to_datastreams()
        sync = ProfileSync(http, self.backend, node_id='one', retry_interval=.01)
        self.addCleanup(sync.close)
        sync.start()
        sync.connected()
        wait_until(lambda: sync.ready)
        self.assertIn('metadata incomplete', sync.last_status)
        failed = []
        def write_status(values):
            if values.get('v15', '').startswith('SAVED') and not failed:
                failed.append(True)
                raise CloudError('Unavailable', category='network')
        http.write_profile.side_effect = write_status
        sync.receive(BlynkTopics.APPLY_PROFILE, '1')
        wait_until(lambda: failed and sync.last_error is None)
        self.assertTrue(sync.last_status.startswith('SAVED'))
        self.assertNotIn('rev=None', sync.last_status)
        self.assertNotIn('metadata incomplete', sync.last_status)
        self.assertEqual(self.writer.call_count, 1)

class RealEdgeContractTests(unittest.TestCase):
    def test_existing_proxy_roundtrip_uses_only_isolated_store(self):
        from configparser import ConfigParser
        import importlib.util
        import sys
        import json
        repo = Path(__file__).resolve().parents[2]
        cfg = ConfigParser()
        cfg.read_dict({'nodes': {'default_node_profile': json.dumps({
            'plant': 'Example', 'pwp': .1, 'madp': .184, 'fc': .22, 'sp': .41})}})
        spec = importlib.util.spec_from_file_location('_isolated_operation_log', repo/'AI/operation_log_manager.py')
        manager_module = importlib.util.module_from_spec(spec)
        # Constructor reads config but does not write files; replace the store
        # before calling ANY mutating method. No live AI app imports occur.
        with patch('configparser.ConfigParser', return_value=cfg):
            spec.loader.exec_module(manager_module)
        with TemporaryDirectory() as directory:
            manager = manager_module.operation_log_manager
            manager.log_file_path = Path(directory)/'operation_log.json'
            manager.log_node('one')
            proxy_spec = importlib.util.spec_from_file_location('_isolated_proxy', repo/'AI/feranmut_proxy.py')
            proxy = importlib.util.module_from_spec(proxy_spec)
            with patch.dict(sys.modules, {'operation_log_manager': manager_module}):
                proxy_spec.loader.exec_module(proxy)
            metadata = Path(directory)/'metadata.json'
            backend = EdgeProfileBackend(proxy.update_node_profile, manager.load_log, metadata)
            profile = illustrative_preset('one', 'Temporary test')
            self.assertEqual(backend.apply_profile(profile).outcome, 'saved')
            # New manager/backend, reading only disk, demonstrates restart readback.
            with patch('configparser.ConfigParser', return_value=cfg):
                restarted = manager_module.OperationLogManager()
            restarted.log_file_path = manager.log_file_path
            restored = EdgeProfileBackend(proxy.update_node_profile, restarted.load_log, metadata).get_active('one')
            self.assertAlmostEqual(restored.profile.management_allowed_depletion, .3)
            self.assertEqual(restored.profile.node_description, 'Temporary test')
            self.assertEqual(restored.profile.mode, 'preset')
            self.assertNotIn('mad', restarted.load_log()['node_profile']['one'])
