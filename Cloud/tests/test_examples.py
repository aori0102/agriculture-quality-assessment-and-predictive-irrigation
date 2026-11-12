from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
import requests

from Cloud import CloudConfig
from Cloud.datastreams import BlynkTopics
from Cloud.examples import live_demo, smoke_http


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.response = requests.Response()
        self.response.status_code = 200
        self.response._content = b""
        self.response._content_consumed = True
        self.session.get.return_value = self.response
        self.network = Mock(spec=mqtt.Client)
        self.network.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 7)
        self.network.loop_start.return_value = mqtt.MQTT_ERR_SUCCESS
        for target, replacement in (
            ("Cloud.blynk_http.requests.Session", self.session),
            ("Cloud.blynk_mqtt.mqtt.Client", self.network),
            ("Cloud.config.CloudConfig.from_env", CloudConfig("sgp1.blynk.cloud", "test-secret")),
        ):
            patcher = patch(target, return_value=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_smoke_example_uploads_through_public_interface_and_closes(self):
        output = io.StringIO()
        with patch("sys.argv", ["smoke_http", "--node-id", "node-1"]), redirect_stdout(output):
            self.assertEqual(smoke_http.main(), 0)
        self.assertIn("successfully", output.getvalue())
        self.assertEqual(self.session.get.call_args.kwargs["params"],
                         {"token": "test-secret", "v0": 36, "v1": 36.5, "v6": 36})
        self.network.connect_async.assert_not_called()
        self.session.close.assert_called_once()

    def test_live_example_prints_intent_without_reporting_execution(self):
        def connect_and_receive():
            self.network.on_connect(self.network, None, None, ReasonCode(PacketTypes.CONNACK, identifier=0), None)
            self.network.on_subscribe(self.network, None, 7, [ReasonCode(PacketTypes.SUBACK, identifier=1)], None)
            message = mqtt.MQTTMessage(topic=BlynkTopics.PUMP_COMMAND.encode())
            message.payload = b"1"
            self.network.on_message(self.network, None, message)
            return mqtt.MQTT_ERR_SUCCESS

        self.network.loop_start.side_effect = connect_and_receive
        output = io.StringIO()
        with patch("sys.argv", ["live_demo", "--node-id", "node-1", "--duration", "2"]), \
                patch("Cloud.examples.live_demo.monotonic", side_effect=[0, 1, 2]), \
                patch("Cloud.examples.live_demo.sleep"), redirect_stdout(output):
            self.assertEqual(live_demo.main(), 0)
        self.assertIn("Request for node-1: ON", output.getvalue())
        self.assertIn("Command subscription ready", output.getvalue())
        self.session.get.assert_called_once()
        self.assertNotIn("v4", self.session.get.call_args.kwargs["params"])
        self.network.publish.assert_not_called()
        self.session.close.assert_called_once()
        self.network.loop_stop.assert_called_once()

    def test_smoke_failure_returns_nonzero_without_leaking_credentials(self):
        self.response.status_code = 401
        self.response.url = "https://sgp1.blynk.cloud/?token=test-secret"
        output = io.StringIO()
        with patch("sys.argv", ["smoke_http", "--node-id", "node-1"]), redirect_stderr(output):
            self.assertEqual(smoke_http.main(), 1)
        self.assertIn("401", output.getvalue())
        self.assertNotIn("test-secret", output.getvalue())
        self.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
