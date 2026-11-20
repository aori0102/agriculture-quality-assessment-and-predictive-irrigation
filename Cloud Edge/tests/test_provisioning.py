from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import Mock

from cloud_edge.blynk_provisioner import AuthorizationRequired, BlynkProvisioner, ProvisioningError
from cloud_edge.device_registry import RuntimeDeviceRegistry
from cloud_edge.device_store import DeviceStore
from cloud_edge.provisioning import DeviceProvisioningWorker, stable_name


class ProvisioningTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = DeviceStore(Path(self.temporary.name) / "private" / "devices.json")
        self.registry = RuntimeDeviceRegistry()
        self.provisioner = Mock()
        self.provisioner.find_device.return_value = None
        self.provisioner.create_device.side_effect = lambda name: {
            "name": name, "id": 42, "templateId": "TMPL6FGqaSpR3", "token": "token-one"
        }
        self.clients = []

        def factory(*args, **kwargs):
            client = Mock()
            self.clients.append(client)
            return client

        self.worker = DeviceProvisioningWorker(
            edge_id="edge-1", approved={"esp01"}, server="sgp1.blynk.cloud",
            store=self.store, registry=self.registry, provisioner=self.provisioner,
            client_factory=factory, profile_backend=None,
        )

    def test_first_observation_creates_once_then_uses_binding(self):
        self.worker._process("esp01", {"soil_index_pct": 30})
        self.assertEqual(self.worker.status["esp01"], "ready")
        self.assertEqual(len(self.store.load()), 1)
        self.assertIsNotNone(self.registry.get("esp01"))
        self.clients[0].send_snapshot.assert_not_called()
        self.worker._process("esp01", {"soil_index_pct": 31})
        self.provisioner.create_device.assert_called_once()
        self.clients[0].send_snapshot.assert_called_once_with(node_id="esp01", soil_index_pct=31)

    def test_restart_restores_without_remote_creation(self):
        self.worker._process("esp01", None)
        self.registry.close()
        self.worker.registry = RuntimeDeviceRegistry()
        self.worker._process("esp01", {"temperature_c": 28})
        self.provisioner.create_device.assert_called_once()
        self.assertEqual(len(self.clients), 2)
        self.clients[1].send_snapshot.assert_called_once()

    def test_existing_remote_or_uncertain_create_requires_reconciliation(self):
        name = stable_name("edge-1", "esp01")
        self.provisioner.find_device.return_value = {"name": name, "id": 42}
        self.worker._process("esp01", None)
        self.assertEqual(self.worker.status["esp01"], "needs_reconciliation")
        self.provisioner.create_device.assert_not_called()
        self.provisioner.find_device.return_value = None
        self.provisioner.create_device.side_effect = ProvisioningError("uncertain")
        self.worker._process("esp01", None)
        self.assertEqual(self.worker.status["esp01"], "needs_reconciliation")
        self.assertEqual(self.store.load(), {})

    def test_unapproved_or_duplicate_observation_never_queues_twice(self):
        self.assertFalse(self.worker.observe_node("stranger"))
        self.assertEqual(self.worker.status["stranger"], "unapproved")
        self.assertTrue(self.worker.observe_node("esp01"))
        self.assertFalse(self.worker.observe_node("esp01"))
        self.assertEqual(self.worker._queue.qsize(), 1)

    def test_search_requires_complete_pagination(self):
        provisioner = BlynkProvisioner(Path("unused"))
        provisioner.call = Mock(side_effect=[
            {"content": [{"name": "other"}], "totalElements": 2},
            {"content": [{"name": "target", "templateId": "TMPL6FGqaSpR3", "id": 9}], "totalElements": 2},
        ])
        self.assertEqual(provisioner.find_device("target")["id"], 9)
        self.assertEqual(provisioner.call.call_count, 2)

    def test_two_approved_nodes_get_distinct_bindings(self):
        self.worker.approved = frozenset({"esp01", "esp02"})
        next_id = iter((42, 43))

        def create(name):
            device_id = next(next_id)
            return {"name": name, "id": device_id, "templateId": "TMPL6FGqaSpR3",
                    "token": f"test-token-{device_id}"}

        self.provisioner.create_device.side_effect = create
        self.worker._process("esp01", None)
        self.worker._process("esp02", None)
        saved = self.store.load()
        self.assertEqual({saved[node].device_id for node in saved}, {42, 43})
        self.assertEqual(len({saved[node].token for node in saved}), 2)
        self.assertEqual(len(self.registry.snapshot()), 2)

    def test_expired_oauth_is_not_marked_as_uncertain_create(self):
        self.provisioner.create_device.side_effect = AuthorizationRequired("reauthorize")
        with self.assertRaises(AuthorizationRequired):
            self.worker._process("esp01", None)
        self.assertEqual(self.store.load(), {})

    def test_background_observation_activates_one_client(self):
        self.worker.start()
        try:
            self.assertTrue(self.worker.observe_node("esp01", {"soil_index_pct": 30}))
            self.assertFalse(self.worker.observe_node("esp01", {"soil_index_pct": 31}))
            deadline = monotonic() + 2
            while self.registry.get("esp01") is None and monotonic() < deadline:
                sleep(.01)
            self.assertIsNotNone(self.registry.get("esp01"))
            self.provisioner.create_device.assert_called_once()
        finally:
            self.worker.close()
            self.registry.close()

