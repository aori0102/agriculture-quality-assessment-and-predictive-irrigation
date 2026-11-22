from concurrent.futures import ThreadPoolExecutor
import importlib
import threading
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
import requests

from cloud_edge import CloudClient, CloudConfig, CloudError, PumpCommand
from cloud_edge.datastreams import BlynkTopics


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        response._content_consumed = True
        self.session.get.return_value = response
        self.network = Mock(spec=mqtt.Client)
        self.network.loop_start.return_value = mqtt.MQTT_ERR_SUCCESS
        self.network.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 7)
        for target, replacement in (
            ("cloud_edge.blynk_http.requests.Session", self.session),
            ("cloud_edge.blynk_mqtt.mqtt.Client", self.network),
        ):
            patcher = patch(target, return_value=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = CloudConfig("sgp1.blynk.cloud", "test-secret")
        self.client = CloudClient(self.config, node_id="node-1")
        self.addCleanup(self.client.close)

    def test_log_event_uses_device_endpoint_and_validates_node(self):
        self.client.log_event(node_id="node-1", code="soil_moisture_low", description="Soil 15%")
        call = self.session.get.call_args
        self.assertTrue(call.args[0].endswith("/external/api/logEvent"))
        self.assertEqual(call.kwargs["params"]["code"], "soil_moisture_low")
        self.assertEqual(call.kwargs["params"]["description"], "Soil 15%")
        self.assertTrue(call.kwargs["verify"])
        self.session.get.reset_mock()
        with self.assertRaises(ValueError):
            self.client.log_event(node_id="other", code="pump_started")
        for code, description in (("bad&code", ""), ("", ""), ("valid", "x" * 301)):
            with self.assertRaises(ValueError):
                self.client.log_event(node_id="node-1", code=code, description=description)
        self.session.get.assert_not_called()

    def test_snapshot_and_convenience_methods_have_identical_mappings(self):
        self.client.send_snapshot(
            node_id="node-1", soil_index_pct=42.5, temperature_c=28.5,
            air_humidity_pct=65, plant_status="Healthy", predicted_moisture_pct=40.5,
            pump_is_on=True,
        )
        snapshot = self.session.get.call_args.kwargs["params"]
        self.assertEqual(self.session.get.call_count, 1)
        self.session.get.reset_mock()
        self.client.send_telemetry(node_id="node-1", soil_index_pct=42.5,
                                   temperature_c=28.5, air_humidity_pct=65)
        self.client.send_ai_results(node_id="node-1", plant_status="Healthy",
                                   predicted_moisture_pct=40.5)
        self.client.report_pump_status(node_id="node-1", is_on=True)
        combined = {}
        for call in self.session.get.call_args_list:
            combined.update(call.kwargs["params"])
        self.assertEqual(snapshot, combined)
        self.assertNotIn("v3", combined)

    def test_bound_node_is_required_and_validated_for_every_upload(self):
        for operation, kwargs in (
            (self.client.send_snapshot, {}), (self.client.send_telemetry, {}),
            (self.client.send_ai_results, {}), (self.client.report_pump_status, {"is_on": False}),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(ValueError):
                    operation(node_id="other-node", **kwargs)
                with self.assertRaises(TypeError):
                    operation(**kwargs)
        self.session.get.assert_not_called()

    def test_empty_node_ids_are_rejected_before_creating_resources(self):
        for node_id in (None, "", " ", 123):
            with self.subTest(node_id=node_id), self.assertRaises(ValueError):
                CloudClient(self.config, node_id=node_id)

    def test_construction_and_imports_do_not_connect(self):
        import cloud_edge
        importlib.reload(cloud_edge)
        self.network.connect_async.assert_not_called()
        self.network.loop_start.assert_not_called()
        self.session.get.assert_not_called()

    def test_lifecycle_contract_and_upload_independent_of_mqtt(self):
        self.assertFalse(self.client.commands_ready)
        self.assertIsNone(self.client.last_command_error)
        self.client.send_telemetry(node_id="node-1", soil_index_pct=42)
        self.client.start()
        self.client.start()
        self.network.loop_start.assert_called_once()
        self.client.close()
        self.client.close()
        self.session.close.assert_called_once()
        self.assertIsNone(self.client.poll_command())
        self.assertFalse(self.client.commands_ready)
        with self.assertRaises(CloudError):
            self.client.start()
        with self.assertRaises(CloudError):
            self.client.send_snapshot(node_id="node-1")

    def test_incoming_command_never_uploads_pump_status(self):
        self.client.start()
        self.network.on_connect(self.network, None, None, ReasonCode(PacketTypes.CONNACK, identifier=0), None)
        self.network.on_subscribe(self.network, None, 7, [ReasonCode(PacketTypes.SUBACK, identifier=1)] * 2, None)
        message = mqtt.MQTTMessage(topic=BlynkTopics.PUMP_COMMAND.encode())
        message.payload = b"1"
        self.network.on_message(self.network, None, message)
        command = self.client.poll_command()
        self.assertIsInstance(command, PumpCommand)
        self.assertTrue(command.requested_on)
        self.assertTrue(self.client.commands_ready)
        self.session.get.assert_not_called()

    def test_mode_and_detail_upload_only_to_bound_datastreams(self):
        self.client.report_control_mode(node_id="node-1", enabled=False)
        self.assertEqual(self.session.get.call_args.kwargs["params"],
                         {"token": "test-secret", "v17": 0})
        self.client.report_pump_detail(node_id="node-1", detail="unknown: awaiting device state")
        self.assertEqual(self.session.get.call_args.kwargs["params"],
                         {"token": "test-secret", "v18": "unknown: awaiting device state"})
        with self.assertRaises(ValueError):
            self.client.report_control_mode(node_id="node-1", enabled=1)
        with self.assertRaises(ValueError):
            self.client.report_pump_detail(node_id="other", detail="confirmed: off")

    def test_catalog_upload_uses_v19_and_bound_node(self):
        payload = '{"v":1,"nodes":[]}'
        self.client.report_node_catalog(node_id="node-1", catalog=payload)
        self.assertEqual(self.session.get.call_args.kwargs["params"],
                         {"token": "test-secret", "v19": payload})
        with self.assertRaises(ValueError):
            self.client.report_node_catalog(node_id="other", catalog=payload)

    def test_own_mode_write_does_not_cancel_a_manual_start(self):
        self.client.start()
        self.network.on_connect(self.network, None, None,
                                ReasonCode(PacketTypes.CONNACK, identifier=0), None)
        self.network.on_subscribe(self.network, None, 7,
                                  [ReasonCode(PacketTypes.SUBACK, identifier=1)] * 2, None)
        self.client.report_control_mode(node_id="node-1", enabled=False)
        echo = mqtt.MQTTMessage(topic=BlynkTopics.AUTO_IRRIGATION_ENABLED.encode())
        echo.payload = b"0"
        self.network.on_message(self.network, None, echo)
        self.assertIsNone(self.client.poll_command())
        opposite = mqtt.MQTTMessage(topic=BlynkTopics.AUTO_IRRIGATION_ENABLED.encode())
        opposite.payload = b"1"
        self.network.on_message(self.network, None, opposite)
        self.assertEqual(self.client.poll_command().control, "auto")

    def test_close_waits_for_inflight_http_then_releases_resources(self):
        entered, release = threading.Event(), threading.Event()
        response = self.session.get.return_value

        def upload(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=3):
                raise AssertionError("Upload was never released")
            return response

        self.session.get.side_effect = upload
        with ThreadPoolExecutor(max_workers=2) as executor:
            sending = executor.submit(self.client.send_telemetry, node_id="node-1", soil_index_pct=42)
            try:
                self.assertTrue(entered.wait(timeout=3))
                closing = executor.submit(self.client.close)
            finally:
                release.set()
            sending.result(timeout=3)
            closing.result(timeout=3)
        self.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
