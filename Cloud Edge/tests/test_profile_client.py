import json
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
import requests

from cloud_edge import CloudClient, CloudConfig
from cloud_edge.datastreams import BlynkTopics
from tests.test_mqtt import reason
from tests.test_profiles import draft
from tests.test_profile_sync import FakeBackend, wait_until


class ProfileClientTests(unittest.TestCase):
    def setUp(self):
        self.network = Mock(spec=mqtt.Client)
        self.network.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 7)
        self.network.loop_start.return_value = mqtt.MQTT_ERR_SUCCESS
        self.session = Mock(spec=requests.Session)
        self.writes = []

        def request(url, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response._content_consumed = True
            response._content = json.dumps(draft()).encode() if url.endswith("/get") else b""
            if url.endswith("/update"):
                self.writes.append(kwargs["params"])
            return response

        self.session.get.side_effect = request
        for target, replacement in (
            ("cloud_edge.blynk_mqtt.mqtt.Client", self.network),
            ("cloud_edge.blynk_http.requests.Session", self.session),
        ):
            patcher = patch(target, return_value=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = FakeBackend()
        self.client = CloudClient(CloudConfig("sgp1.blynk.cloud", "test-secret"),
                                  node_id="node-1", profile_backend=self.backend)
        self.addCleanup(self.client.close)

    def connect(self, codes=(1, 1, 1, 1)):
        self.client.start()
        self.network.on_connect(self.network, None, None, reason(PacketTypes.CONNACK), None)
        self.network.on_subscribe(self.network, None, 7,
                                  [reason(PacketTypes.SUBACK, code) for code in codes], None)

    def receive(self, topic, payload=b"1", retain=False):
        message = mqtt.MQTTMessage(topic=topic.encode())
        message.payload, message.retain = payload, retain
        self.network.on_message(self.network, None, message)

    def test_optional_worker_lifecycle_and_independent_pump_command(self):
        self.session.get.assert_not_called()
        self.assertFalse(self.client.profiles_ready)
        self.connect()
        wait_until(lambda: self.client.profiles_ready)
        topics = self.network.subscribe.call_args.args[0]
        self.assertEqual(topics, [(BlynkTopics.PUMP_COMMAND, 1),
                                 (BlynkTopics.AUTO_IRRIGATION_ENABLED, 1),
                                 (BlynkTopics.APPLY_PROFILE, 1), (BlynkTopics.PROFILE_PRESET, 1)])
        self.receive(BlynkTopics.APPLY_PROFILE)
        self.receive(BlynkTopics.PUMP_COMMAND)
        self.assertTrue(self.client.poll_command().requested_on)
        wait_until(lambda: "APPLIED" in self.client.last_profile_status)
        self.assertIsNone(self.client.last_profile_error)
        self.client.close()
        self.assertFalse(self.client.profiles_ready)
        count = len(self.backend.calls)
        self.receive(BlynkTopics.APPLY_PROFILE)
        self.assertEqual(len(self.backend.calls), count)
        self.network.loop_stop.assert_called_once()

    def test_retained_and_malformed_profile_events_are_ignored(self):
        self.connect()
        wait_until(lambda: self.client.profiles_ready)
        for payload in (b"true", b"2", b"1.0", b"\xff", b"", b"1" * 1000):
            self.receive(BlynkTopics.APPLY_PROFILE, payload)
        self.receive(BlynkTopics.APPLY_PROFILE, retain=True)
        self.receive(BlynkTopics.APPLY_PROFILE, b"0")
        self.assertFalse(self.backend.calls)
        self.assertIn("UNCONFIGURED", self.client.last_profile_status)

    def test_rejected_profile_subscription_prevents_restoration(self):
        self.connect((1, 128, 1, 1))
        self.assertFalse(self.client.profiles_ready)
        self.assertEqual(self.client.last_command_error.category, "subscription")
        self.session.get.assert_not_called()

    def test_disabled_clients_do_not_subscribe_to_or_write_profiles(self):
        self.client.close()
        client = CloudClient(CloudConfig("sgp1.blynk.cloud", "test-secret"), node_id="node-1")
        self.addCleanup(client.close)
        client.start()
        self.network.on_connect(self.network, None, None, reason(PacketTypes.CONNACK), None)
        self.assertEqual(self.network.subscribe.call_args.args[0], [(BlynkTopics.PUMP_COMMAND, 1),
                                                                    (BlynkTopics.AUTO_IRRIGATION_ENABLED, 1)])
        self.assertFalse(client.profiles_ready)
        self.assertIsNone(client.last_profile_error)
        self.assertEqual(client.last_profile_status, "")
        self.session.get.assert_not_called()
