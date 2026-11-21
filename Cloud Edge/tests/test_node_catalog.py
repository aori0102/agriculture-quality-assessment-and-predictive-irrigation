import json
import unittest
from unittest.mock import Mock

from cloud_edge.config import CloudConfig, DeviceBinding, load_device_bindings
from cloud_edge.launcher import run_with_clients
from cloud_edge.node_catalog import encode_node_catalog


class NodeCatalogTests(unittest.TestCase):
    def test_catalog_has_only_public_bindings_and_is_sorted(self):
        result = json.loads(encode_node_catalog([
            ("zone-b", 222, "sgp1.blynk.cloud"),
            ("zone-a", None, "sgp1.blynk.cloud"),
        ]))
        self.assertEqual(result, {"v": 1, "nodes": [
            {"n": "zone-a", "d": None, "h": "sgp1.blynk.cloud"},
            {"n": "zone-b", "d": 222, "h": "sgp1.blynk.cloud"},
        ]})
        self.assertNotIn("token", json.dumps(result))

    def test_catalog_overflow_is_explicit(self):
        entries = [(f"zone-{i:02d}", i + 1, "sgp1.blynk.cloud") for i in range(40)]
        self.assertEqual(json.loads(encode_node_catalog(entries))["error"], "catalog_too_large")

    def test_static_device_id_is_optional_but_validated(self):
        values = {"TOKEN": "test-secret", "BLYNK_DEVICES_JSON": json.dumps([
            {"node_id": "zone-a", "server": "sgp1.blynk.cloud",
             "auth_token_env": "TOKEN", "device_id": 123}
        ])}
        self.assertEqual(load_device_bindings(values)[0].device_id, 123)
        values["BLYNK_DEVICES_JSON"] = values["BLYNK_DEVICES_JSON"].replace("123", "0")
        with self.assertRaises(ValueError):
            load_device_bindings(values)
        values["TOKEN2"] = "another-secret"
        values["BLYNK_DEVICES_JSON"] = json.dumps([
            {"node_id": "zone-a", "server": "sgp1.blynk.cloud", "auth_token_env": "TOKEN", "device_id": 123},
            {"node_id": "zone-b", "server": "sgp1.blynk.cloud", "auth_token_env": "TOKEN2", "device_id": 123},
        ])
        with self.assertRaises(ValueError):
            load_device_bindings(values)

    def test_launcher_publishes_catalog_to_each_registered_client(self):
        reporter = Mock()
        clients = [Mock(), Mock()]
        bindings = [DeviceBinding("zone-a", CloudConfig("sgp1.blynk.cloud", "token-a"), 101),
                    DeviceBinding("zone-b", CloudConfig("sgp1.blynk.cloud", "token-b"), 102)]
        factory = Mock(side_effect=clients)
        run_with_clients(lambda: None, object(), bindings,
                         client_factory=factory, reporter=reporter)
        uploads = [call for call in reporter.submit.call_args_list
                   if call.args[0] == "report_node_catalog"]
        self.assertEqual(len(uploads), 2)
        self.assertEqual({call.kwargs["node_id"] for call in uploads}, {"zone-a", "zone-b"})
        self.assertEqual(json.loads(uploads[0].kwargs["catalog"])["nodes"][1]["d"], 102)


if __name__ == "__main__":
    unittest.main()
